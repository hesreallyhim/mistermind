<div align="center">

# 🔴🟠🟡🔵 &nbsp; MisterMind &nbsp; 🟢🟣🔵🔴

<br>

<img src="https://img.shields.io/badge/Players-1-blue?style=for-the-badge" alt="Players: 1" />
<img src="https://img.shields.io/badge/Turns-10-red?style=for-the-badge" alt="Turns: 10" />
<img src="https://img.shields.io/badge/Code_Length-4-green?style=for-the-badge" alt="Code Length: 4" />
<img src="https://img.shields.io/badge/Colors-6-purple?style=for-the-badge" alt="Colors: 6" />

---

</div>

<div align="center">

# How to Play MisterMind

<br>

> [!INFO]
> _DID YOU KNOW?_<br>
> _MisterMind is one of the earliest board-and-peg-based games known to animal civilization. It was invented by Alan Turing during the 0th World War and was a big inspiration for the Church-Turing thesis. It was even made into a Hollywood movie called "Mister Beautiful Mind"._

<br>

</div>

---



## Rules of the Game

Deduce the **hidden code**
- 6 possible colors 🔴🟠🟡🟢🔵🟣
- 4 colored pegs in a specific order 🟡🟢🔵🟣
- The solution may reuse colors 🟡🟡🟡🟢
- 10 chances to guess the exact order
- After each guess, you will receive a response with information about how accurate the guess was
- The nature of this feedback varies by game mode, and the presentation differs by gameboard style
- Feedback gives aggregate counts only and no information about _which specific pegs_ are correct

<br>

## How to Play

- Open an issue to start your own game - no other users can affect your game
- To make a guess, just post a comment:
  - `red green blue blue`
  - `o y r p`
  - `bbgy`

Each turn follows two steps:

### Step 1 — You Guess

Submit a sequence of **4 colors** as your guess.

### Step 2 — You Get Feedback

1. Classic Mode

MisterMind responds with three count rows:
- **Color and position**
- **Color only**
- **Neither color nor position**

These are aggregate counts only. They do not indicate which specific peg is in each category.

2. Hint Mode

Generally, MisterMind works by starting with no information, and therefore all possible solutions are open, and reducing the number of possible solutions through this feedback cycle. As you play, it may emerge that based on the current state of the game, a certain color in a certain position is _guaranteed_ to be correct - alternatively, it may be that color X in position Y is _impossible_.

Hint Mode helps you by visually indicating when these conditions apply.

3. Perfectionist Mode (a.k.a. Knuth Mode, a.k.a. Boffins Mode)

The brilliant computer scientist Donald Knuth wrote a brief article describing an optimal gameplay strategy for Mastermind®️ in 1977. He proved that with this strategy any code sequence is solvable within 5 moves. It involves an algorithm (which you can peruse at your leisure) that _optimally reduces the possible solution space each turn._ So, given the state of the board, there is a set of moves that are in the "Knuth set", and any of these moves are optimal in the sense that they eliminate the most alternatives.

In Knuth Mode, your gameplay must be optimal - every guess you make must be within the Knuth-set, or "Knuth-optimal", at that point in the game. If you make a good move, but not totally optimal, the game is immediately lost. This is a really nice challenge, and is probably hard to do well even if you read about it in advance. Knuth's paper is published in a few places and can be found online here, for example: https://cs.stanford.edu/~knuth/mm.txt - it is also summarized [here](KNUTH.md) - it refers to the trademarked game "Mastermind", which is a variation of the same pre-existing codebreaking game.
