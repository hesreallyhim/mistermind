"""
Moderation / conduct-policy engine.

Handles moderation policy defaults, normalization, file loading,
conduct-state event recording, cooldown tracking, and the main
apply_conduct_input() state machine that enforces fair-play rules.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from typing import Any

from mistermind.constants import CONDUCT_MARKER, DEFAULT_POLICY_PATH
from mistermind.utils import (
    _as_int,
    merge_dict,
    now_iso,
    parse_iso_utc,
    resolve_runtime_path,
)

# ── Policy defaults / normalization ──────────────────────────────────


def default_moderation_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "owner_malformed": {
            "warning_streak": 1,
            "cooldown_streak": 2,
            "violation_streak": 3,
            "cooldown_minutes": 10,
            "disqualify_on_violation": False,
        },
        "owner_spam": {
            "warning_count": 1,
            "cooldown_count": 2,
            "violation_count": 3,
            "cooldown_minutes": 10,
            "disqualify_on_violation": False,
        },
        "non_owner": {
            "warning_attempt": 1,
            "mute_attempt": 2,
            "escalation_attempt": 3,
            "mute_minutes": 60,
        },
        "retention": {
            "max_actor_records": 50,
            "max_recent_events": 200,
        },
    }


def normalize_moderation_policy(policy: dict[str, Any]) -> dict[str, Any]:
    base = default_moderation_policy()
    merged = merge_dict(base, policy)

    owner_malformed = merged["owner_malformed"]
    owner_spam = merged["owner_spam"]
    non_owner = merged["non_owner"]
    retention = merged["retention"]

    om: dict[str, Any] = {
        "warning_streak": _as_int(owner_malformed.get("warning_streak"), 1, 1),
        "cooldown_streak": _as_int(owner_malformed.get("cooldown_streak"), 2, 1),
        "violation_streak": _as_int(owner_malformed.get("violation_streak"), 3, 1),
        "cooldown_minutes": _as_int(owner_malformed.get("cooldown_minutes"), 10, 1),
        "disqualify_on_violation": bool(owner_malformed.get("disqualify_on_violation", True)),
    }
    os: dict[str, Any] = {
        "warning_count": _as_int(owner_spam.get("warning_count"), 1, 1),
        "cooldown_count": _as_int(owner_spam.get("cooldown_count"), 2, 1),
        "violation_count": _as_int(owner_spam.get("violation_count"), 3, 1),
        "cooldown_minutes": _as_int(owner_spam.get("cooldown_minutes"), 10, 1),
        "disqualify_on_violation": bool(owner_spam.get("disqualify_on_violation", False)),
    }

    # Enforce threshold ordering: warning <= cooldown <= violation
    if om["cooldown_streak"] < om["warning_streak"]:
        om["cooldown_streak"] = om["warning_streak"]
    if os["cooldown_count"] < os["warning_count"]:
        os["cooldown_count"] = os["warning_count"]
    if om["violation_streak"] < om["cooldown_streak"]:
        om["violation_streak"] = om["cooldown_streak"]
    if os["violation_count"] < os["cooldown_count"]:
        os["violation_count"] = os["cooldown_count"]

    return {
        "version": _as_int(merged.get("version"), base["version"], 1),
        "owner_malformed": om,
        "owner_spam": os,
        "non_owner": {
            "warning_attempt": _as_int(non_owner.get("warning_attempt"), 1, 1),
            "mute_attempt": _as_int(non_owner.get("mute_attempt"), 2, 1),
            "escalation_attempt": _as_int(non_owner.get("escalation_attempt"), 3, 1),
            "mute_minutes": _as_int(non_owner.get("mute_minutes"), 60, 1),
        },
        "retention": {
            "max_actor_records": _as_int(retention.get("max_actor_records"), 50, 5),
            "max_recent_events": _as_int(retention.get("max_recent_events"), 200, 20),
        },
    }


def load_moderation_policy(path: str | None) -> dict[str, Any]:
    candidate = (path or "").strip() or DEFAULT_POLICY_PATH
    resolved = resolve_runtime_path(candidate)
    if not resolved.exists():
        return default_moderation_policy()
    try:
        with open(resolved, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load moderation policy at {resolved}: {exc}. Using defaults.")
        return default_moderation_policy()
    if not isinstance(raw, dict):
        print(f"Moderation policy at {resolved} is not a JSON object. Using defaults.")
        return default_moderation_policy()
    return normalize_moderation_policy(raw)


# ── Conduct-state helpers ────────────────────────────────────────────


def conduct_note_event(
    state: dict[str, Any],
    *,
    kind: str,
    actor: str,
    detail: str = "",
) -> None:
    events = state.setdefault("recent_events", [])
    events.append(
        {
            "at": now_iso(),
            "kind": kind,
            "actor": actor,
            "detail": detail,
        }
    )
    state["recent_events"] = events


def owner_cooldown_active(owner_state: dict[str, Any]) -> bool:
    cooldown_until = parse_iso_utc(owner_state.get("cooldown_until"))
    if cooldown_until is None:
        return False
    return cooldown_until > dt.datetime.now(dt.UTC)


def render_conduct_state_comment(token: str) -> str:
    """Render the conduct state as an HTML comment (invisible to users)."""
    return f"<!-- {CONDUCT_MARKER} {token} -->"


def set_owner_cooldown(owner_state: dict[str, Any], *, minutes: int) -> None:
    cooldown_until = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)
    owner_state["cooldown_until"] = (
        cooldown_until.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def set_owner_disqualified(owner_state: dict[str, Any]) -> None:
    owner_state["disqualified"] = True


# ── Main conduct state machine ───────────────────────────────────────


def apply_conduct_input(
    *,
    previous: dict[str, Any],
    actor: str,
    owner: str,
    parsed_command: dict[str, Any],
    pre_response_spam: bool,
    policy: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    state = copy.deepcopy(previous)
    owner_state = state.setdefault("owner_state", {})
    actors = state.setdefault("actors", {})
    messages: list[str] = []
    actor_key = actor.lower()
    owner_key = owner.lower()

    if actor_key != owner_key:
        non_owner_policy = policy["non_owner"]
        actor_state = actors.setdefault(
            actor_key,
            {
                "non_owner_attempts": 0,
                "warnings": 0,
                "muted_until": None,
                "last_at": None,
            },
        )
        actor_state["non_owner_attempts"] = int(actor_state.get("non_owner_attempts", 0)) + 1
        actor_state["last_at"] = now_iso()
        conduct_note_event(
            state,
            kind="non_owner_comment_attempt",
            actor=actor_key,
            detail=f"attempt={actor_state['non_owner_attempts']}",
        )
        if actor_state["non_owner_attempts"] == int(non_owner_policy["warning_attempt"]):
            actor_state["warnings"] = int(actor_state.get("warnings", 0)) + 1
            messages.append(
                f"@{actor} This room is owner-only. Please leave gameplay comments to @{owner}."
            )
        elif actor_state["non_owner_attempts"] == int(non_owner_policy["mute_attempt"]):
            actor_state["warnings"] = int(actor_state.get("warnings", 0)) + 1
            conduct_note_event(
                state,
                kind="non_owner_comment_monitoring",
                actor=actor_key,
                detail=f"attempt={actor_state['non_owner_attempts']}",
            )
            messages.append(
                f"@{actor} Repeated non-owner play attempts are being logged. "
                f"Please keep active rooms owner-only."
            )
        elif actor_state["non_owner_attempts"] >= int(non_owner_policy["escalation_attempt"]):
            if not actor_state.get("escalated"):
                actor_state["escalated"] = True
                conduct_note_event(
                    state,
                    kind="non_owner_escalation_candidate",
                    actor=actor_key,
                    detail=f"attempt={actor_state['non_owner_attempts']}",
                )
            messages.append(
                f"@{actor} Continued non-owner command attempts were logged for escalation review."
            )
    else:
        owner_spam_policy = policy["owner_spam"]
        owner_malformed_policy = policy["owner_malformed"]
        if pre_response_spam and parsed_command["kind"] in {"guess", "status", "help", "giveup"}:
            owner_state["pre_response_spam_warnings"] = (
                int(owner_state.get("pre_response_spam_warnings", 0)) + 1
            )
            spam_count = int(owner_state["pre_response_spam_warnings"])
            conduct_note_event(
                state,
                kind="owner_pre_response_spam",
                actor=owner_key,
                detail=f"count={spam_count}",
            )
            if spam_count == int(owner_spam_policy["warning_count"]):
                messages.append(
                    f"@{owner} Please wait for the bot response before posting another command."
                )
            elif spam_count == int(owner_spam_policy["cooldown_count"]):
                conduct_note_event(
                    state,
                    kind="owner_spam_monitoring",
                    actor=owner_key,
                    detail=f"count={spam_count}",
                )
                messages.append(
                    f"@{owner} Repeated pre-response commands are being logged. "
                    "No automatic penalty is applied right now."
                )
            elif spam_count >= int(owner_spam_policy["violation_count"]):
                conduct_note_event(
                    state,
                    kind="owner_spam_escalation_candidate",
                    actor=owner_key,
                    detail=f"count={spam_count}",
                )
                messages.append(f"@{owner} Continued rapid-fire commands were logged for review.")

        if parsed_command["kind"] == "guess":
            if parsed_command.get("error"):
                owner_state["malformed_streak"] = int(owner_state.get("malformed_streak", 0)) + 1
                owner_state["malformed_total"] = int(owner_state.get("malformed_total", 0)) + 1
                streak = int(owner_state["malformed_streak"])
                conduct_note_event(
                    state,
                    kind="owner_malformed_guess",
                    actor=owner_key,
                    detail=f"streak={streak}",
                )
                if streak == int(owner_malformed_policy["warning_streak"]):
                    messages.append(
                        f"@{owner} Malformed guess warning ({streak}/{int(owner_malformed_policy['violation_streak'])}). "
                        "Use `/guess red blue green yellow` or `guess r b g y`."
                    )
                elif streak == int(owner_malformed_policy["cooldown_streak"]):
                    conduct_note_event(
                        state,
                        kind="owner_malformed_monitoring",
                        actor=owner_key,
                        detail=f"streak={streak}",
                    )
                    messages.append(
                        f"@{owner} Repeated malformed guesses are being logged. "
                        "No automatic penalty is applied right now."
                    )
                elif streak >= int(owner_malformed_policy["violation_streak"]):
                    conduct_note_event(
                        state,
                        kind="owner_malformed_escalation_candidate",
                        actor=owner_key,
                        detail=f"streak={streak}",
                    )
                    messages.append(
                        f"@{owner} Repeated malformed guesses were logged for review. "
                        "Try the documented guess formats before sending another command."
                    )
            else:
                if int(owner_state.get("malformed_streak", 0)) > 0:
                    conduct_note_event(
                        state,
                        kind="owner_malformed_streak_reset",
                        actor=owner_key,
                        detail="valid_guess",
                    )
                owner_state["malformed_streak"] = 0

    # Keep actor map bounded by recency.
    max_actors = int(policy["retention"]["max_actor_records"])
    if len(actors) > max_actors:
        sortable: list[tuple[str, str]] = []
        for key, value in actors.items():
            last_at = value.get("last_at") or ""
            sortable.append((key, last_at))
        sortable.sort(key=lambda item: item[1], reverse=True)
        keep = {key for key, _ in sortable[:max_actors]}
        state["actors"] = {key: value for key, value in actors.items() if key in keep}

    max_events = int(policy["retention"]["max_recent_events"])
    events = state.get("recent_events", [])
    if isinstance(events, list) and len(events) > max_events:
        state["recent_events"] = events[-max_events:]

    state["policy_version"] = int(policy.get("version", state.get("policy_version", 1)))
    state["seq"] = int(state.get("seq", 0)) + 1
    state["updated_at"] = now_iso()
    return state, messages


# ── Label synchronization ────────────────────────────────────────────


def sync_conduct_labels(
    api: Any,
    issue_number: int,
    conduct_state: dict[str, Any],
    *,
    policy: dict[str, Any],
) -> None:
    del api, issue_number, conduct_state, policy
