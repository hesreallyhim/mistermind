"""
Guess scoring, candidate-space reduction, and solver integration.

Contains the core MisterMind scoring algorithm, numeric-code helpers for
the hint/optimizer engines, and the computation functions for deductive
hints and perfectionist-mode optimality checks.
"""

from __future__ import annotations

from typing import Any

from mistermind.constants import (
    ALL_NUMERIC_CODES,
    CODE_LENGTH,
    COLOR_EMOJI,
    COLOR_TO_INDEX,
    INDEX_TO_COLOR,
    PALETTE,
)
from mistermind.hints import HintEngine
from mistermind.knuth_optimizer import MastermindSolver

# ── Core scoring ─────────────────────────────────────────────────────


def score_guess(secret: list[str], guess: list[str]) -> tuple[int, int]:
    black = 0
    secret_counts: dict[str, int] = {}
    guess_counts: dict[str, int] = {}

    for s_color, g_color in zip(secret, guess, strict=False):
        if s_color == g_color:
            black += 1
            continue
        secret_counts[s_color] = secret_counts.get(s_color, 0) + 1
        guess_counts[g_color] = guess_counts.get(g_color, 0) + 1

    white = 0
    for color, count in guess_counts.items():
        white += min(count, secret_counts.get(color, 0))

    return black, white


def _score_guess_numeric(secret: tuple[int, ...], guess: tuple[int, ...]) -> tuple[int, int]:
    black = 0
    secret_counts: dict[int, int] = {}
    guess_counts: dict[int, int] = {}

    for s_color, g_color in zip(secret, guess, strict=False):
        if s_color == g_color:
            black += 1
            continue
        secret_counts[s_color] = secret_counts.get(s_color, 0) + 1
        guess_counts[g_color] = guess_counts.get(g_color, 0) + 1

    white = 0
    for color, count in guess_counts.items():
        white += min(count, secret_counts.get(color, 0))

    return black, white


# ── Numeric-code helpers ─────────────────────────────────────────────


def _history_entry_to_numeric_guess(entry: dict[str, Any]) -> tuple[int, ...] | None:
    guess = entry.get("guess")
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH:
        return None

    try:
        return tuple(COLOR_TO_INDEX[color] for color in guess)
    except KeyError:
        return None


def _remaining_candidates_from_history(history: list[dict[str, Any]]) -> set[tuple[int, ...]]:
    candidates = set(ALL_NUMERIC_CODES)

    for entry in history:
        guess_numeric = _history_entry_to_numeric_guess(entry)
        if guess_numeric is None:
            continue

        black = int(entry.get("black", 0))
        white = int(entry.get("white", 0))

        candidates = {
            code
            for code in candidates
            if _score_guess_numeric(code, guess_numeric) == (black, white)
        }
        if not candidates:
            break

    return candidates


# ── Solver view (bridge for HintEngine) ──────────────────────────────


class _HintSolverView:
    """Minimal solver view expected by HintEngine."""

    def __init__(self, remaining: set[tuple[int, ...]]) -> None:
        self._remaining = frozenset(remaining)
        self.pegs = CODE_LENGTH
        self.colors = len(PALETTE)

    @property
    def remaining(self) -> frozenset[tuple[int, ...]]:
        return self._remaining


# ── Dynamic engine loaders ───────────────────────────────────────────

_HINT_ENGINE_CLASS: Any | None = None
_HINT_ENGINE_LOAD_ERROR: str | None = None
_OPTIMIZER_SOLVER_CLASS: Any | None = None
_OPTIMIZER_SOLVER_LOAD_ERROR: str | None = None


def _load_hint_engine_class() -> Any | None:
    global _HINT_ENGINE_CLASS, _HINT_ENGINE_LOAD_ERROR

    if _HINT_ENGINE_CLASS is not None:
        return _HINT_ENGINE_CLASS
    if _HINT_ENGINE_LOAD_ERROR is not None:
        return None

    try:
        _HINT_ENGINE_CLASS = HintEngine
        return _HINT_ENGINE_CLASS
    except Exception as exc:
        _HINT_ENGINE_LOAD_ERROR = str(exc)
        print(f"Hint engine unavailable: {_HINT_ENGINE_LOAD_ERROR}")
        return None


