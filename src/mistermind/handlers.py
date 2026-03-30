"""
Orchestration handlers and the CLI entry point.

Top-level event handlers for GitHub Actions (issue opened, issue comment),
routing predicates, comment search helpers, and the main() function that
reads environment variables and dispatches to the appropriate handler.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Any

from mistermind.conduct import (
    apply_conduct_input,
    load_moderation_policy,
    render_conduct_state_comment,
    sync_conduct_labels,
)
from mistermind.constants import (
    DEFAULT_POLICY_PATH,
    GAME_TIMEOUT_MINUTES,
    PHASE_LABEL,
    TERMINAL_CLOSE_GRACE_MINUTES,
    TERMINAL_PHASES,
)
from mistermind.github_api import (
    GitHubAPI,
    SecondaryRateLimitError,
    ensure_asset_branch,
    upload_board_svg,
)
from mistermind.parsing import (
    board_template_path_for_state,
    parse_board_theme,
    parse_command,
    parse_room_variant,
    room_hints_enabled,
)
from mistermind.rendering import game_rules_text, render_comment
from mistermind.scoring import (
    compute_deductive_hint_summary,
    render_deductive_hints_markdown,
)
from mistermind.state import (
    apply_command_to_state,
    apply_owner_guardrail_to_state,
    apply_perfectionist_gate,
    build_initial_conduct_state,
    build_initial_state,
    conduct_state_is_valid,
    decode_conduct_token,
    decode_state_token,
    derive_signing_secret,
    derive_solution,
    encode_conduct_token,
    encode_state_token,
    extract_conduct_token,
    extract_state_token,
    is_valid_state_transition,
    state_is_valid,
)
from mistermind.svg import hydrate_board_template, render_svg_board
from mistermind.utils import issue_room_key, parse_iso_utc

# ── Routing predicates ───────────────────────────────────────────────


def issue_has_label(issue_payload: dict[str, Any], label_name: str) -> bool:
    labels = issue_payload.get("labels") or []
    for label in labels:
        if isinstance(label, str) and label == label_name:
            return True
        if isinstance(label, dict) and label.get("name") == label_name:
            return True
    return False


def should_process_issue_open(event_payload: dict[str, Any]) -> bool:
    if event_payload.get("action") != "opened":
        return False
    issue = event_payload.get("issue") or {}
    return issue_has_label(issue, "game:mistermind")


def should_process_issue_comment(event_payload: dict[str, Any]) -> bool:
    if event_payload.get("action") != "created":
        return False
    issue = event_payload.get("issue") or {}
    if issue.get("pull_request"):
        return False
    if not issue_has_label(issue, "game:mistermind"):
        return False

    issue_owner = ((issue.get("user") or {}).get("login") or "").strip().lower()
    commenter = (
        (((event_payload.get("comment") or {}).get("user") or {}).get("login") or "")
        .strip()
        .lower()
    )
    if not issue_owner or commenter != issue_owner:
        return False

    comment_body = (event_payload.get("comment") or {}).get("body", "")
    parsed = parse_command(comment_body if isinstance(comment_body, str) else "")
    return parsed["kind"] in {"guess", "status", "help", "giveup"}


# ── Comment search helpers ───────────────────────────────────────────


def find_latest_state_from_comments(
    comments: list[dict[str, Any]],
    *,
    signing_secret: str,
    room_key: str,
    owner: str,
    issue_number: int,
) -> tuple[dict[str, Any] | None, int | None]:
    automation_logins = _automation_logins()
    for comment in reversed(comments):
        author = ((comment.get("user") or {}).get("login") or "").strip().lower()
        if author not in automation_logins:
            continue

        token = extract_state_token(comment.get("body", ""))
        if not token:
            continue

        decoded = decode_state_token(token, signing_secret)
        if not decoded:
            continue

        if not state_is_valid(decoded, room_key=room_key, owner=owner, issue_number=issue_number):
            continue

        comment_id = comment.get("id")
        if not isinstance(comment_id, int):
            continue
        return decoded, comment_id
    return None, None


def find_latest_conduct_state_from_comments(
    comments: list[dict[str, Any]],
    *,
    signing_secret: str,
    room_key: str,
    owner: str,
    issue_number: int,
) -> tuple[dict[str, Any] | None, int | None]:
    automation_logins = _automation_logins()
    for comment in reversed(comments):
        author = ((comment.get("user") or {}).get("login") or "").strip().lower()
        if author not in automation_logins:
            continue

        token = extract_conduct_token(comment.get("body", ""))
        if not token:
            continue

        decoded = decode_conduct_token(token, signing_secret)
        if not decoded:
            continue

        if not conduct_state_is_valid(
            decoded, room_key=room_key, owner=owner, issue_number=issue_number
        ):
            continue

        comment_id = comment.get("id")
        if not isinstance(comment_id, int):
            continue
        return decoded, comment_id
    return None, None


def find_latest_game_state_comment_id(
    comments: list[dict[str, Any]],
    *,
    signing_secret: str,
    room_key: str,
    owner: str,
    issue_number: int,
) -> int | None:
    _, comment_id = find_latest_state_from_comments(
        comments,
        signing_secret=signing_secret,
        room_key=room_key,
        owner=owner,
        issue_number=issue_number,
    )
    return comment_id


def _automation_logins() -> set[str]:
    """Return the set of author logins treated as engine automation."""
    raw = os.environ.get("MM_AUTOMATION_LOGIN", "")
    logins = {"github-actions[bot]"}
    for chunk in raw.split(","):
        login = chunk.strip().lower()
        if login and login != "0":
            logins.add(login)
    return logins


def owner_has_prior_unanswered_command(
    comments: list[dict[str, Any]],
    *,
    owner: str,
    current_comment_id: int,
    last_game_state_comment_id: int | None,
) -> bool:
    if last_game_state_comment_id is None:
        return False

    for comment in comments:
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid <= last_game_state_comment_id or cid >= current_comment_id:
            continue
        author = ((comment.get("user") or {}).get("login") or "").strip().lower()
        if author != owner.lower():
            continue
        parsed = parse_command(comment.get("body", ""))
        if parsed["kind"] in {"guess", "status", "help", "giveup"}:
            return True
    return False


# ── Label synchronization ────────────────────────────────────────────


def sync_room_labels(api: GitHubAPI, issue_number: int, phase: str) -> None:
    target = PHASE_LABEL.get(phase, "mm:active")
    api.add_labels(issue_number, ["game:mistermind", target])
    for label in {"mm:active", "mm:won", "mm:lost"} - {target}:
        api.remove_label(issue_number, label)


# Delegate conduct labels to the conduct module
sync_conduct_labels = sync_conduct_labels  # re-export


# ── Board upload + render ────────────────────────────────────────────


def _upload_board_and_render(
    *,
    api: GitHubAPI,
    repo: str,
    issue_number: int,
    state: dict[str, Any],
    headline: str,
    token: str,
    reveal_solution: bool,
    solution: list[str],
    hint_block: str | None = None,
    hint_overlay: dict[str, Any] | None = None,
) -> str:
    """Generate the SVG board, upload it to the asset branch, and return
    the rendered comment body with the image embedded."""
    # Generate SVG from template
    template_path = board_template_path_for_state(state)
    try:
        svg = hydrate_board_template(
            state,
            reveal_solution=reveal_solution,
            solution=solution,
            hint_overlay=hint_overlay,
            template_path=template_path,
        )
    except Exception as exc:
        print(f"Template hydration failed, falling back to programmatic SVG: {exc}")
        svg = render_svg_board(
            state,
            reveal_solution=reveal_solution,
            solution=solution if reveal_solution else None,
            hint_overlay=hint_overlay,
        )

    # Upload an immutable board snapshot for the current turn.
    board_url: str | None = None
    seq = int(state.get("seq", 0))
    try:
        ensure_asset_branch(api)
        board_url = upload_board_svg(
            api,
            repo=repo,
            issue_number=issue_number,
            seq=seq,
            svg_content=svg,
        )
    except Exception as exc:
        print(f"Board asset upload failed (non-fatal): {exc}")

    return render_comment(
        headline=headline,
        state=state,
        token=token,
        reveal_solution=reveal_solution,
        solution=solution,
        board_image_url=board_url,
        hint_block=hint_block,
    )


# ── Post-game hook ───────────────────────────────────────────────────


def _on_game_terminal(
    issue_number: int,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Post-game hook: persist terminal game record for branch-based stats."""
    phase = state.get("phase", "active")
    if phase not in TERMINAL_PHASES:
        return None

    output_path = os.environ.get("MM_TERMINAL_RECORD_PATH", "").strip()
    if not output_path:
        return None

    owner = str(state.get("owner", "")).strip()
    if not owner:
        print("Terminal record skipped: missing owner.")
        return None

    variant = str(state.get("variant", "classic")).strip().lower() or "classic"
    attempt = int(state.get("attempt", 0))
    result = "won" if phase == "won" else "lost"
    completed_at = str(state.get("updated_at", "")).strip() or dt.datetime.now(dt.UTC).isoformat()
    record: dict[str, Any] = {
        "schema": "mistermind-game-result-v1",
        "issue": issue_number,
        "player": owner,
        "variant": variant,
        "result": result,
        "attempts": attempt,
        "completed_at": completed_at,
    }

    try:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=False)
            handle.write("\n")
        print(f"Wrote terminal game record: {output_path}")
        return record
    except Exception as exc:
        print(f"Terminal game record write failed (non-fatal): {exc}")
        return None


