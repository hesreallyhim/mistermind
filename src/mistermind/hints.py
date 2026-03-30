"""
MasterMind Hint System

Analyzes a proposed guess against the current possibility space and
produces per-peg feedback BEFORE the guess is submitted. This gives
players opt-in assistance without revealing the answer.

Three hint levels:
    Level 1 ("flags"):   Flag provably impossible placements.
    Level 2 ("nudges"):  Flag impossibles + highlight high-confidence pegs.
    Level 3 ("coach"):   Full analysis with swap suggestions and optimal
                         alternative.

The module is presentation-free. It returns structured HintReport
objects — wire them to any UI (SVG, Markdown, terminal, etc.).

Integration:
    from mistermind.knuth_optimizer import MastermindSolver, score
    from mistermind.hints import HintEngine

    solver = MastermindSolver()
    solver.respond((0,0,1,1), exact=1, misplaced=0)

    hints = HintEngine(solver)
    report = hints.analyze((2, 3, 0, 5), level=2)

    for peg in report.pegs:
        print(peg.position, peg.color, peg.status, peg.reason)

    if report.has_impossibles:
        print("This guess has provably wrong placements!")
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mistermind.knuth_optimizer import MastermindSolver


# ─── Types ──────────────────────────────────────────────────────────────


class PegStatus(Enum):
    """Status of a single peg in a proposed guess."""

    IMPOSSIBLE = "impossible"  # provably wrong (probability = 0)
    UNLIKELY = "unlikely"  # low probability (< 10%)
    PLAUSIBLE = "plausible"  # moderate probability (10-50%)
    LIKELY = "likely"  # high probability (50-90%)
    CERTAIN = "certain"  # provably correct (probability = 100%)
    UNKNOWN = "unknown"  # can't determine (no remaining set)


@dataclass(frozen=True)
class PegHint:
    """Analysis of a single peg placement."""

    position: int  # 0-indexed
    color: int  # color id placed here
    status: PegStatus
    probability: float  # exact probability this color is at this position
    reason: str  # human-readable explanation

    # Extra detail for actionable feedback
    best_color: int  # highest-probability color for this position
    best_probability: float  # its probability
    color_in_solution: float  # probability this color is ANYWHERE in solution
    color_min_count: int  # minimum guaranteed count of this color
    color_max_count: int  # maximum possible count of this color


@dataclass(frozen=True)
class SwapSuggestion:
    """A suggested swap of two pegs that would improve the guess."""

    pos_a: int
    pos_b: int
    improvement: str  # what this fixes


@dataclass(frozen=True)
class HintReport:
    """Complete hint analysis for a proposed guess."""

    guess: tuple
    level: int  # 1, 2, or 3

    # Per-peg analysis
    pegs: tuple[PegHint, ...]

    # Aggregate flags
    has_impossibles: bool  # any peg provably wrong?
    impossible_count: int  # how many pegs are impossible
    certain_count: int  # how many pegs are provably correct
    guess_is_candidate: bool  # is this exact code in S?
    remaining_count: int  # |S| for context

    # Level 2+: position-level best picks
    best_per_position: tuple  # tuple of (color, probability) per position

    # Level 3: swap suggestions and optimal comparison
    swaps: tuple[SwapSuggestion, ...]  # suggested swaps
    optimal_guess: tuple | None  # a minimax-optimal alternative
    optimal_worst_case: int | None  # its worst-case bucket
    guess_worst_case: int | None  # this guess's worst-case bucket
    guess_rating: str | None  # from knuth.evaluate_guess


# ─── Engine ─────────────────────────────────────────────────────────────


class HintEngine:
    """
    Produces hints for a proposed guess given the current solver state.

    Operates on the solver's remaining set S to compute exact
    probabilities — no heuristics, no approximation.
    """

    def __init__(self, solver: MastermindSolver):
        self._solver = solver
        self._S = solver.remaining
        self._n = len(self._S)
        self._pegs = solver.pegs
        self._colors = solver.colors

        # Precompute the full probability landscape once
        self._pos_probs = self._compute_position_probs()
        self._color_counts = self._compute_color_counts()

    def analyze(self, guess: tuple, level: int = 2) -> HintReport:
        """
        Analyze a proposed guess at the given hint level.

        Level 1: Only flag impossible placements.
        Level 2: Impossibles + highlight likely/certain pegs + best picks.
        Level 3: Everything + swap suggestions + optimal comparison.
        """
        level = max(1, min(3, level))

        peg_hints = []
        for pos in range(self._pegs):
            color = guess[pos]
            peg_hints.append(self._analyze_peg(pos, color, level))

        impossibles = [p for p in peg_hints if p.status == PegStatus.IMPOSSIBLE]
        certains = [p for p in peg_hints if p.status == PegStatus.CERTAIN]

        # Best color per position (level 2+)
        best_per_pos = (
            tuple(
                max(self._pos_probs[pos].items(), key=lambda kv: kv[1]) for pos in range(self._pegs)
            )
            if level >= 2
            else tuple((0, 0.0) for _ in range(self._pegs))
        )

        # Level 3: swaps and optimal comparison
        swaps: tuple[SwapSuggestion, ...] = ()
        optimal_guess = None
        optimal_wc = None
        guess_wc = None
        guess_rating = None

        if level >= 3:
            swaps = tuple(self._find_swaps(guess, peg_hints))
            try:
                ev = self._solver.evaluate_guess(guess)
                guess_wc = ev.worst_case_bucket
                optimal_wc = ev.optimal_worst_case
                guess_rating = ev.rating
                if not ev.is_optimal:
                    optimal_guess = self._solver.next_guess()
            except (ValueError, AttributeError):
                pass

        return HintReport(
            guess=guess,
            level=level,
            pegs=tuple(peg_hints),
            has_impossibles=len(impossibles) > 0,
            impossible_count=len(impossibles),
            certain_count=len(certains),
            guess_is_candidate=guess in self._S,
            remaining_count=self._n,
            best_per_position=best_per_pos,
            swaps=swaps,
            optimal_guess=optimal_guess,
            optimal_worst_case=optimal_wc,
            guess_worst_case=guess_wc,
            guess_rating=guess_rating,
        )

    # ── Per-peg analysis ────────────────────────────────────────────

    def _analyze_peg(self, pos: int, color: int, level: int) -> PegHint:
        prob = self._pos_probs[pos].get(color, 0.0)
        c_min = self._color_counts[color]["min"]
        c_max = self._color_counts[color]["max"]
        color_anywhere = self._color_counts[color]["present_prob"]

        # Best alternative for this position
        best_color, best_prob = max(self._pos_probs[pos].items(), key=lambda kv: kv[1])

        # Determine status
        if prob == 0.0:
            status = PegStatus.IMPOSSIBLE
            if c_max == 0:
                reason = f"Color {color} is eliminated — not in solution."
            else:
                reason = f"Color {color} appears in the solution but never at position {pos + 1}."
        elif prob == 1.0:
            status = PegStatus.CERTAIN
            reason = f"Position {pos + 1} is locked — must be color {color}."
        elif level < 2:
            # Level 1 only cares about impossible/not-impossible
            status = PegStatus.PLAUSIBLE
            reason = f"Consistent with remaining codes ({prob:.0%})."
        elif prob >= 0.5:
            status = PegStatus.LIKELY
            reason = f"{prob:.0%} of remaining codes have color {color} here."
        elif prob >= 0.1:
            status = PegStatus.PLAUSIBLE
            reason = f"Possible but not strong — {prob:.0%} probability."
        else:
            status = PegStatus.UNLIKELY
            reason = (
                f"Only {prob:.1%} of remaining codes. Color {best_color} is {best_prob:.0%} here."
            )

        return PegHint(
            position=pos,
            color=color,
            status=status,
            probability=round(prob, 4),
            reason=reason,
            best_color=best_color,
            best_probability=round(best_prob, 4),
            color_in_solution=round(color_anywhere, 4),
            color_min_count=c_min,
            color_max_count=c_max,
        )

    # ── Swap suggestions ────────────────────────────────────────────

    def _find_swaps(self, guess: tuple, peg_hints: list[PegHint]) -> list[SwapSuggestion]:
        """
        Find pairs of pegs where swapping would fix impossible placements.

        A swap is suggested when:
          - Peg A's color is impossible at A's position but plausible at B's
          - Peg B's color is impossible at B's position but plausible at A's
          - (Both improve, neither gets worse)
        """
        swaps = []
        n = self._pegs
        for i in range(n):
            for j in range(i + 1, n):
                ci, cj = guess[i], guess[j]
                if ci == cj:
                    continue

                # Current probabilities
                pi_at_i = self._pos_probs[i].get(ci, 0.0)
                pj_at_j = self._pos_probs[j].get(cj, 0.0)

                # After swap
                pi_at_j = self._pos_probs[j].get(ci, 0.0)
                pj_at_i = self._pos_probs[i].get(cj, 0.0)

                # Only suggest if the swap strictly improves at least one
                # peg and doesn't worsen the other
                before = min(pi_at_i, pj_at_j)
                after = min(pi_at_j, pj_at_i)

                if after <= before:
                    continue

                # Characterize the improvement
                fixes = []
                if pi_at_i == 0 and pi_at_j > 0:
                    fixes.append(f"color {ci} at pos {j + 1} is {pi_at_j:.0%}")
                if pj_at_j == 0 and pj_at_i > 0:
                    fixes.append(f"color {cj} at pos {i + 1} is {pj_at_i:.0%}")
                if not fixes:
                    fixes.append("improves both positions")

                swaps.append(
                    SwapSuggestion(
                        pos_a=i,
                        pos_b=j,
                        improvement=" + ".join(fixes),
                    )
                )

        return swaps

    # ── Precomputation ──────────────────────────────────────────────

    def _compute_position_probs(self) -> list[dict[int, float]]:
        """P(color c at position p) for all c, p."""
        if self._n == 0:
            return [{} for _ in range(self._pegs)]

        probs = []
        for pos in range(self._pegs):
            counts = Counter(code[pos] for code in self._S)
            probs.append({c: counts.get(c, 0) / self._n for c in range(self._colors)})
        return probs

    def _compute_color_counts(self) -> dict[int, dict]:
        """For each color: min/max count in solution, probability of presence."""
        result = {}
        for color in range(self._colors):
            if self._n == 0:
                result[color] = {"min": 0, "max": 0, "present_prob": 0.0}
                continue

            counts = [sum(1 for c in code if c == color) for code in self._S]
            present = sum(1 for c in counts if c > 0)
            result[color] = {
                "min": min(counts),
                "max": max(counts),
                "present_prob": present / self._n,
            }
        return result


# ─── Formatting helpers (presentation-layer examples) ───────────────────


def format_hint_markdown(
    report: HintReport, color_names: list[str] | None = None, emoji: list[str] | None = None
) -> str:
    """
    Format a HintReport as GitHub-compatible Markdown.

    This is a reference implementation — replace with your own
    presentation layer as needed.
    """
    if color_names is None:
        color_names = ["red", "yellow", "green", "blue", "purple", "orange"]
    if emoji is None:
        emoji = ["🔴", "🟡", "🟢", "🔵", "🟣", "🟠"]

    def cn(c):
        return color_names[c] if c < len(color_names) else str(c)

    def ce(c):
        return emoji[c] if c < len(emoji) else f"[{c}]"

    lines = []
    guess_str = " ".join(ce(c) for c in report.guess)

    # Header
    if report.level == 1:
        label = "Flag Check"
    elif report.level == 2:
        label = "Hint"
    else:
        label = "Coach"

    lines.append("<details>")
    lines.append(
        f"<summary>💡 <b>{label}</b> for {guess_str} "
        f"({report.remaining_count} codes remaining)</summary>"
    )
    lines.append("")

    # Per-peg analysis
    STATUS_ICON = {
        PegStatus.IMPOSSIBLE: "🚫",
        PegStatus.UNLIKELY: "⚠️",
        PegStatus.PLAUSIBLE: "🔹",
        PegStatus.LIKELY: "✅",
        PegStatus.CERTAIN: "🔒",
        PegStatus.UNKNOWN: "❓",
    }

    lines.append("| Pos | Peg | Status | Probability | Detail |")
    lines.append("|:---:|:---:|:------:|:-----------:|--------|")

    for peg in report.pegs:
        icon = STATUS_ICON.get(peg.status, "·")
        prob_str = f"{peg.probability:.0%}" if peg.probability > 0 else "—"
        status_str = peg.status.value

        detail = peg.reason
        if report.level >= 2 and peg.status in (PegStatus.IMPOSSIBLE, PegStatus.UNLIKELY):
            detail += f" Best here: {ce(peg.best_color)} ({peg.best_probability:.0%})"

        lines.append(
            f"| {peg.position + 1} | {ce(peg.color)} | "
            f"{icon} {status_str} | {prob_str} | {detail} |"
        )

    lines.append("")

    # Summary
    if report.has_impossibles:
        lines.append(
            f"> ⚠️ **{report.impossible_count} placement(s) "
            f"are provably wrong.** Consider changing them before guessing."
        )
        lines.append("")

    if report.certain_count > 0:
        lines.append(
            f"> 🔒 **{report.certain_count} placement(s) are locked in.** Don't change those!"
        )
        lines.append("")

    if not report.guess_is_candidate and report.remaining_count > 0:
        lines.append(
            "> This exact guess is not in the remaining candidate set — it cannot be the answer."
        )
        lines.append("")

    # Level 2+: best picks per position
    if report.level >= 2:
        best_str = " ".join(
            f"P{i + 1}={ce(c)}({p:.0%})" for i, (c, p) in enumerate(report.best_per_position)
        )
        lines.append(f"**Best per position:** {best_str}")
        lines.append("")

    # Level 3: swaps and comparison
    if report.level >= 3:
        if report.swaps:
            lines.append("**Suggested swaps:**")
            for swap in report.swaps:
                ci = report.guess[swap.pos_a]
                cj = report.guess[swap.pos_b]
                lines.append(
                    f"- Swap P{swap.pos_a + 1} ({ce(ci)}) ↔ P{swap.pos_b + 1} ({ce(cj)})"
                    f": {swap.improvement}"
                )
            lines.append("")

        if report.guess_rating:
            lines.append(
                f"**Minimax rating:** {report.guess_rating.upper()}"
                f" (worst-case {report.guess_worst_case}"
                f" vs optimal {report.optimal_worst_case})"
            )
            if report.optimal_guess:
                opt_str = " ".join(ce(c) for c in report.optimal_guess)
                lines.append(f"**Optimal alternative:** {opt_str}")
            lines.append("")

    lines.append("</details>")
    return "\n".join(lines)


def format_hint_compact(report: HintReport, emoji: list[str] | None = None) -> str:
    """One-line summary suitable for inline display."""
    if emoji is None:
        emoji = ["🔴", "🟡", "🟢", "🔵", "🟣", "🟠"]

    def ce(c):
        return emoji[c] if c < len(emoji) else f"[{c}]"

    STATUS_CHAR = {
        PegStatus.IMPOSSIBLE: "🚫",
        PegStatus.UNLIKELY: "⚠️",
        PegStatus.PLAUSIBLE: "·",
        PegStatus.LIKELY: "✓",
        PegStatus.CERTAIN: "🔒",
    }

    peg_strs = []
    for peg in report.pegs:
        icon = STATUS_CHAR.get(peg.status, "·")
        peg_strs.append(f"{ce(peg.color)}{icon}")

    parts = [" ".join(peg_strs)]
    if report.has_impossibles:
        parts.append(f"{report.impossible_count} impossible")
    if report.certain_count:
        parts.append(f"{report.certain_count} locked")

    return " │ ".join(parts)
