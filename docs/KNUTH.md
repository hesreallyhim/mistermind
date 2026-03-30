# Knuth-mode

In 1977, Donald Knuth, a true mastermind, published a [short essay](https://cs.stanford.edu/~knuth/mm.txt) showing that classic Mastermind can always be solved in five guesses or fewer. The key idea is to treat each guess as an information-gathering experiment, not just an attempt to name the hidden code. In this repo, "Knuth-mode" tests that discipline: any guess outside the minimax-optimal set causes an immediate loss.

The algorithm can be summarized as follows:

1. Start with the full code space: in classic Mastermind, that is `6^4 = 1296` possible secrets. Knuth's opening guess is `0011` (two pegs of one color, then two of another).
2. After each reply, keep only the codes that would have produced exactly that same feedback against your guess. Those surviving codes are the remaining candidates.
3. To score a possible next guess `g`, pretend `g` is played against every remaining candidate. This splits the candidate set into buckets, one bucket per possible feedback result `(exact, misplaced)`.
4. Look at the largest bucket. That is the worst case for `g`, because the true secret could be in the most ambiguous bucket and you would still have that many possibilities left.
5. Choose a guess whose largest bucket is as small as possible. This is the minimax step: minimize the maximum number of candidates that could survive.
6. The minimax-optimal set is the full set of guesses that achieve that smallest worst-case bucket. When the solver needs one representative move, it prefers a guess that is itself still a valid candidate, since it might solve the game immediately.

One subtle but important point: the best probe is sometimes a code that is already known not to be the answer. Knuth still allows that guess, because its value comes from how sharply it partitions the remaining candidates. So in Knuth-mode, "optimal" means "best worst-case information gain," not merely "still possible secret."