def _lock_on_terminal_transition(
    *,
    api: GitHubAPI,
    issue_number: int,
    previous_phase: str,
    phase: str,
) -> None:
    """Lock issue once when game first transitions to terminal."""
    if previous_phase in TERMINAL_PHASES or phase not in TERMINAL_PHASES:
        return

    try:
        api.lock_issue(issue_number, reason="resolved")
    except Exception as exc:
        print(f"Issue lock failed (non-fatal): {exc}")


def _is_room_timed_out(
    state: dict[str, Any],
    *,
    now: dt.datetime | None = None,
    timeout_minutes: int = GAME_TIMEOUT_MINUTES,
) -> bool:
    """Return True when an active room exceeded its total lifetime budget."""
    if state.get("phase") in TERMINAL_PHASES:
        return False
    if timeout_minutes <= 0:
        return False

    created_at = parse_iso_utc(state.get("created_at"))
    if created_at is None:
        return False
    current = now or dt.datetime.now(dt.UTC)
    deadline = created_at + dt.timedelta(minutes=timeout_minutes)
    return current >= deadline


def _issue_is_terminal(issue: dict[str, Any]) -> bool:
    return issue_has_label(issue, "mm:won") or issue_has_label(issue, "mm:lost")


def handle_terminal_room_sweep(
    *,
    api: GitHubAPI,
    now: dt.datetime | None = None,
    close_after_minutes: int = TERMINAL_CLOSE_GRACE_MINUTES,
    active_timeout_minutes: int = GAME_TIMEOUT_MINUTES,
) -> list[int]:
    """Timeout active rooms, then close locked terminal rooms after grace expires."""
    if close_after_minutes <= 0 and active_timeout_minutes <= 0:
        return []

    current = now or dt.datetime.now(dt.UTC)
    closed: list[int] = []
    issues = api.list_open_issues_with_label("game:mistermind")
    for issue in issues:
        issue_number = issue.get("number")
        if not isinstance(issue_number, int):
            continue

        if not _issue_is_terminal(issue):
            if active_timeout_minutes <= 0:
                continue
            if not issue_has_label(issue, "mm:active"):
                continue

            created_raw = issue.get("created_at")
            created_at = parse_iso_utc(created_raw if isinstance(created_raw, str) else None)
            if created_at is None:
                continue
            active_age = current - created_at
            if active_age < dt.timedelta(minutes=active_timeout_minutes):
                continue

            try:
                sync_room_labels(api, issue_number, phase="lost")
                api.lock_issue(issue_number, reason="resolved")
                print(
                    f"Sweep timed out active room #{issue_number} "
                    f"(age={active_age}, timeout={active_timeout_minutes}m)."
                )
            except Exception as exc:
                print(f"Sweep failed to timeout active room #{issue_number}: {exc}")
            continue

        if not bool(issue.get("locked", False)):
            continue

        updated_raw = issue.get("updated_at")
        updated_at = parse_iso_utc(updated_raw if isinstance(updated_raw, str) else None)
        if updated_at is None:
            continue
        age = current - updated_at
        if age < dt.timedelta(minutes=close_after_minutes):
            continue

        try:
            api.close_issue(issue_number)
            closed.append(issue_number)
            print(f"Sweep closed terminal room #{issue_number} (age={age}).")
        except Exception as exc:
            print(f"Sweep failed to close issue #{issue_number}: {exc}")

    return closed