def _load_optimizer_solver_class() -> Any | None:
    global _OPTIMIZER_SOLVER_CLASS, _OPTIMIZER_SOLVER_LOAD_ERROR

    if _OPTIMIZER_SOLVER_CLASS is not None:
        return _OPTIMIZER_SOLVER_CLASS
    if _OPTIMIZER_SOLVER_LOAD_ERROR is not None:
        return None

    try:
        _OPTIMIZER_SOLVER_CLASS = MastermindSolver
        return _OPTIMIZER_SOLVER_CLASS
    except Exception as exc:
        _OPTIMIZER_SOLVER_LOAD_ERROR = str(exc)
        print(f"Perfectionist optimizer unavailable: {_OPTIMIZER_SOLVER_LOAD_ERROR}")
        return None


# ── Perfectionist optimality ─────────────────────────────────────────


def compute_perfectionist_optimality_summary(
    *,
    previous_state: dict[str, Any],
    guess: list[str],
) -> dict[str, Any] | None:
    """Evaluate whether a guess is minimax-optimal for the current state.

    Evaluation is always performed against the *previous* state, before
    applying this guess's feedback.
    """
    solver_class = _load_optimizer_solver_class()
    if solver_class is None:
        return {
            "available": False,
            "error": _OPTIMIZER_SOLVER_LOAD_ERROR or "optimizer unavailable",
        }

    history = previous_state.get("history", [])
    if not isinstance(history, list):
        history = []

    try:
        guess_numeric = tuple(COLOR_TO_INDEX[color] for color in guess)
    except KeyError:
        return None

    try:
        solver = solver_class(pegs=CODE_LENGTH, colors=len(PALETTE))
        for entry in history:
            guess_from_history = _history_entry_to_numeric_guess(entry)
            if guess_from_history is None:
                continue
            solver.respond(
                guess_from_history,
                int(entry.get("black", 0)),
                int(entry.get("white", 0)),
            )

        evaluation = solver.evaluate_guess(guess_numeric)
        suggested_optimal: tuple[int, ...] | None = None
        if not bool(getattr(evaluation, "is_optimal", False)):
            try:
                suggested_optimal = solver.next_guess()
            except Exception:
                suggested_optimal = None
    except Exception as exc:
        print(f"Perfectionist evaluation failed: {exc}")
        return {
            "available": False,
            "error": str(exc),
        }

    return {
        "available": True,
        "is_optimal": bool(getattr(evaluation, "is_optimal", False)),
        "rating": str(getattr(evaluation, "rating", "")),
        "explanation": str(getattr(evaluation, "explanation", "")),
        "worst_case_bucket": int(getattr(evaluation, "worst_case_bucket", 0)),
        "optimal_worst_case": int(getattr(evaluation, "optimal_worst_case", 0)),
        "rank": int(getattr(evaluation, "rank", 0)),
        "total_at_rank": int(getattr(evaluation, "total_at_rank", 0)),
        "optimal_count": int(getattr(evaluation, "optimal_count", 0)),
        "suggested_optimal_guess": (
            [INDEX_TO_COLOR[idx] for idx in suggested_optimal]
            if suggested_optimal is not None
            else None
        ),
    }


def render_perfectionist_failure_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "### Perfectionist Verdict",
        "",
        f"- `rating`: **{summary.get('rating', 'unknown')}**",
        "- `result`: this move is **not in the minimax-optimal class**.",
        (
            f"- `worst_case`: `{summary.get('worst_case_bucket', '?')}` "
            f"(optimal is `{summary.get('optimal_worst_case', '?')}`)"
        ),
        (
            f"- `rank`: `{summary.get('rank', '?')}` of "
            f"`{summary.get('total_at_rank', '?')}` at this tier "
            f"(`{summary.get('optimal_count', '?')}` optimal moves total)"
        ),
    ]

    explanation = str(summary.get("explanation", "")).strip()
    if explanation:
        lines.append(f"- `why`: {explanation}")

    suggested = summary.get("suggested_optimal_guess")
    if isinstance(suggested, list) and len(suggested) == CODE_LENGTH:
        suggested_str = " ".join(f"{COLOR_EMOJI.get(color, '◉')} `{color}`" for color in suggested)
        lines.append(f"- `example optimal move`: {suggested_str}")

    return "\n".join(lines)


