"""
Markdown rendering for GitHub issue comments.

Plain-text and rich-HTML board tables, progress bars, comment assembly
(headline + board + hints + state token), rules text, and help text.
"""

from __future__ import annotations

from typing import Any

from mistermind.constants import (
    ALIASES,
    CODE_LENGTH,
    COLOR_EMOJI,
    EMPTY_SLOT,
    MAX_ATTEMPTS,
    PALETTE,
    PROGRESS_EMPTY,
    PROGRESS_FULL,
    PROGRESS_LOST,
    PROGRESS_WON,
    STATE_MARKER,
)
from mistermind.parsing import normalize_room_variant

# ── Formatting helpers ───────────────────────────────────────────────


def format_guess(guess: list[str]) -> str:
    return " ".join(guess)


def feedback_bucket_counts(black: int, white: int) -> tuple[int, int, int]:
    """Map internal black/white counts onto product-facing result buckets."""
    exact = max(0, int(black))
    color_only = max(0, int(white))
    absent = max(0, CODE_LENGTH - exact - color_only)
    return exact, color_only, absent


def feedback_matrix_rows(black: int, white: int) -> tuple[tuple[str, str, int], ...]:
    exact, color_only, absent = feedback_bucket_counts(black, white)
    return (
        ("✓", "✓", exact),
        ("✓", "✗", color_only),
        ("✗", "✗", absent),
    )


def feedback_verbose_rows(black: int, white: int) -> tuple[tuple[str, int], ...]:
    exact, color_only, absent = feedback_bucket_counts(black, white)
    return (
        ("Color and position", exact),
        ("Color only", color_only),
        ("Neither color nor position", absent),
    )


def format_feedback_summary(black: int, white: int) -> str:
    exact, color_only, absent = feedback_bucket_counts(black, white)
    return f"{exact} exact, {color_only} color-only, {absent} absent"


def format_feedback_matrix_text(black: int, white: int, *, separator: str = " · ") -> str:
    return separator.join(
        f"{color}/{location} {count}"
        for color, location, count in feedback_matrix_rows(black, white)
    )


def format_feedback_matrix_html(black: int, white: int) -> str:
    return "<br>".join(
        f"<code>{color}/{location} {count}</code>"
        for color, location, count in feedback_matrix_rows(black, white)
    )


def format_feedback_verbose_html(black: int, white: int) -> str:
    return "<br>".join(
        f"<strong>{label}</strong>: <code>{count}</code>"
        for label, count in feedback_verbose_rows(black, white)
    )


def format_feedback_placeholder_text() -> str:
    return "·/· · ·/· · ·/· ·"


def format_history_table(state: dict[str, Any]) -> str:
    history = state.get("history", [])
    if not history:
        return "_No guesses yet._"

    lines = [
        "| # | Guess | Feedback |",
        "|---|---|---|",
    ]
    for entry in history:
        feedback = format_feedback_summary(int(entry["black"]), int(entry["white"]))
        lines.append(f"| {entry['attempt']} | `{format_guess(entry['guess'])}` | {feedback} |")
    return "\n".join(lines)


def format_visual_board(state: dict[str, Any]) -> str:
    """Legacy plain-text board (kept for fallback / debug)."""
    history = state.get("history", [])
    lines = [
        "#  Guess              │ Feedback",
        "----------------------+----------------",
    ]

    for slot in range(1, MAX_ATTEMPTS + 1):
        if slot <= len(history):
            entry = history[slot - 1]
            guess = entry["guess"]
            guess_icons = " ".join(COLOR_EMOJI.get(color, EMPTY_SLOT) for color in guess)
            black = int(entry["black"])
            white = int(entry["white"])
            peg_icons = format_feedback_matrix_text(black, white)
            lines.append(f"{slot:02d} {guess_icons} │ {peg_icons}")
        else:
            guess_icons = " ".join([EMPTY_SLOT] * CODE_LENGTH)
            peg_icons = format_feedback_placeholder_text()
            lines.append(f"{slot:02d} {guess_icons} │ {peg_icons}")

    return "\n".join(lines)


# ── Progress bar ─────────────────────────────────────────────────────


def _render_progress_bar_md(attempt: int, phase: str) -> str:
    """Render a visual progress bar using emoji blocks."""
    filled = attempt
    total = MAX_ATTEMPTS
    remaining = total - filled

    if phase == "won":
        bar = PROGRESS_WON + " " + PROGRESS_FULL * filled + PROGRESS_EMPTY * remaining
    elif phase == "lost":
        bar = PROGRESS_LOST * filled + PROGRESS_EMPTY * remaining
    else:
        bar = PROGRESS_FULL * filled + PROGRESS_EMPTY * remaining

    return bar


# ── Rich board (HTML table) ──────────────────────────────────────────