# ── Issue-opened handler ─────────────────────────────────────────────


def handle_issue_opened(
    *,
    api: GitHubAPI,
    repo: str,
    payload: dict[str, Any],
    signing_secret: str,
    solution_salt: str,
) -> None:
    issue = payload.get("issue") or {}
    issue_number = int(issue["number"])
    owner = (issue.get("user") or {}).get("login") or ""
    issue_body = issue.get("body", "")
    variant = parse_room_variant(issue_body if isinstance(issue_body, str) else "")
    board_theme = parse_board_theme(issue_body if isinstance(issue_body, str) else "")

    state = build_initial_state(
        repo,
        issue_number,
        owner,
        variant=variant,
        board_theme=board_theme,
    )
    room_key = state["room_key"]
    solution = derive_solution(room_key, solution_salt)
    token = encode_state_token(state, signing_secret)

    if variant == "hint":
        intro = "The code has been set. Hint mode is enabled."
    elif variant == "perfectionist":
        intro = "The code has been set. Perfectionist mode is enabled."
    else:
        intro = "The code has been set. Good luck!"
    created_at = parse_iso_utc(str(state.get("created_at", "")))
    if created_at is None:
        intro += f" Room timeout: {GAME_TIMEOUT_MINUTES} minutes from room creation."
    else:
        deadline = created_at + dt.timedelta(minutes=GAME_TIMEOUT_MINUTES)
        deadline_text = deadline.strftime("%Y-%m-%d %H:%M UTC")
        intro += f" Room timeout at `{deadline_text}` if unfinished."
    body = _upload_board_and_render(
        api=api,
        repo=repo,
        issue_number=issue_number,
        state=state,
        headline=intro,
        token=token,
        reveal_solution=False,
        solution=solution,
    )
    # Prepend the full rules to the bot's opening comment
    body = game_rules_text(variant=variant) + "\n\n---\n\n" + body
    api.create_issue_comment(issue_number, body)
    sync_room_labels(api, issue_number, phase="active")


