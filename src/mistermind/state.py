"""
Game-state and conduct-state lifecycle.

Builds initial state dicts, validates their structure, encodes/decodes
signed state tokens, derives solutions and signing secrets, and checks
state-transition integrity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from mistermind.constants import (
    BOARD_THEMES,
    CODE_LENGTH,
    CONDUCT_CHAIN_VERSION,
    CONDUCT_MARKER,
    CONDUCT_SCHEMA,
    CONDUCT_VERSION,
    DEFAULT_BOARD_THEME,
    MAX_ATTEMPTS,
    PALETTE,
    ROOM_VARIANTS,
    STATE_CHAIN_VERSION,
    STATE_MARKER,
    STATE_SCHEMA,
    STATE_VERSION,
)
from mistermind.parsing import normalize_board_theme, normalize_room_variant
from mistermind.utils import (
    b64url_decode,
    b64url_encode,
    canonical_json,
    issue_room_key,
    now_iso,
)

# ── Solution / signing derivation ────────────────────────────────────


def derive_solution(room_key: str, salt: str) -> list[str]:
    digest = hmac.new(
        salt.encode("utf-8"),
        f"{room_key}|mistermind-v1".encode(),
        hashlib.sha256,
    ).digest()
    return [PALETTE[digest[i] % len(PALETTE)] for i in range(CODE_LENGTH)]


def derive_signing_secret(salt: str, explicit_secret: str | None) -> str:
    if explicit_secret:
        return explicit_secret
    return hmac.new(
        salt.encode("utf-8"),
        b"mistermind-state-signing-v1",
        hashlib.sha256,
    ).hexdigest()


# ── Token encode/decode ──────────────────────────────────────────────


def token_signature(payload_b64: str, signing_secret: str, marker: str) -> str:
    raw_sig = hmac.new(
        signing_secret.encode("utf-8"),
        f"{marker}:{payload_b64}".encode(),
        hashlib.sha256,
    ).digest()
    return b64url_encode(raw_sig)


def encode_state_token(state: dict[str, Any], signing_secret: str) -> str:
    payload_raw = canonical_json(state).encode("utf-8")
    payload_b64 = b64url_encode(payload_raw)
    sig_b64 = token_signature(payload_b64, signing_secret, STATE_MARKER)
    return f"{payload_b64}.{sig_b64}"


def decode_state_token(token: str, signing_secret: str) -> dict[str, Any] | None:
    if "." not in token:
        return None
    payload_b64, sig_b64 = token.split(".", 1)
    expected = token_signature(payload_b64, signing_secret, STATE_MARKER)
    if not hmac.compare_digest(expected, sig_b64):
        return None
    try:
        payload = b64url_decode(payload_b64).decode("utf-8")
        result: dict[str, Any] = json.loads(payload)
        return result
    except (ValueError, json.JSONDecodeError):
        return None


def extract_state_token(text: str) -> str | None:
    if not text:
        return None
    match = re.search(
        rf"{re.escape(STATE_MARKER)}\s+([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
        text,
    )
    return match.group(1) if match else None


def encode_conduct_token(state: dict[str, Any], signing_secret: str) -> str:
    payload_raw = canonical_json(state).encode("utf-8")
    payload_b64 = b64url_encode(payload_raw)
    sig_b64 = token_signature(payload_b64, signing_secret, CONDUCT_MARKER)
    return f"{payload_b64}.{sig_b64}"


def decode_conduct_token(token: str, signing_secret: str) -> dict[str, Any] | None:
    if "." not in token:
        return None
    payload_b64, sig_b64 = token.split(".", 1)
    expected = token_signature(payload_b64, signing_secret, CONDUCT_MARKER)
    if not hmac.compare_digest(expected, sig_b64):
        return None
    try:
        payload = b64url_decode(payload_b64).decode("utf-8")
        result: dict[str, Any] = json.loads(payload)
        return result
    except (ValueError, json.JSONDecodeError):
        return None


def extract_conduct_token(text: str) -> str | None:
    if not text:
        return None
    match = re.search(
        rf"{re.escape(CONDUCT_MARKER)}\s+([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
        text,
    )
    return match.group(1) if match else None


# ── State validation ─────────────────────────────────────────────────


def state_is_valid(
    state: dict[str, Any],
    *,
    room_key: str,
    owner: str,
    issue_number: int,
) -> bool:
    if state.get("schema") != STATE_SCHEMA:
        return False
    if state.get("version") != STATE_VERSION:
        return False
    if state.get("chain_version") != STATE_CHAIN_VERSION:
        return False
    if state.get("room_key") != room_key:
        return False
    if state.get("owner") != owner:
        return False
    if state.get("issue_number") != issue_number:
        return False

    raw_variant = state.get("variant", "classic")
    if not isinstance(raw_variant, str):
        return False
    if raw_variant.strip().lower() not in ROOM_VARIANTS:
        return False

    raw_board_theme = state.get("board_theme", DEFAULT_BOARD_THEME)
    if not isinstance(raw_board_theme, str):
        return False
    if raw_board_theme.strip().lower() not in BOARD_THEMES:
        return False

    history = state.get("history")
    if not isinstance(history, list):
        return False

    attempt = state.get("attempt")
    seq = state.get("seq")
    if not isinstance(attempt, int) or not isinstance(seq, int):
        return False
    if attempt < 0 or attempt > MAX_ATTEMPTS:
        return False
    if seq < attempt:
        return False
    if attempt != len(history):
        return False

    processed = state.get("processed_comment_ids")
    if not isinstance(processed, list):
        return False

    for idx, entry in enumerate(history, start=1):
        if entry.get("attempt") != idx:
            return False
        guess = entry.get("guess")
        if not isinstance(guess, list) or len(guess) != CODE_LENGTH:
            return False
        for color in guess:
            if color not in PALETTE:
                return False
        black = entry.get("black")
        white = entry.get("white")
        if not isinstance(black, int) or not isinstance(white, int):
            return False
        if black < 0 or white < 0 or black + white > CODE_LENGTH:
            return False

    phase = state.get("phase")
    return phase in {"active", "won", "lost"}


def conduct_state_is_valid(
    state: dict[str, Any],
    *,
    room_key: str,
    owner: str,
    issue_number: int,
) -> bool:
    if state.get("schema") != CONDUCT_SCHEMA:
        return False
    if state.get("version") != CONDUCT_VERSION:
        return False
    if state.get("chain_version") != CONDUCT_CHAIN_VERSION:
        return False
    if state.get("room_key") != room_key:
        return False
    if state.get("owner") != owner:
        return False
    if state.get("issue_number") != issue_number:
        return False
    if not isinstance(state.get("seq"), int):
        return False

    owner_state = state.get("owner_state")
    if not isinstance(owner_state, dict):
        return False
    if not isinstance(owner_state.get("malformed_streak"), int):
        return False
    if not isinstance(owner_state.get("malformed_total"), int):
        return False
    if not isinstance(owner_state.get("pre_response_spam_warnings"), int):
        return False

    actors = state.get("actors")
    if not isinstance(actors, dict):
        return False
    recent_events = state.get("recent_events")
    return isinstance(recent_events, list)


# ── Transition validation ────────────────────────────────────────────


def is_valid_state_transition(previous: dict[str, Any], candidate: dict[str, Any]) -> bool:
    prev_seq = previous.get("seq")
    cand_seq = candidate.get("seq")
    if not isinstance(prev_seq, int) or not isinstance(cand_seq, int):
        return False
    if cand_seq <= prev_seq:
        return False

    prev_attempt = previous.get("attempt")
    cand_attempt = candidate.get("attempt")
    if not isinstance(prev_attempt, int) or not isinstance(cand_attempt, int):
        return False
    if cand_attempt < prev_attempt:
        return False
    return not cand_attempt > prev_attempt + 1


# ── State builders ───────────────────────────────────────────────────


def build_initial_state(
    repo: str,
    issue_number: int,
    owner: str,
    *,
    variant: str = "classic",
    board_theme: str = DEFAULT_BOARD_THEME,
) -> dict[str, Any]:
    timestamp = now_iso()
    mode = normalize_room_variant(variant)
    theme = normalize_board_theme(board_theme)
    return {
        "schema": STATE_SCHEMA,
        "version": STATE_VERSION,
        "chain_version": STATE_CHAIN_VERSION,
        "room_key": issue_room_key(repo, issue_number),
        "issue_number": issue_number,
        "owner": owner,
        "variant": mode,
        "board_theme": theme,
        "phase": "active",
        "attempt": 0,
        "seq": 0,
        "max_attempts": MAX_ATTEMPTS,
        "history": [],
        "processed_comment_ids": [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_action": "room_created",
    }


def build_initial_conduct_state(
    repo: str,
    issue_number: int,
    owner: str,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mistermind.conduct import default_moderation_policy

    active_policy = policy or default_moderation_policy()
    timestamp = now_iso()
    return {
        "schema": CONDUCT_SCHEMA,
        "version": CONDUCT_VERSION,
        "chain_version": CONDUCT_CHAIN_VERSION,
        "policy_version": int(active_policy.get("version", 1)),
        "room_key": issue_room_key(repo, issue_number),
        "issue_number": issue_number,
        "owner": owner,
        "owner_state": {
            "malformed_streak": 0,
            "malformed_total": 0,
            "pre_response_spam_warnings": 0,
            "cooldown_until": None,
            "disqualified": False,
        },
        "actors": {},
        "recent_events": [],
        "seq": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


# ── Command application (delegated to commands module) ───────────────
# Re-exported here for backward compatibility with callers that import
# from state.

from mistermind.commands import (  # noqa: F401, E402
    apply_command_to_state,
    apply_owner_guardrail_to_state,
    apply_perfectionist_gate,
)
