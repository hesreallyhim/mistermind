"""
Knuth's Mastermind Algorithm (1977)

A pure, functional implementation of Donald Knuth's minimax strategy
for solving Mastermind in 5 or fewer guesses, generalized to arbitrary
peg counts and color counts.

The module is presentation-free — it operates on tuples of integers
(or any hashable type) and returns structured results. Wire it to
any UI layer.

Reference:
    Knuth, D.E. (1977). "The Computer as Master Mind."
    Journal of Recreational Mathematics, 9(1), 1-6.

Usage:
    from mistermind.knuth_optimizer import MastermindSolver

    solver = MastermindSolver(pegs=4, colors=6)
    guess = solver.first_guess()          # (0, 0, 1, 1)
    solver.respond(guess, exact=1, color=2)
    guess = solver.next_guess()           # minimax-optimal
    solver.respond(guess, exact=4, color=0)
    assert solver.solved

    # Or play a full automated game:
    solution = solver.play(secret=(3, 2, 1, 0))
    print(solution.turns, solution.history)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import product

# ─── Core scoring function ─────────────────────────────────────────────


def score(secret: tuple, guess: tuple) -> tuple[int, int]:
    """
    Compute Mastermind feedback for a guess against a secret code.

    Returns:
        (exact, misplaced) where:
        - exact: count of pegs correct in both color and position
        - misplaced: count of pegs correct in color but wrong position

    This is the canonical scoring rule: for each color, the number of
    "matches" is min(count_in_secret, count_in_guess). Exact matches
    are subtracted to get misplaced.

    >>> score((0, 1, 2, 3), (0, 1, 2, 3))
    (4, 0)
    >>> score((0, 0, 1, 1), (1, 1, 0, 0))
    (0, 4)
    >>> score((0, 0, 0, 0), (1, 1, 1, 1))
    (0, 0)
    >>> score((0, 1, 2, 3), (0, 3, 2, 1))
    (2, 2)
    """
    exact = sum(s == g for s, g in zip(secret, guess, strict=False))
    secret_counts = Counter(secret)
    guess_counts = Counter(guess)
    total_color_matches = sum(min(secret_counts[c], guess_counts[c]) for c in secret_counts)
    return (exact, total_color_matches - exact)


# ─── Result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuessResult:
    """A single guess and its feedback."""

    guess: tuple
    exact: int
    misplaced: int
    remaining_before: int  # |S| before this guess was scored
    remaining_after: int  # |S| after filtering


@dataclass(frozen=True)
class GameResult:
    """Complete record of a solved game."""

    secret: tuple
    turns: int
    solved: bool
    history: tuple[GuessResult, ...]


@dataclass(frozen=True)
class SuggestionInfo:
    """Metadata about why a guess was chosen."""

    guess: tuple
    worst_case_bucket: int  # max partition size after this guess
    is_in_remaining: bool  # whether the guess is itself a candidate
    partitions: int  # number of distinct feedback outcomes
    remaining_count: int  # |S| at time of suggestion


@dataclass(frozen=True)
class GuessEvaluation:
    """
    Full evaluation of a player's guess against the optimal set.

    This is the core of the "was my move optimal?" feedback system.
    """

    guess: tuple

    # ── This guess's own stats ──
    worst_case_bucket: int  # max partition size if this guess is played
    expected_remaining: float  # mean partition size (expected |S| after response)
    partitions: int  # number of distinct feedback outcomes produced
    is_in_remaining: bool  # could this guess itself be the secret?
    is_consistent: bool  # is this guess compatible with known feedback?

    # ── Comparison to the optimal set ──
    optimal_worst_case: int  # the best achievable worst-case bucket
    optimal_count: int  # how many guesses achieve that optimum
    optimal_in_S_count: int  # of those, how many are in S (could win now)
    rank: int  # 1 = ties for best, 2 = second-best score, etc.
    total_at_rank: int  # how many guesses share this rank
    percentile: float  # 0.0 = best, 1.0 = worst (among all 1296)

    # ── Human-readable verdict ──
    rating: str  # "optimal", "strong", "good", "decent", "weak", "poor"
    explanation: str  # one-line reason for the rating

    @property
    def is_optimal(self) -> bool:
        """Whether this guess is in the minimax-optimal set."""
        return self.worst_case_bucket == self.optimal_worst_case


# ─── Solver ─────────────────────────────────────────────────────────────


class MastermindSolver:
    """
    Knuth's minimax Mastermind solver.

    Maintains the set S of codes consistent with all feedback so far,
    and selects each guess by minimizing the maximum partition size
    across all possible feedback outcomes.

    Parameters:
        pegs:   number of positions (default 4)
        colors: number of distinct colors (default 6)
        first:  override the opening guess (default: Knuth's choice)

    The solver is stateful — call respond() after each guess, then
    next_guess() to get the next move. Or use play() for a full game.
    """

    def __init__(
        self,
        pegs: int = 4,
        colors: int = 6,
        first: tuple | None = None,
    ):
        self.pegs = pegs
        self.colors = colors

        # All possible codes and all possible feedbacks
        self._all_codes: list[tuple] = list(product(range(colors), repeat=pegs))
        self._all_responses: list[tuple[int, int]] = [
            (exact, misplaced)
            for exact in range(pegs + 1)
            for misplaced in range(pegs + 1 - exact)
            if not (exact == pegs - 1 and misplaced == 1)  # impossible response
        ]

        # S: set of codes still consistent with all feedback
        self._remaining: set[tuple] = set(self._all_codes)

        # History
        self._history: list[GuessResult] = []
        self._solved = False

        # Cache for expensive all-guess scoring
        self._all_scores_cache: list[tuple[tuple, int, float, int, bool]] | None = None
        self._all_scores_cache_key: frozenset[tuple] | None = None

        # First guess: Knuth uses (0,0,1,1) for 4-peg 6-color.
        # Generalized: repeat first two colors across pegs.
        if first is not None:
            self._first = first
        elif pegs >= 2 and colors >= 2:
            half = pegs // 2
            self._first = tuple([0] * half + [1] * (pegs - half))
        else:
            self._first = (0,) * pegs

    # ── Public API ──────────────────────────────────────────────────

    @property
    def remaining(self) -> frozenset[tuple]:
        """The current set of codes consistent with all feedback."""
        return frozenset(self._remaining)

    @property
    def remaining_count(self) -> int:
        return len(self._remaining)

    @property
    def solved(self) -> bool:
        return self._solved

    @property
    def history(self) -> tuple[GuessResult, ...]:
        return tuple(self._history)

    def first_guess(self) -> tuple:
        """Return the opening guess (always the same for a given config)."""
        return self._first

    def respond(self, guess: tuple, exact: int, misplaced: int) -> int:
        """
        Record the codemaker's feedback for a guess.

        Filters the remaining set S to only codes that would produce
        the same (exact, misplaced) feedback against this guess.

        Returns the number of remaining candidates after filtering.
        """
        before = len(self._remaining)
        feedback = (exact, misplaced)

        self._remaining = {code for code in self._remaining if score(code, guess) == feedback}

        # Invalidate evaluation cache (S changed)
        self._all_scores_cache = None
        self._all_scores_cache_key = None

        after = len(self._remaining)
        self._history.append(
            GuessResult(
                guess=guess,
                exact=exact,
                misplaced=misplaced,
                remaining_before=before,
                remaining_after=after,
            )
        )

        if exact == self.pegs:
            self._solved = True

        return after

    def next_guess(self) -> tuple:
        """
        Select the next guess using Knuth's minimax strategy.

        For each candidate guess g (from ALL codes, not just S),
        compute the partition of S by the feedback that g would produce
        against each code in S. The "score" of g is the size of its
        largest partition (worst case). We pick the g with the smallest
        such score. Ties are broken by preferring guesses that are
        themselves in S (so we might win immediately).

        Returns the optimal guess.

        Raises ValueError if the game is already solved or if S is empty.
        """
        if self._solved:
            raise ValueError("Game already solved")
        if not self._remaining:
            raise ValueError("No remaining candidates — contradictory feedback?")

        # If only one candidate left, guess it directly
        if len(self._remaining) == 1:
            return next(iter(self._remaining))

        # If only two candidates, guess either one
        if len(self._remaining) == 2:
            return min(self._remaining)

        return self._minimax_guess().guess

    def next_guess_detailed(self) -> SuggestionInfo:
        """Like next_guess(), but returns full metadata."""
        if len(self._remaining) <= 2:
            g = self.next_guess()
            return SuggestionInfo(
                guess=g,
                worst_case_bucket=1,
                is_in_remaining=g in self._remaining,
                partitions=len(self._remaining),
                remaining_count=len(self._remaining),
            )
        return self._minimax_guess()

    def play(self, secret: tuple) -> GameResult:
        """
        Play a complete game against a known secret code.

        Returns a GameResult with the full history.
        """
        # Reset state
        self._remaining = set(self._all_codes)
        self._history = []
        self._solved = False

        guess = self.first_guess()

        for turn in range(1, self.pegs * self.colors + 1):  # generous upper bound
            exact, misplaced = score(secret, guess)
            self.respond(guess, exact, misplaced)

            if self._solved:
                return GameResult(
                    secret=secret,
                    turns=turn,
                    solved=True,
                    history=tuple(self._history),
                )

            guess = self.next_guess()

        return GameResult(
            secret=secret,
            turns=len(self._history),
            solved=False,
            history=tuple(self._history),
        )

    def reset(self):
        """Reset solver to initial state for a new game."""
        self._remaining = set(self._all_codes)
        self._history = []
        self._solved = False
        self._all_scores_cache = None
        self._all_scores_cache_key = None

    # ── Guess evaluation (player feedback system) ───────────────────

    def score_guess(self, guess: tuple) -> tuple[int, float, int, bool]:
        """
        Score a single guess against the current remaining set S.

        Returns:
            (worst_case, expected_remaining, num_partitions, is_in_S)

        This is the building block — it tells you what would happen
        if you played this guess, without any comparison to alternatives.
        """
        S = self._remaining
        n = len(S)
        if n == 0:
            return (0, 0.0, 0, False)

        partition_sizes: dict[tuple[int, int], int] = {}
        for code in S:
            fb = score(code, guess)
            partition_sizes[fb] = partition_sizes.get(fb, 0) + 1

        worst = max(partition_sizes.values())
        expected = sum(sz * sz for sz in partition_sizes.values()) / n
        return (worst, expected, len(partition_sizes), guess in S)

    def optimal_guesses(self) -> list[tuple]:
        """
        Return the full set of minimax-optimal guesses.

        These are ALL guesses (from the full 1296, not just S) that
        achieve the minimum worst-case partition size. Knuth's algorithm
        picks one from this set; there are typically 1-50+ tied guesses.

        Expensive: O(|all_codes| x |S|). Call sparingly.
        """
        scores = self._score_all_guesses()
        if not scores:
            return []
        best_worst = scores[0][1]
        return [guess for guess, worst, _, _, _ in scores if worst == best_worst]

    def evaluate_guess(self, guess: tuple) -> GuessEvaluation:
        """
        Evaluate a player's guess against the full landscape of alternatives.

        Computes:
        - How this guess performs (worst-case, expected, partitions)
        - How it ranks against all 1296 possible guesses
        - Whether it's in the optimal set
        - A human-readable rating and explanation

        Expensive: requires scoring all 1296 guesses. The result is
        cached for the current state of S, so repeated calls within
        the same turn are cheap.
        """
        S = self._remaining
        n = len(S)

        if n <= 1:
            is_correct = guess in S
            return GuessEvaluation(
                guess=guess,
                worst_case_bucket=1 if is_correct else 0,
                expected_remaining=1.0 if is_correct else 0.0,
                partitions=1 if is_correct else 0,
                is_in_remaining=is_correct,
                is_consistent=is_correct,
                optimal_worst_case=1,
                optimal_count=n,
                optimal_in_S_count=n,
                rank=1 if is_correct else 2,
                total_at_rank=1,
                percentile=0.0 if is_correct else 1.0,
                rating="optimal" if is_correct else "poor",
                explanation="Only one code remains — guess it!"
                if n == 1
                else "No codes remain (contradictory feedback).",
            )

        # Score this guess
        g_worst, g_expected, g_parts, g_in_S = self.score_guess(guess)

        # Score all guesses to build the landscape
        all_scores = self._score_all_guesses()

        # Extract unique worst-case tiers (sorted ascending)
        worst_values = sorted(set(worst for _, worst, _, _, _ in all_scores))
        optimal_worst = worst_values[0]

        # Compute rank and count at each tier
        tier_index = {v: i + 1 for i, v in enumerate(worst_values)}
        rank = tier_index.get(g_worst, len(worst_values) + 1)
        total_at_rank = sum(1 for _, w, _, _, _ in all_scores if w == g_worst)

        # Optimal set stats
        optimal_count = sum(1 for _, w, _, _, _ in all_scores if w == optimal_worst)
        optimal_in_S = sum(1 for g, w, _, _, in_S in all_scores if w == optimal_worst and in_S)

        # Percentile: fraction of guesses this one is better than or equal to
        worse_or_equal = sum(1 for _, w, _, _, _ in all_scores if w >= g_worst)
        percentile = 1.0 - (worse_or_equal / len(all_scores))

        # Is this guess even consistent with known feedback?
        is_consistent = guess in S or g_parts > 0

        # Rating and explanation
        rating, explanation = self._rate_guess(
            g_worst,
            g_expected,
            g_parts,
            g_in_S,
            is_consistent,
            optimal_worst,
            rank,
            len(worst_values),
            n,
        )

        return GuessEvaluation(
            guess=guess,
            worst_case_bucket=g_worst,
            expected_remaining=round(g_expected, 2),
            partitions=g_parts,
            is_in_remaining=g_in_S,
            is_consistent=is_consistent,
            optimal_worst_case=optimal_worst,
            optimal_count=optimal_count,
            optimal_in_S_count=optimal_in_S,
            rank=rank,
            total_at_rank=total_at_rank,
            percentile=round(percentile, 4),
            rating=rating,
            explanation=explanation,
        )

    # ── Internals ───────────────────────────────────────────────────

    def _score_all_guesses(self) -> list[tuple[tuple, int, float, int, bool]]:
        """
        Score every possible guess against current S.

        Returns list of (guess, worst_case, expected, partitions, is_in_S)
        sorted by (worst_case, -is_in_S, -partitions).

        Cached: recomputed only when S changes.
        """
        cache_key = frozenset(self._remaining)
        if self._all_scores_cache is not None and self._all_scores_cache_key == cache_key:
            return self._all_scores_cache

        S = self._remaining
        S_list = list(S)
        n = len(S)
        results: list[tuple[tuple, int, float, int, bool]] = []

        for guess in self._all_codes:
            partition_sizes: dict[tuple[int, int], int] = {}
            for code in S_list:
                fb = score(code, guess)
                partition_sizes[fb] = partition_sizes.get(fb, 0) + 1

            worst = max(partition_sizes.values())
            expected = sum(sz * sz for sz in partition_sizes.values()) / n
            in_S = guess in S
            results.append((guess, worst, expected, len(partition_sizes), in_S))

        # Sort: primary = worst_case ascending,
        #        secondary = prefer in_S (True sorts after False, so negate),
        #        tertiary = more partitions better
        results.sort(key=lambda r: (r[1], not r[4], -r[3]))

        self._all_scores_cache = results
        self._all_scores_cache_key = cache_key
        return results

    @staticmethod
    def _rate_guess(
        worst, expected, parts, in_S, consistent, optimal_worst, rank, total_tiers, n
    ) -> tuple[str, str]:
        """Assign a human-readable rating and explanation."""

        if not consistent:
            return (
                "invalid",
                "This guess produces no information — every color has been eliminated.",
            )

        if worst == optimal_worst:
            if in_S:
                return (
                    "optimal",
                    f"Minimax-optimal AND could be the answer. Worst case leaves {worst} codes.",
                )
            else:
                return (
                    "optimal",
                    f"Minimax-optimal (pure information play — "
                    f"not a candidate itself). Worst case: {worst}.",
                )

        gap = worst - optimal_worst

        if gap == 1:
            reason = f"Worst case {worst} vs optimal {optimal_worst} (+1). Very close to optimal."
            if in_S:
                return ("strong", reason + " Could also be the answer.")
            return ("strong", reason)

        if gap <= 3 or worst <= optimal_worst * 1.5:
            return (
                "good",
                f"Worst case {worst} vs optimal {optimal_worst} "
                f"(+{gap}). Solid guess, slight information loss.",
            )

        if worst <= optimal_worst * 2:
            return (
                "decent",
                f"Worst case {worst} vs optimal {optimal_worst} "
                f"(+{gap}). Workable but leaves more ambiguity.",
            )

        if worst <= optimal_worst * 3:
            return (
                "weak",
                f"Worst case {worst} vs optimal {optimal_worst} "
                f"(+{gap}). Loses significant information.",
            )

        return (
            "poor",
            f"Worst case {worst} vs optimal {optimal_worst} "
            f"(+{gap}). Leaves most of the search space unresolved.",
        )

    def _minimax_guess(self) -> SuggestionInfo:
        """
        Knuth's minimax: find the guess that minimizes the maximum
        partition size across all possible responses.

        Searches all codes as potential guesses (not just S). This is
        crucial for the 5-guess guarantee — the optimal guess may not
        be in S.

        Optimization: early termination when we find a guess whose
        worst-case bucket == ceil(|S| / num_possible_responses), since
        that's the theoretical lower bound.
        """
        S = self._remaining
        S_list = list(S)
        n = len(S)

        # Theoretical lower bound: best possible worst case
        # (perfect partitioning across all distinct responses)
        num_responses = len(self._all_responses)
        lower_bound = -(-n // num_responses)  # ceil division

        best_worst = n + 1
        best_guess: tuple | None = None
        best_partitions = 0
        best_in_S = False

        for guess in self._all_codes:
            # Count partition sizes using a dict
            partition_sizes: dict[tuple[int, int], int] = {}
            worst_so_far = 0
            for code in S_list:
                fb = score(code, guess)
                cnt = partition_sizes.get(fb, 0) + 1
                partition_sizes[fb] = cnt
                if cnt > worst_so_far:
                    worst_so_far = cnt

                # Early exit: this guess already worse than best known
                if worst_so_far > best_worst:
                    break
            else:
                # Loop completed without break — this guess is competitive
                worst = worst_so_far
                in_S = guess in S
                num_partitions = len(partition_sizes)

                if (
                    worst < best_worst
                    or (worst == best_worst and in_S and not best_in_S)
                    or (
                        worst == best_worst
                        and in_S == best_in_S
                        and num_partitions > best_partitions
                    )
                ):
                    best_worst = worst
                    best_guess = guess
                    best_partitions = num_partitions
                    best_in_S = in_S

                    # Hit theoretical optimum — can't do better
                    if best_worst <= lower_bound:
                        break

        if best_guess is None:
            raise ValueError("No valid guess could be selected from the code space.")

        return SuggestionInfo(
            guess=best_guess,
            worst_case_bucket=best_worst,
            is_in_remaining=best_in_S,
            partitions=best_partitions,
            remaining_count=n,
        )


# ─── Convenience functions for analysis ─────────────────────────────────


def worst_case_depth(
    solver: MastermindSolver | None = None, pegs: int = 4, colors: int = 6
) -> dict:
    """
    Play all possible secrets and report worst/average case statistics.

    Returns dict with keys:
        max_turns, avg_turns, distribution (turns → count),
        total_games, hardest_codes
    """
    if solver is None:
        solver = MastermindSolver(pegs=pegs, colors=colors)

    all_codes = list(product(range(colors), repeat=pegs))
    distribution: dict[int, int] = {}
    total_turns = 0
    max_turns = 0
    hardest: list[tuple] = []

    for secret in all_codes:
        solver.reset()
        result = solver.play(secret)
        t = result.turns

        distribution[t] = distribution.get(t, 0) + 1
        total_turns += t

        if t > max_turns:
            max_turns = t
            hardest = [secret]
        elif t == max_turns:
            hardest.append(secret)

    return {
        "max_turns": max_turns,
        "avg_turns": total_turns / len(all_codes),
        "distribution": dict(sorted(distribution.items())),
        "total_games": len(all_codes),
        "hardest_codes": hardest[:10],  # cap for display
    }


# ─── CLI demo ───────────────────────────────────────────────────────────


def _demo():
    import time

    _COLOR_NAMES = ["red", "yellow", "green", "blue", "purple", "orange"]
    EMOJI = ["🔴", "🟡", "🟢", "🔵", "🟣", "🟠"]

    def fmt(code):
        return " ".join(EMOJI[c] for c in code)

    print("=" * 62)
    print("  KNUTH'S MASTERMIND ALGORITHM (1977)")
    print("  4 pegs, 6 colors — guaranteed ≤5 guesses")
    print("=" * 62)

    solver = MastermindSolver(pegs=4, colors=6)
    secret = (5, 2, 1, 3)  # orange green yellow blue

    print(f"\n  Secret: {fmt(secret)}")
    print(f"  Search space: {solver.remaining_count} codes\n")

    guess = solver.first_guess()
    for turn in range(1, 12):
        exact, misplaced = score(secret, guess)
        before = solver.remaining_count
        solver.respond(guess, exact, misplaced)
        after = solver.remaining_count

        info = f"  Turn {turn}: {fmt(guess)}  →  {exact}● {misplaced}○"
        info += f"  │ {before} → {after} remaining"
        print(info)

        if solver.solved:
            print(f"\n  ✅ Solved in {turn} turns!")
            break

        suggestion = solver.next_guess_detailed()
        guess = suggestion.guess

    # Full benchmark
    print(f"\n{'─' * 62}")
    print("  Running sample benchmark (100 random secrets)...", end=" ", flush=True)
    t0 = time.time()

    import random as _rnd

    _rnd.seed(42)
    sample = _rnd.sample(list(product(range(6), repeat=4)), 100)

    distribution: dict[int, int] = {}
    max_turns = 0
    total = 0

    for sampled_secret in sample:
        s = MastermindSolver(pegs=4, colors=6)
        result = s.play(sampled_secret)
        t = result.turns
        distribution[t] = distribution.get(t, 0) + 1
        total += t
        if t > max_turns:
            max_turns = t

    elapsed = time.time() - t0
    print(f"done in {elapsed:.1f}s\n")

    print(f"  Worst case:  {max_turns} guesses (Knuth guarantees ≤5)")
    print(f"  Average:     {total / len(sample):.3f} guesses")
    print(f"  Distribution (n={len(sample)}):")
    for turns, count in sorted(distribution.items()):
        bar = "█" * count
        pct = count / len(sample) * 100
        print(f"    {turns} guesses: {count:3d} ({pct:5.1f}%)  {bar}")


if __name__ == "__main__":
    _demo()