# ── Issue-comment handler ────────────────────────────────────────────


def handle_issue_comment(
    *,
    api: GitHubAPI,
    repo: str,
    payload: dict[str, Any],
    signing_secret: str,
    solution_salt: str,
    moderation_policy: dict[str, Any],
) -> None:
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    issue_number = int(issue["number"])
    owner = (issue.get("user") or {}).get("login") or ""
    comment_id = int(comment["id"])
    command = parse_command(comment.get("body", ""))
    if command["kind"] == "ignore":
        print("Comment is not a supported command. Skipping.")
        return

    room_key = issue_room_key(repo, issue_number)
    comments = api.list_issue_comments(issue_number)
    current_state, _ = find_latest_state_from_comments(
        comments,
        signing_secret=signing_secret,
        room_key=room_key,
        owner=owner,
        issue_number=issue_number,
    )
    if current_state is None:
        print("No valid state envelope found. Initializing room state from scratch.")
        issue_body = issue.get("body", "")
        fallback_variant = parse_room_variant(issue_body if isinstance(issue_body, str) else "")
        fallback_board_theme = parse_board_theme(issue_body if isinstance(issue_body, str) else "")
        current_state = build_initial_state(
            repo,
            issue_number,
            owner,
            variant=fallback_variant,
            board_theme=fallback_board_theme,
        )

    solution = derive_solution(room_key, solution_salt)
    if _is_room_timed_out(current_state):
        timeout_state, should_emit = apply_owner_guardrail_to_state(
            previous=current_state,
            comment_id=comment_id,
            action="timeout",
        )
        if not should_emit:
            print("Duplicate timeout command. Skipping.")
            return
        timeout_state["phase"] = "lost"
        timeout_state["last_action"] = "timeout"
        token = encode_state_token(timeout_state, signing_secret)
        body = _upload_board_and_render(
            api=api,
            repo=repo,
            issue_number=issue_number,
            state=timeout_state,
            headline=(
                f"Room timed out after {GAME_TIMEOUT_MINUTES} minutes and is now marked as lost."
            ),
            token=token,
            reveal_solution=True,
            solution=solution,
        )
        api.create_issue_comment(issue_number, body)
        phase = timeout_state.get("phase", "active")
        sync_room_labels(api, issue_number, phase=phase)
        _on_game_terminal(issue_number, timeout_state)
        _lock_on_terminal_transition(
            api=api,
            issue_number=issue_number,
            previous_phase=current_state.get("phase", "active"),
            phase=phase,
        )
        return

    current_conduct_state, _ = find_latest_conduct_state_from_comments(
        comments,
        signing_secret=signing_secret,
        room_key=room_key,
        owner=owner,
        issue_number=issue_number,
    )
    if current_conduct_state is None:
        current_conduct_state = build_initial_conduct_state(
            repo,
            issue_number,
            owner,
            policy=moderation_policy,
        )

    hint_block: str | None = None
    hint_overlay: dict[str, Any] | None = None

    perfectionist_gate = apply_perfectionist_gate(
        previous=current_state,
        parsed_command=command,
        comment_id=comment_id,
        solution=solution,
    )
    if perfectionist_gate is not None:
        (next_state, headline, reveal_solution, should_emit), hint_block = perfectionist_gate
    else:
        next_state, headline, reveal_solution, should_emit = apply_command_to_state(
            previous=current_state,
            parsed_command=command,
            comment_id=comment_id,
            solution=solution,
        )

    if not should_emit:
        print("Duplicate or no-op command. Skipping comment emit.")
        return

    if not is_valid_state_transition(current_state, next_state):
        print("State transition rejected: stale/rollback candidate.")
        return

    if (
        room_hints_enabled(current_state)
        and command.get("kind") == "guess"
        and not command.get("error")
        and current_state.get("phase") == "active"
    ):
        summary = compute_deductive_hint_summary(
            previous_state=current_state,
            guess=command.get("guess", []),
        )
        if summary is not None:
            hint_block = render_deductive_hints_markdown(summary)
            certain_positions = [
                int(item.get("position", 0))
                for item in summary.get("certain", [])
                if int(item.get("position", 0)) > 0
            ]
            impossible_positions = [
                int(item.get("position", 0))
                for item in summary.get("impossible", [])
                if int(item.get("position", 0)) > 0
            ]
            hint_overlay = {
                "attempt": int(next_state.get("attempt", 0)),
                "certain_positions": certain_positions,
                "impossible_positions": impossible_positions,
            }

    token = encode_state_token(next_state, signing_secret)
    body = _upload_board_and_render(
        api=api,
        repo=repo,
        issue_number=issue_number,
        state=next_state,
        headline=headline,
        token=token,
        reveal_solution=reveal_solution,
        solution=solution,
        hint_block=hint_block,
        hint_overlay=hint_overlay,
    )

    previous_phase = current_state.get("phase", "active")
    phase = next_state.get("phase", "active")
    api.create_issue_comment(issue_number, body)
    sync_room_labels(api, issue_number, phase=phase)
    # On first transition to terminal, persist result and finalize room lifecycle.
    if previous_phase not in TERMINAL_PHASES and phase in TERMINAL_PHASES:
        _on_game_terminal(issue_number, next_state)
    _lock_on_terminal_transition(
        api=api,
        issue_number=issue_number,
        previous_phase=previous_phase,
        phase=phase,
    )