def render_perfectionist_retry_markdown(summary: dict[str, Any] | None) -> str:
    error = ""
    if isinstance(summary, dict):
        error = str(summary.get("error", "")).strip()
    detail = f" (`{error}`)" if error else ""
    return "\n".join(
        [
            "### Perfectionist Check",
            "",
            (
                "> The Knuth optimality evaluator is temporarily unavailable"
                f"{detail}. No turn was consumed."
            ),
            "> Please retry the same guess in a moment.",
        ]
    )


# ── Deductive hints ─────────────────────────────────────────────────


def compute_deductive_hint_summary(
    *,
    previous_state: dict[str, Any],
    guess: list[str],
) -> dict[str, Any] | None:
    """Compute per-peg impossible/certain deductions for a guess.

    The deduction space is computed from the *previous* state history,
    which matches "hint as advice from known information before this
    guess was processed".
    """
    hint_engine_class = _load_hint_engine_class()
    if hint_engine_class is None:
        return None

    history = previous_state.get("history", [])
    if not isinstance(history, list):
        history = []

    try:
        guess_numeric = tuple(COLOR_TO_INDEX[color] for color in guess)
    except KeyError:
        return None

    remaining = _remaining_candidates_from_history(history)
    solver_view = _HintSolverView(remaining)

    try:
        report = hint_engine_class(solver_view).analyze(guess_numeric, level=1)
    except Exception as exc:
        print(f"Hint analysis failed: {exc}")
        return None

    impossible: list[dict[str, Any]] = []
    certain: list[dict[str, Any]] = []

    for peg in getattr(report, "pegs", ()):
        status_obj = getattr(peg, "status", None)
        status = str(getattr(status_obj, "value", ""))
        pos = int(getattr(peg, "position", 0)) + 1
        color_idx = int(getattr(peg, "color", -1))
        color = INDEX_TO_COLOR.get(color_idx, str(color_idx))
        reason = str(getattr(peg, "reason", ""))
        detail = {
            "position": pos,
            "color": color,
            "reason": reason,
        }
        if status == "impossible":
            impossible.append(detail)
        elif status == "certain":
            certain.append(detail)

    return {
        "remaining_count": int(getattr(report, "remaining_count", len(remaining))),
        "impossible": impossible,
        "certain": certain,
        "has_hints": bool(impossible or certain),
    }


def render_deductive_hints_markdown(summary: dict[str, Any]) -> str:
    remaining = int(summary.get("remaining_count", 0))
    impossible = summary.get("impossible", [])
    certain = summary.get("certain", [])

    lines = [
        "### Deductive Hints",
        "",
        f"> Based on prior state: **{remaining}** candidate code"
        f"{'' if remaining == 1 else 's'} remained before this guess.",
        "",
    ]

    if certain:
        lines.append("**Locked pegs (provably correct):**")
        for item in certain:
            pos = int(item.get("position", 0))
            color = str(item.get("color", ""))
            emoji = COLOR_EMOJI.get(color, "◉")
            lines.append(f"- `P{pos}` {emoji} `{color}` is guaranteed correct.")
        lines.append("")

    if impossible:
        lines.append("**Ruled-out placements (provably impossible):**")
        for item in impossible:
            pos = int(item.get("position", 0))
            color = str(item.get("color", ""))
            emoji = COLOR_EMOJI.get(color, "◉")
            lines.append(f"- `P{pos}` {emoji} `{color}` cannot be correct there.")
        lines.append("")

    if not certain and not impossible:
        lines.append("_No forced peg locks/eliminations are provable yet._")

    return "\n".join(lines)
