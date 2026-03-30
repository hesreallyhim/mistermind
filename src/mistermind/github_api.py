"""
GitHub REST API wrapper with per-session call counting and rate-limit
awareness.

Provides the GitHubAPI class (thin wrapper around urllib), the
SecondaryRateLimitError exception, and board-asset lifecycle helpers
(ensure_asset_branch, upload_board_svg, cleanup_board_svg).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mistermind.constants import (
    BOARD_ASSET_BRANCH,
    BOARD_ASSET_DIR,
    RATE_LOW_WATERMARK,
)


class SecondaryRateLimitError(Exception):
    """Raised when GitHub returns a 403/429 indicating a secondary
    (abuse-detection) rate limit.  The job must stop immediately."""

    def __init__(self, reset_at: int, message: str = "") -> None:
        self.reset_at = reset_at
        super().__init__(message or f"Secondary rate limit hit. Resets at {reset_at}.")


class GitHubAPI:
    """Thin GitHub REST API wrapper with per-session call counting."""

    def __init__(self, repository: str, token: str) -> None:
        self.repository = repository
        self.base = f"https://api.github.com/repos/{repository}"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mistermind-engine",
        }
        # Per-session call counter
        self.calls: dict[str, int] = {}
        # Remaining budget (updated from response headers, best-effort)
        self.remaining: int | None = None
        self.reset_at: int = 0

    def _count(self, method: str) -> None:
        self.calls[method] = self.calls.get(method, 0) + 1

    def _read_rate_headers(
        self,
        headers: Any,
        *,
        method: str,
        status: int,
        url: str,
    ) -> None:
        """Capture and dump GitHub rate-limit headers for this response."""
        rem = headers.get("x-ratelimit-remaining", "")
        if rem.isdigit():
            self.remaining = int(rem)
        rst = headers.get("x-ratelimit-reset", "")
        if rst.isdigit():
            self.reset_at = int(rst)
        limit = headers.get("x-ratelimit-limit", "")
        used = headers.get("x-ratelimit-used", "")
        resource = headers.get("x-ratelimit-resource", "")
        if any((limit, rem, used, rst, resource)):
            print(
                "RATE_HEADERS"
                f" method={method}"
                f" status={status}"
                f" url={url}"
                f" x-ratelimit-limit={limit or '-'}"
                f" x-ratelimit-remaining={rem or '-'}"
                f" x-ratelimit-used={used or '-'}"
                f" x-ratelimit-reset={rst or '-'}"
                f" x-ratelimit-resource={resource or '-'}"
            )

    @property
    def budget_low(self) -> bool:
        """True when remaining is known and below RATE_LOW_WATERMARK."""
        return self.remaining is not None and self.remaining < RATE_LOW_WATERMARK

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def call_summary(self) -> str:
        """One-line summary, e.g. 'GET:5 POST:2 PUT:2 (total 9)'."""
        parts = " ".join(f"{m}:{c}" for m, c in sorted(self.calls.items()))
        budget = ""
        if self.remaining is not None:
            budget = f" | remaining: {self.remaining}"
        return f"{parts} (total {self.total_calls}){budget}"

    def poll_rate_limit(self) -> dict[str, Any] | None:
        """GET /rate_limit -- repo-wide snapshot on demand."""
        try:
            url = "https://api.github.com/rate_limit"
            self._count("GET")
            req = urllib.request.Request(url=url, method="GET", headers=self.headers)
            with urllib.request.urlopen(req) as resp:
                self._read_rate_headers(resp.headers, method="GET", status=resp.status, url=url)
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except Exception as exc:
            print(f"Failed to poll rate limit: {exc}")
            return None

    @staticmethod
    def _check_secondary_limit(exc: urllib.error.HTTPError) -> None:
        """If the error is a secondary (abuse-detection) rate limit,
        raise SecondaryRateLimitError to kill the job immediately."""
        if exc.code not in (403, 429):
            return
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if "secondary" in body.lower():
            reset_at = 0
            retry_after = exc.headers.get("retry-after", "")
            if retry_after.isdigit():
                reset_at = int(dt.datetime.now(dt.UTC).timestamp()) + int(retry_after)
            else:
                reset_str = exc.headers.get("x-ratelimit-reset", "")
                if reset_str.isdigit():
                    reset_at = int(reset_str)
            raise SecondaryRateLimitError(
                reset_at=reset_at,
                message=f"Secondary rate limit: {body[:200]}",
            ) from exc

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        body = None
        headers = dict(self.headers)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        self._count(method)
        req = urllib.request.Request(url=url, method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(req) as resp:
                self._read_rate_headers(resp.headers, method=method, status=resp.status, url=url)
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            self._read_rate_headers(exc.headers, method=method, status=exc.code, url=url)
            self._check_secondary_limit(exc)
            raise

    def _request_allow_404(self, method: str, path: str) -> tuple[int, Any]:
        url = path if path.startswith("http") else f"{self.base}{path}"
        self._count(method)
        req = urllib.request.Request(url=url, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req) as resp:
                self._read_rate_headers(resp.headers, method=method, status=resp.status, url=url)
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            self._read_rate_headers(exc.headers, method=method, status=exc.code, url=url)
            self._check_secondary_limit(exc)  # kill switch first
            if exc.code == 404:
                return 404, None
            raise

    def list_issue_comments(self, issue_number: int) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            chunk = (
                self._request(
                    "GET",
                    f"/issues/{issue_number}/comments?per_page=100&page={page}",
                )
                or []
            )
            if not isinstance(chunk, list):
                break
            comments.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
        return comments

    def get_issue(self, issue_number: int) -> dict[str, Any]:
        """Fetch a single issue payload."""
        return self._request("GET", f"/issues/{issue_number}")  # type: ignore[no-any-return]

    def create_issue_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        return self._request(  # type: ignore[no-any-return]
            "POST",
            f"/issues/{issue_number}/comments",
            {"body": body},
        )

    def update_issue_comment(self, comment_id: int, body: str) -> dict[str, Any]:
        return self._request(  # type: ignore[no-any-return]
            "PATCH",
            f"/issues/comments/{comment_id}",
            {"body": body},
        )

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        if not labels:
            return
        self._request(
            "POST",
            f"/issues/{issue_number}/labels",
            {"labels": labels},
        )

    def set_repo_interaction_limit(
        self,
        *,
        limit: str = "collaborators_only",
        expiry: str = "one_day",
    ) -> dict[str, Any]:
        """Apply a repository-wide interaction limit."""
        return self._request(  # type: ignore[no-any-return]
            "PUT",
            "/interaction-limits",
            {
                "limit": limit,
                "expiry": expiry,
            },
        )

    def list_open_issues_with_label(self, label: str) -> list[dict[str, Any]]:
        """List open issues with a specific label."""
        issues: list[dict[str, Any]] = []
        page = 1
        encoded = urllib.parse.quote(label, safe="")
        while True:
            chunk = (
                self._request(
                    "GET",
                    f"/issues?state=open&labels={encoded}&per_page=100&page={page}",
                )
                or []
            )
            if not isinstance(chunk, list):
                break
            issues.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
        return issues

    def remove_label(self, issue_number: int, label: str) -> None:
        encoded = urllib.parse.quote(label, safe="")
        status, _ = self._request_allow_404("DELETE", f"/issues/{issue_number}/labels/{encoded}")
        if status not in (200, 204, 404):
            raise RuntimeError(f"Unexpected status removing label {label}: {status}")

    def close_issue(self, issue_number: int) -> dict[str, Any]:
        """Close an issue via PATCH /issues/{issue_number}."""
        return self._request(  # type: ignore[no-any-return]
            "PATCH",
            f"/issues/{issue_number}",
            {"state": "closed"},
        )

    def lock_issue(self, issue_number: int, *, reason: str = "resolved") -> None:
        """Lock an issue conversation via PUT /issues/{issue_number}/lock."""
        payload: dict[str, Any] = {}
        if reason:
            payload["lock_reason"] = reason
        self._request(
            "PUT",
            f"/issues/{issue_number}/lock",
            payload if payload else None,
        )

    # ── Contents API (for board asset hosting) ────────────────────────

    def get_file_contents(
        self,
        path: str,
        *,
        ref: str | None = None,
    ) -> dict[str, Any] | None:
        """GET /repos/{owner}/{repo}/contents/{path}?ref=...  Returns None on 404."""
        qs = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
        status, data = self._request_allow_404(
            "GET",
            f"/contents/{urllib.parse.quote(path, safe='/')}{qs}",
        )
        if status == 404:
            return None
        return data  # type: ignore[no-any-return]

    def create_or_update_file(
        self,
        path: str,
        *,
        content_b64: str,
        message: str,
        branch: str,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """PUT /repos/{owner}/{repo}/contents/{path}  — create or update."""
        payload: dict[str, Any] = {
            "message": message,
            "content": content_b64,
            "branch": branch,
        }
        if sha is not None:
            payload["sha"] = sha
        return self._request(  # type: ignore[no-any-return]
            "PUT",
            f"/contents/{urllib.parse.quote(path, safe='/')}",
            payload,
        )

    def delete_file(
        self,
        path: str,
        *,
        sha: str,
        message: str,
        branch: str,
    ) -> dict[str, Any] | None:
        """DELETE /repos/{owner}/{repo}/contents/{path}"""
        payload: dict[str, Any] = {
            "message": message,
            "sha": sha,
            "branch": branch,
        }
        url = f"{self.base}/contents/{urllib.parse.quote(path, safe='/')}"
        body = json.dumps(payload).encode("utf-8")
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url=url,
            method="DELETE",
            headers=headers,
            data=body,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                self._read_rate_headers(resp.headers, method="DELETE", status=resp.status, url=url)
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            self._read_rate_headers(exc.headers, method="DELETE", status=exc.code, url=url)
            if exc.code == 404:
                return None
            raise

    def get_ref(self, ref: str) -> dict[str, Any] | None:
        """GET /repos/{owner}/{repo}/git/ref/{ref}  — returns None on 404."""
        status, data = self._request_allow_404(
            "GET",
            f"/git/ref/{urllib.parse.quote(ref, safe='/')}",
        )
        return data if status != 404 else None

    def create_ref(self, ref: str, sha: str) -> dict[str, Any]:
        """POST /repos/{owner}/{repo}/git/refs"""
        return self._request("POST", "/git/refs", {"ref": ref, "sha": sha})  # type: ignore[no-any-return]

    def list_directory_contents(
        self,
        path: str,
        *,
        ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """List files in a directory via Contents API. Returns [] on 404."""
        qs = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
        status, data = self._request_allow_404(
            "GET",
            f"/contents/{urllib.parse.quote(path, safe='/')}{qs}",
        )
        if status == 404 or not isinstance(data, list):
            return []
        return data


# ── Board asset lifecycle ────────────────────────────────────────────


def board_asset_path(issue_number: int, *, seq: int | None = None) -> str:
    """Return the repo-relative path for a board SVG asset.

    When ``seq`` is provided, the path is an immutable per-turn snapshot.
    """
    if seq is None:
        return f"{BOARD_ASSET_DIR}/{issue_number}.svg"
    return f"{BOARD_ASSET_DIR}/{issue_number}-{seq:03d}.svg"


def board_raw_url(
    repo: str,
    issue_number: int,
    *,
    seq: int | None = None,
    nonce: str | int | None = None,
) -> str:
    """Build a relative URL for a board SVG on the game-boards branch."""
    path = board_asset_path(issue_number, seq=seq)
    url = f"../blob/{BOARD_ASSET_BRANCH}/{path}?raw=1"
    if nonce is not None:
        url += f"&v={nonce}"
    return url


def ensure_asset_branch(api: GitHubAPI) -> None:
    """Create the game-boards branch if it doesn't exist yet."""
    ref = api.get_ref(f"heads/{BOARD_ASSET_BRANCH}")
    if ref is not None:
        return
    # Branch off the default branch HEAD
    main_ref = api.get_ref("heads/main")
    if main_ref is None:
        main_ref = api.get_ref("heads/master")
    if main_ref is None:
        print(f"Cannot find main/master branch to create {BOARD_ASSET_BRANCH}.")
        return
    sha = main_ref["object"]["sha"]
    api.create_ref(f"refs/heads/{BOARD_ASSET_BRANCH}", sha)
    print(f"Created branch {BOARD_ASSET_BRANCH} at {sha[:8]}.")