# ── Conduct-comment handler ──────────────────────────────────────────


def handle_issue_comment_conduct(
    *,
    api: GitHubAPI,
    repo: str,
    payload: dict[str, Any],
    signing_secret: str,
    moderation_policy: dict[str, Any],
) -> None:
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    issue_number = int(issue["number"])
    owner = (issue.get("user") or {}).get("login") or ""
    actor = ((comment.get("user") or {}).get("login") or "").strip()
    comment_id = int(comment["id"])
    comment_body = comment.get("body", "")

    if issue.get("pull_request"):
        print("PR comment payload ignored for conduct lane.")
        return
    if not issue_has_label(issue, "game:mistermind"):
        print("Issue is not labeled game:mistermind. Skipping conduct lane.")
        return

    room_key = issue_room_key(repo, issue_number)
    comments = api.list_issue_comments(issue_number)

    current_conduct_state, conduct_comment_id = find_latest_conduct_state_from_comments(
        comments,
        signing_secret=signing_secret,
        room_key=room_key,
        owner=owner,
        issue_number=issue_number,
    )
    if current_conduct_state is None:
        current_conduct_state = build_initial_conduct_state(
            repo,
            issue_number,
            owner,
            policy=moderation_policy,
        )

    last_game_state_comment_id = find_latest_game_state_comment_id(
        comments,
        signing_secret=signing_secret,
        room_key=room_key,
        owner=owner,
        issue_number=issue_number,
    )
    parsed_command = parse_command(comment_body if isinstance(comment_body, str) else "")
    pre_response_spam = owner_has_prior_unanswered_command(
        comments,
        owner=owner,
        current_comment_id=comment_id,
        last_game_state_comment_id=last_game_state_comment_id,
    )

    next_conduct_state, moderation_messages = apply_conduct_input(
        previous=current_conduct_state,
        actor=actor,
        owner=owner,
        parsed_command=parsed_command,
        pre_response_spam=pre_response_spam,
        policy=moderation_policy,
    )

    token = encode_conduct_token(next_conduct_state, signing_secret)
    ledger_body = render_conduct_state_comment(token)
    if conduct_comment_id is not None:
        api.update_issue_comment(conduct_comment_id, ledger_body)
    else:
        api.create_issue_comment(issue_number, ledger_body)

    sync_conduct_labels(api, issue_number, next_conduct_state, policy=moderation_policy)

    if moderation_messages:
        api.create_issue_comment(issue_number, "\n\n".join(moderation_messages))