def _render_rich_board(
    state: dict[str, Any],
    *,
    reveal_solution: bool = False,
    solution: list[str] | None = None,
) -> str:
    """
    Render the MisterMind board as a rich GitHub-compatible HTML table.

    Inspired by the visual design in assets/mistermind-board-animated.svg,
    this uses emoji, HTML tables, and careful formatting to create a
    visually engaging board directly in GitHub issue comments.
    """
    history = state.get("history", [])
    attempt = int(state.get("attempt", 0))
    phase = state.get("phase", "active")
    issue_number = state.get("issue_number", "?")
    remaining = max(0, MAX_ATTEMPTS - attempt)
    # ── Header ──────────────────────────────────────────────────────
    if phase == "won":
        title_text = "MISTERMIND — CRACKED!"
    elif phase == "lost":
        title_text = "MISTERMIND — GAME OVER"
    else:
        title_text = f"MISTERMIND — GAME #{issue_number}"

    lines: list[str] = []

    # Title block
    lines.append(f"## {title_text}")
    lines.append("")

    # Status bar
    progress_bar = _render_progress_bar_md(attempt, phase)
    if phase == "active":
        lines.append(
            f"> {progress_bar}  **Turn {attempt + 1}** of {MAX_ATTEMPTS} "
            f"— {remaining} guess{'es' if remaining != 1 else ''} left"
        )
    elif phase == "won":
        lines.append(
            f"> {progress_bar}  **Solved in {attempt} turn{'s' if attempt != 1 else ''}!**"
        )
    else:
        lines.append(f"> {progress_bar}  **No attempts remaining**")
    lines.append("")

    # ── Board table ─────────────────────────────────────────────────
    result_header = "**Result (Count Summary)**"
    lines.append(f"| | | | **Guess** | | | {result_header} |")
    lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    for slot in range(1, MAX_ATTEMPTS + 1):
        if slot <= len(history):
            entry = history[slot - 1]
            guess = entry["guess"]
            black = int(entry["black"])
            white = int(entry["white"])
            # Guess pegs as emoji
            pegs = [COLOR_EMOJI.get(c, EMPTY_SLOT) for c in guess]

            feedback = format_feedback_verbose_html(black, white)

            # Highlight the latest guess row
            if slot == len(history) and phase == "active":
                row_num = f"**`{slot:02d}`**"
            elif slot == len(history) and phase == "won":
                row_num = f"**`{slot:02d}`** *"
            else:
                row_num = f"`{slot:02d}`"

            lines.append(
                f"| {row_num} | {pegs[0]} | {pegs[1]} | {pegs[2]} | {pegs[3]} | | {feedback} |"
            )

        elif slot == attempt + 1 and phase == "active":
            # Active row — awaiting guess
            lines.append(f"| **`▶{slot:02d}`** | ◌ | ◌ | ◌ | ◌ | | |")

        else:
            # Future row — dimmed
            lines.append(f"| `{slot:02d}` | · | · | · | · | | |")

    lines.append("")

    # ── Legend ───────────────────────────────────────────────────────
    lines.append(
        "> Result key: "
        "<strong>Color and position</strong>, "
        "<strong>Color only</strong>, "
        "<strong>Neither color nor position</strong> "
        f"&nbsp;&nbsp; **Palette:** {' '.join(COLOR_EMOJI[c] for c in PALETTE)}"
    )
    lines.append("")

    # ── Win / loss banner ───────────────────────────────────────────
    if phase == "won":
        lines.extend(
            [
                "---",
                "",
                '<div align="center">',
                "",
                "### CODE CRACKED!",
                "",
                f"You broke the code in **{attempt}** turn{'s' if attempt != 1 else ''}!",
                "",
                "</div>",
                "",
            ]
        )

    if phase == "lost" and not reveal_solution:
        lines.extend(
            [
                "---",
                "",
                '<div align="center">',
                "",
                "### THE CODE STANDS UNBROKEN",
                "",
                "</div>",
                "",
            ]
        )

    # ── Solution reveal ─────────────────────────────────────────────
    if reveal_solution and solution:
        sol_emoji = " ".join(COLOR_EMOJI.get(c, c) for c in solution)
        sol_text = " ".join(solution)
        if phase == "won":
            lines.extend(
                [
                    f"> **Solution:** {sol_emoji} (`{sol_text}`)",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "---",
                    "",
                    '<div align="center">',
                    "",
                    "### THE CODE STANDS UNBROKEN",
                    "",
                    f"The secret was: {sol_emoji}",
                    "",
                    f"`{sol_text}`",
                    "",
                    "</div>",
                    "",
                ]
            )

    return "\n".join(lines)


# ── Comment assembly ─────────────────────────────────────────────────