def upload_board_svg(
    api: GitHubAPI,
    *,
    repo: str,
    issue_number: int,
    seq: int,
    svg_content: str,
) -> str | None:
    """Upload (create or update) a board SVG to the asset branch.

    Returns the raw URL on success, or None on failure.
    """
    path = board_asset_path(issue_number, seq=seq)
    content_b64 = base64.b64encode(svg_content.encode("utf-8")).decode("ascii")
    max_attempts = 3

    for attempt_no in range(1, max_attempts + 1):
        existing = api.get_file_contents(path, ref=BOARD_ASSET_BRANCH)
        sha = existing["sha"] if existing else None
        attempt = "update" if sha else "create"

        try:
            api.create_or_update_file(
                path,
                content_b64=content_b64,
                message=f"board: issue {issue_number} turn {seq}",
                branch=BOARD_ASSET_BRANCH,
                sha=sha,
            )
            url = board_raw_url(repo, issue_number, seq=seq)
            print(f"Board SVG {attempt}: {path}")
            return url
        except urllib.error.HTTPError as exc:
            if exc.code == 409 and attempt_no < max_attempts:
                print(
                    f"Board SVG {attempt} conflict on {path} "
                    f"(attempt {attempt_no}/{max_attempts}); retrying."
                )
                time.sleep(0.2 * attempt_no)
                continue
            print(f"Failed to {attempt} board SVG: {exc}")
            return None
        except Exception as exc:
            print(f"Failed to {attempt} board SVG: {exc}")
            return None

    return None


def cleanup_board_svg(
    api: GitHubAPI,
    issue_number: int,
) -> None:
    """Delete board SVG snapshots for a finished game, if requested."""
    prefix = f"{issue_number}-"
    try:
        entries = api.list_directory_contents(BOARD_ASSET_DIR, ref=BOARD_ASSET_BRANCH)
    except Exception as exc:
        print(f"Failed to list board assets for cleanup: {exc}")
        return

    targets = []
    legacy_path = board_asset_path(issue_number)
    for entry in entries:
        path = entry.get("path")
        name = entry.get("name")
        sha = entry.get("sha")
        if not isinstance(path, str) or not isinstance(name, str) or not isinstance(sha, str):
            continue
        if path == legacy_path or (name.startswith(prefix) and name.endswith(".svg")):
            targets.append((path, sha))

    for path, sha in targets:
        try:
            api.delete_file(
                path,
                sha=sha,
                message=f"cleanup: issue {issue_number} game over",
                branch=BOARD_ASSET_BRANCH,
            )
            print(f"Cleaned up board asset: {path}")
        except Exception as exc:
            print(f"Failed to clean up {path}: {exc}")