# ── Remote action dispatch ───────────────────────────────────────────


def _remote_command_body(action: str, guess_text: str = "") -> str:
    """Map a remote action token to a canonical command body."""
    action_norm = action.strip().lower()
    if action_norm == "guess":
        guess = guess_text.strip()
        if not guess:
            raise ValueError("Remote action 'guess' requires MM_REMOTE_GUESS.")
        return f"/guess {guess}"
    if action_norm in {"status", "help", "giveup"}:
        return f"/{action_norm}"
    raise ValueError(f"Unsupported remote action: {action!r}")


def handle_remote_action(
    *,
    api: GitHubAPI,
    repo: str,
    issue_number: int,
    action: str,
    guess_text: str,
    signing_secret: str,
    solution_salt: str,
    moderation_policy: dict[str, Any],
    apply_moderation: bool = True,
) -> None:
    """Run a command against a room from workflow_dispatch inputs."""
    issue = api.get_issue(issue_number)
    if not issue_has_label(issue, "game:mistermind"):
        raise RuntimeError(f"Issue #{issue_number} is not labeled game:mistermind.")
    if issue.get("pull_request"):
        raise RuntimeError(f"Issue #{issue_number} is a pull request thread.")

    owner = ((issue.get("user") or {}).get("login") or "").strip()
    if not owner:
        raise RuntimeError(f"Issue #{issue_number} has no owner login.")

    comment_body = _remote_command_body(action, guess_text)
    synthetic_comment_id = -int(dt.datetime.now(dt.UTC).timestamp() * 1_000_000)
    payload = {
        "action": "created",
        "issue": issue,
        "comment": {
            "id": synthetic_comment_id,
            "body": comment_body,
            "user": {"login": owner},
        },
    }

    if apply_moderation:
        handle_issue_comment_conduct(
            api=api,
            repo=repo,
            payload=payload,
            signing_secret=signing_secret,
            moderation_policy=moderation_policy,
        )

    handle_issue_comment(
        api=api,
        repo=repo,
        payload=payload,
        signing_secret=signing_secret,
        solution_salt=solution_salt,
        moderation_policy=moderation_policy,
    )


