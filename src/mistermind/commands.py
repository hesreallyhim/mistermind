"""
Command application logic.

Pure-function state transformers that apply parsed commands (guess,
status, help, giveup) and perfectionist-mode gates to the game state.
"""

from __future__ import annotations

import copy
from typing import Any

from mistermind.constants import CODE_LENGTH, MAX_ATTEMPTS, TERMINAL_PHASES
from mistermind.rendering import format_feedback_summary
from mistermind.utils import now_iso


def apply_command_to_state(
    *,
    previous: dict[str, Any],
    parsed_command: dict[str, Any],
    comment_id: int,
    solution: list[str],
) -> tuple[dict[str, Any], str, bool, bool]:
    """
    Returns:
      (next_state, headline, reveal_solution, should_emit_comment)
    """
    next_state = copy.deepcopy(previous)
    processed = list(next_state.get("processed_comment_ids", []))
    if comment_id in processed:
        return previous, "Duplicate comment ignored.", False, False

    phase = next_state.get("phase", "active")
    kind = parsed_command["kind"]
    reveal_solution = False

    if kind == "help":
        headline = "Help requested."
        next_state["last_action"] = "help"
    elif kind == "status":
        headline = "Current room status."
        next_state["last_action"] = "status"
    elif kind == "giveup":
        if phase in TERMINAL_PHASES:
            headline = f"Room already ended ({phase})."
        else:
            next_state["phase"] = "lost"
            headline = "You gave up. Room marked as lost."
        reveal_solution = True
        next_state["last_action"] = "giveup"
    elif kind == "guess":
        if phase in TERMINAL_PHASES:
            headline = f"Room already ended ({phase}). Use `/status`."
            reveal_solution = True
            next_state["last_action"] = "guess_after_terminal"
        elif parsed_command.get("error"):
            headline = parsed_command["error"]
            next_state["last_action"] = "guess_invalid"
        else:
            guess = parsed_command["guess"]
            black, white = _score_guess_for_command(solution, guess)
            attempt_no = int(next_state.get("attempt", 0)) + 1
            history = list(next_state.get("history", []))
            history.append(
                {
                    "attempt": attempt_no,
                    "guess": guess,
                    "black": black,
                    "white": white,
                }
            )
            next_state["history"] = history
            next_state["attempt"] = attempt_no
            feedback_summary = format_feedback_summary(black, white)

            if black == CODE_LENGTH:
                next_state["phase"] = "won"
                reveal_solution = True
                headline = f"Attempt {attempt_no}: solved ({feedback_summary})."
                next_state["last_action"] = "guess_win"
            elif attempt_no >= MAX_ATTEMPTS:
                next_state["phase"] = "lost"
                reveal_solution = True
                headline = f"Attempt {attempt_no}: {feedback_summary}. No attempts remaining."
                next_state["last_action"] = "guess_loss"
            else:
                headline = f"Attempt {attempt_no}: {feedback_summary}."
                next_state["last_action"] = "guess"
    else:
        return previous, "Unsupported command ignored.", False, False

    processed.append(comment_id)
    next_state["processed_comment_ids"] = processed[-200:]
    next_state["seq"] = int(next_state.get("seq", 0)) + 1
    next_state["updated_at"] = now_iso()
    return next_state, headline, reveal_solution, True


def _score_guess_for_command(solution: list[str], guess: list[str]) -> tuple[int, int]:
    """Thin wrapper to avoid circular import -- delegates to scoring.score_guess."""
    from mistermind.scoring import score_guess

    return score_guess(solution, guess)


def apply_perfectionist_gate(
    *,
    previous: dict[str, Any],
    parsed_command: dict[str, Any],
    comment_id: int,
    solution: list[str],
) -> tuple[tuple[dict[str, Any], str, bool, bool], str | None] | None:
    """Apply perfectionist-mode constraints before normal command flow.

    Returns:
      None -> no gate applied, continue normal processing.
      ((next_state, headline, reveal_solution, should_emit), hint_block)
          -> gate handled this command path.
    """
    from mistermind.parsing import room_perfectionist_enabled
    from mistermind.scoring import (
        compute_perfectionist_optimality_summary,
        render_perfectionist_failure_markdown,
        render_perfectionist_retry_markdown,
        score_guess,
    )

    if not room_perfectionist_enabled(previous):
        return None
    if previous.get("phase") != "active":
        return None
    if parsed_command.get("kind") != "guess" or parsed_command.get("error"):
        return None

    guess = parsed_command.get("guess", [])
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH:
        return None

    # Exception to strict gate: if this guess fully solves the room,
    # allow normal win resolution even if it is not minimax-optimal.
    black, _ = score_guess(solution, guess)
    if black == CODE_LENGTH:
        return None

    summary = compute_perfectionist_optimality_summary(
        previous_state=previous,
        guess=guess,
    )
    if summary is None or not bool(summary.get("available")):
        retry_state, should_emit = apply_owner_guardrail_to_state(
            previous=previous,
            comment_id=comment_id,
            action="perfectionist_eval_retry",
        )
        return (
            (
                retry_state,
                "Perfectionist check temporarily unavailable. Retry the same guess in a moment.",
                False,
                should_emit,
            ),
            render_perfectionist_retry_markdown(summary),
        )

    if bool(summary.get("is_optimal")):
        return None

    failure_state, _, _, should_emit = apply_command_to_state(
        previous=previous,
        parsed_command=parsed_command,
        comment_id=comment_id,
        solution=solution,
    )
    attempt_no = int(failure_state.get("attempt", int(previous.get("attempt", 0))))
    failure_state["phase"] = "lost"
    failure_state["last_action"] = "guess_perfectionist_fail"
    return (
        (
            failure_state,
            f"Attempt {attempt_no}: non-optimal guess in Perfectionist mode. Room failed.",
            True,
            should_emit,
        ),
        render_perfectionist_failure_markdown(summary),
    )


def apply_owner_guardrail_to_state(
    *,
    previous: dict[str, Any],
    comment_id: int,
    action: str,
) -> tuple[dict[str, Any], bool]:
    next_state = copy.deepcopy(previous)
    processed = list(next_state.get("processed_comment_ids", []))
    if comment_id in processed:
        return previous, False
    processed.append(comment_id)
    next_state["processed_comment_ids"] = processed[-200:]
    next_state["seq"] = int(next_state.get("seq", 0)) + 1
    next_state["updated_at"] = now_iso()
    next_state["last_action"] = action
    return next_state, True