def render_comment(
    *,
    headline: str,
    state: dict[str, Any],
    token: str,
    reveal_solution: bool,
    solution: list[str],
    board_image_url: str | None = None,
    hint_block: str | None = None,
) -> str:
    phase = state.get("phase", "active")

    lines: list[str] = []

    # ── Headline ────────────────────────────────────────────────────
    lines.append(f"> **{headline}**")
    lines.append("")

    # ── SVG board image (if hosted) ─────────────────────────────────
    # Wrapped in <picture> so GitHub renders it as a plain image rather
    # than a clickable link.
    if board_image_url:
        lines.extend(
            [
                '<div align="center">',
                "<picture>",
                f'  <img src="{board_image_url}" alt="MisterMind Board" width="460">',
                "</picture>",
                "</div>",
                "",
            ]
        )

    # ── Markdown board (collapsible fallback when image is present) ─
    if board_image_url:
        lines.extend(
            [
                "<details>",
                "<summary>Board (text)</summary>",
                "",
            ]
        )

    lines.append(
        _render_rich_board(
            state,
            reveal_solution=reveal_solution,
            solution=solution,
        )
    )

    if board_image_url:
        lines.extend(
            [
                "</details>",
                "",
            ]
        )

    if hint_block:
        lines.extend(
            [
                hint_block,
                "",
            ]
        )

    # ── Anti-bot pace note (stochastic, ~every 3 turns) ──────────
    seq = int(state.get("seq", 0))
    if phase == "active" and seq > 0 and seq % 3 == 0:
        lines.extend(
            [
                "",
                "> *Please wait a moment for MisterMind to reply "
                "before submitting your next guess.*",
            ]
        )

    # ── Engine envelope (hidden in HTML comment) ──────────────────
    lines.append(f"\n<!-- {STATE_MARKER} {token} -->")

    return "\n".join(lines)


# ── Rules / help text ────────────────────────────────────────────────


def game_rules_text(*, variant: str = "classic") -> str:
    """Full rules block suitable for the bot's opening comment."""
    palette = ", ".join(PALETTE)
    letters = ", ".join(sorted(ALIASES.keys()))
    mode = normalize_room_variant(variant)
    if mode == "hint":
        mode_line = (
            "- **Hint mode enabled:** after each guess the bot flags peg placements\n"
            "  that are provably impossible (`🚫`) or provably locked (`🔒`) from prior state.\n"
        )
    elif mode == "perfectionist":
        mode_line = (
            "- **Perfectionist mode enabled:** every guess must be in Knuth's\n"
            "  minimax-optimal class. A non-optimal guess forfeits the room,\n"
            "  **unless that guess solves the full code**.\n"
        )
    else:
        mode_line = ""
    lines = [
        "### Rules",
        "",
        f"- **{len(PALETTE)} colors:** {palette}",
        f"- **{CODE_LENGTH} slots** per guess -- duplicates allowed",
        f"- **{MAX_ATTEMPTS} guesses** to crack the code",
    ]
    if mode_line:
        lines.extend(mode_line.rstrip().splitlines())
    result_key_lines = [
        "",
        "**Result key**",
        "",
        "| Feedback line | Meaning |",
        "|:---|:---|",
        "| Color and position | right color in the right slot |",
        "| Color only | right color in a different slot |",
        "| Neither color nor position | color not present in the code |",
        "",
        "- The bot reports aggregate counts only; they do **not** map back to specific slots.",
    ]
    lines.extend(
        [
            *result_key_lines,
            "",
            "### How to guess",
            "",
            f"Comment with exactly {CODE_LENGTH} colors. Any of these work:",
            "- Full names: `red blue green yellow`",
            f"- Single letters ({letters}): `r b g y`",
            "- Concatenated letters: `rbgy`",
            "- Mixed: `r orange g g`",
            "",
            "Control command (use slash form): `/giveup`",
            "",
            "### Fair play",
            "",
            "Rooms are owner-driven. Please keep gameplay comments to your own room.",
            "",
            "Avoid rapid-fire commands. Make a guess, then give MisterMind a moment to reply before posting the next command."
            "",
            "At this stage, moderation is reminder-and-monitoring focused. Repeated disruption may lead to restrictions.",
            # "To keep the game fair, lightweight reminders and monitoring are in place:",
            # "",
            # "| Situation | 1st occurrence | 2nd occurrence | 3rd occurrence |",
            # "|:---|:---|:---|:---|",
            # "| **Invalid guess format** | Warning | Reminder + logged | Continued logging |",
            # "| **Rapid-fire guessing** (before bot replies) | Warning | Reminder + logged | Continued logging |",
            # "| **Non-owner commenting** | Friendly redirect | Reminder + logged | Continued logging |",
            # "",
            # "*Submitting a valid guess resets the invalid-format counter.*",
        ]
    )
    return "\n".join(lines)


def command_help_text() -> str:
    """Short help text for /help responses."""
    palette = ", ".join(PALETTE)
    letters = ", ".join(sorted(ALIASES.keys()))
    return (
        f"**Palette:** {palette}\n"
        f"**Letters:** {letters}\n\n"
        f"`Comment with exactly {CODE_LENGTH} colors to guess. "
        "Full names, single letters, or concatenated letters all work.\n"
        "Duplicates are allowed. Case doesn't matter.\n\n"
        "Control command (slash form): `/giveup`"
    )