# ── Entry point ──────────────────────────────────────────────────────


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    engine_mode = os.environ.get("MM_ENGINE_MODE", "game").strip().lower()
    moderation_policy_path = os.environ.get("MM_MOD_POLICY_PATH", DEFAULT_POLICY_PATH)

    if not repository or not token:
        print("Missing required GitHub Actions environment variables (GH_TOKEN).", file=sys.stderr)
        return 1

    api = GitHubAPI(repository, token)
    usage_issue_number: int | None = None

    try:
        # Seed the rate-limit state so budget_low works before any game calls.
        rate_info = api.poll_rate_limit()
        if rate_info:
            core = (rate_info.get("resources") or {}).get("core") or {}
            remaining = core.get("remaining")
            if isinstance(remaining, int):
                api.remaining = remaining
            reset_val = core.get("reset")
            if isinstance(reset_val, int):
                api.reset_at = reset_val
            print(f"Rate limit seed: remaining={api.remaining}, reset_at={api.reset_at}")

        paused_until = parse_iso_utc(os.environ.get("MM_PAUSED_UNTIL", ""))
        rate_mode = os.environ.get("MM_RATE_MODE", "").strip().lower()
        if (
            paused_until is not None
            and paused_until > dt.datetime.now(dt.UTC)
            and engine_mode not in {"sweep", "cleanup"}
        ):
            print(
                f"Gameplay is paused until {paused_until.replace(microsecond=0).isoformat()}. "
                f"Skipping {engine_mode} lane."
            )
            return 0

        if engine_mode in ("sweep", "cleanup"):
            closed = handle_terminal_room_sweep(api=api)
            print(f"Sweep closed {len(closed)} terminal room(s): {closed}")
            return 0

        if rate_mode in {"lockdown", "secondary_lockdown"} and engine_mode in {
            "moderation",
            "conduct",
            "remote",
            "action",
            "game",
        }:
            print(f"Rate-control mode `{rate_mode}` is active. Skipping {engine_mode} lane.")
            return 0

        solution_salt = os.environ.get("MISTERMIND_SALT", "")
        if not solution_salt:
            print("Missing required secret: MISTERMIND_SALT", file=sys.stderr)
            return 1

        explicit_signing = os.environ.get("MISTERMIND_STATE_SIGNING_SECRET", "")
        signing_secret = derive_signing_secret(solution_salt, explicit_signing)
        moderation_policy = load_moderation_policy(moderation_policy_path)

        if engine_mode in ("remote", "action"):
            issue_raw = os.environ.get("MM_REMOTE_ISSUE_NUMBER", "").strip()
            action = os.environ.get("MM_REMOTE_ACTION", "").strip().lower()
            guess_text = os.environ.get("MM_REMOTE_GUESS", "")
            apply_moderation_raw = os.environ.get("MM_REMOTE_APPLY_MODERATION", "1").strip().lower()
            apply_moderation = apply_moderation_raw not in {"0", "false", "no", "off"}

            if not issue_raw:
                print("Missing MM_REMOTE_ISSUE_NUMBER for remote mode.", file=sys.stderr)
                return 1
            if not action:
                print("Missing MM_REMOTE_ACTION for remote mode.", file=sys.stderr)
                return 1
            try:
                usage_issue_number = int(issue_raw)
            except ValueError:
                print(f"Invalid MM_REMOTE_ISSUE_NUMBER: {issue_raw!r}", file=sys.stderr)
                return 1

            handle_remote_action(
                api=api,
                repo=repository,
                issue_number=usage_issue_number,
                action=action,
                guess_text=guess_text,
                signing_secret=signing_secret,
                solution_salt=solution_salt,
                moderation_policy=moderation_policy,
                apply_moderation=apply_moderation,
            )
            return 0

        event_name = os.environ.get("GITHUB_EVENT_NAME", "")
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not event_name or not event_path:
            print("Missing required GitHub event payload environment.", file=sys.stderr)
            return 1

        with open(event_path, encoding="utf-8") as handle:
            payload = json.load(handle)

        usage_issue_number = int((payload.get("issue") or {}).get("number", 0)) or None

        if rate_mode == "slowdown" and event_name == "issues":
            print("Rate-control mode `slowdown` is active. New room creation is disabled.")
            return 0

        if rate_mode in {"lockdown", "secondary_lockdown"} and event_name in {
            "issues",
            "issue_comment",
        }:
            print(f"Rate-control mode `{rate_mode}` is active. Event `{event_name}` skipped.")
            return 0

        if engine_mode in ("moderation", "conduct"):
            if event_name == "issue_comment":
                handle_issue_comment_conduct(
                    api=api,
                    repo=repository,
                    payload=payload,
                    signing_secret=signing_secret,
                    moderation_policy=moderation_policy,
                )
            else:
                print(f"Conduct lane ignores event: {event_name}.")
            return 0

        if event_name == "issues":
            if should_process_issue_open(payload):
                handle_issue_opened(
                    api=api,
                    repo=repository,
                    payload=payload,
                    signing_secret=signing_secret,
                    solution_salt=solution_salt,
                )
            else:
                print("Issue event does not match MisterMind room criteria. Skipping.")

        elif event_name == "issue_comment":
            if should_process_issue_comment(payload):
                handle_issue_comment(
                    api=api,
                    repo=repository,
                    payload=payload,
                    signing_secret=signing_secret,
                    solution_salt=solution_salt,
                    moderation_policy=moderation_policy,
                )
            else:
                print("Issue comment event rejected by ingress criteria. Skipping.")

        else:
            print(f"Unsupported event: {event_name}.")

    except SecondaryRateLimitError as exc:
        print(f"KILL SWITCH: {exc}", file=sys.stderr)
        try:
            api.set_repo_interaction_limit(limit="collaborators_only", expiry="one_day")
            print("Repository interaction limit raised to collaborators_only.")
        except Exception as limit_exc:
            print(f"Failed to raise repository interaction limit: {limit_exc}", file=sys.stderr)
        # Try to post a notice on the issue
        try:
            reset_utc = dt.datetime.fromtimestamp(exc.reset_at, tz=dt.UTC)
            api.create_issue_comment(
                usage_issue_number or 0,
                "> **Game paused.** The repository has hit a rate limit. "
                f"Service should resume after {reset_utc:%H:%M} UTC. "
                "Sorry for the interruption!",
            )
        except Exception:
            pass  # truly throttled
        return 2
    finally:
        print(f"API usage ({engine_mode}): {api.call_summary()}")

    return 0
