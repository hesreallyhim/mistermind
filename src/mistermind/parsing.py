"""
Command parsing, color normalization, and room-option extraction.

Handles all user-input interpretation: guesses (with forgiving normalization
of words/letters and optional guess-prefix typos), slash commands, and
issue-form variant/theme extraction.
"""

from __future__ import annotations

import re
from typing import Any

from mistermind.constants import (
    _COLOR_LETTERS,
    ALIASES,
    BOARD_TEMPLATE_PATH,
    BOARD_THEME_TEMPLATE_PATHS,
    BOARD_THEMES,
    CODE_LENGTH,
    DEFAULT_BOARD_THEME,
    PALETTE,
    ROOM_VARIANTS,
)

# ── Color helpers ─────────────────────────────────────────────────────


def normalize_color(token: str) -> str | None:
    raw = (token or "").strip().lower()
    if raw in ALIASES:
        return ALIASES[raw]
    if raw in PALETTE:
        return raw
    return None


_GUESS_PREFIX_ALIASES = ("guess", "gues", "guesss")
_GUESS_PREFIX_RE = re.compile(rf"^/?(?:{'|'.join(_GUESS_PREFIX_ALIASES)})\b", re.IGNORECASE)
_CONTROL_COMMAND_RE = re.compile(r"^/?(help|status|giveup)\b", re.IGNORECASE)
_GUESS_LETTERS_RE = re.compile(
    rf"^[{''.join(sorted(_COLOR_LETTERS))}]{{{CODE_LENGTH}}}$", re.IGNORECASE
)
_COLOR_NAME_TO_ALIAS = {color: alias for alias, color in ALIASES.items()}
_COLOR_NAME_RE = re.compile(
    "|".join(sorted(_COLOR_NAME_TO_ALIAS.keys(), key=len, reverse=True)),
    re.IGNORECASE,
)


def _starts_with_guess_prefix(raw: str) -> bool:
    return bool(_GUESS_PREFIX_RE.match((raw or "").strip()))


def _strip_guess_prefix(raw: str) -> str:
    text = (raw or "").strip()
    match = _GUESS_PREFIX_RE.match(text)
    if not match:
        return text
    return text[match.end() :]


def _replace_color_names_with_aliases(raw: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        return _COLOR_NAME_TO_ALIAS[match.group(0).lower()]

    return _COLOR_NAME_RE.sub(_repl, raw)


def _tokenize_guess(raw: str) -> list[str] | None:
    """Extract a guess by normalizing words/letters to 4 color aliases."""
    if not raw:
        return None

    cleaned = _strip_guess_prefix(raw).lower()
    if not cleaned:
        return None

    normalized = _replace_color_names_with_aliases(cleaned)
    # Forgive accidental spacing / speech-to-text dots between tokens.
    normalized = re.sub(r"(?:\s*\.\s*|\s+)", "", normalized)
    if _GUESS_LETTERS_RE.fullmatch(normalized):
        return [ALIASES[ch] for ch in normalized]

    return None


# ── Command parser ────────────────────────────────────────────────────


def parse_command(text: str) -> dict[str, Any]:
    """Parse user input into a structured command.

    Command precedence:
      1) leading control command (`/help`, `/status`, `/giveup`) wins
         and ignores trailing text
      2) otherwise parse guess normalization
    """
    if not text:
        return {"kind": "ignore"}

    first = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            first = stripped
            break

    if not first:
        return {"kind": "ignore"}

    control = _CONTROL_COMMAND_RE.match(first)
    if control:
        return {"kind": control.group(1).lower()}

    # Try to parse as a guess (flexible tokenizer)
    colors = _tokenize_guess(first)
    if colors is not None:
        return {"kind": "guess", "guess": colors}

    # If the user explicitly invoked guess (including tolerated typo aliases)
    # but formatting failed, return a friendly format error.
    if _starts_with_guess_prefix(first):
        return {
            "kind": "guess",
            "error": (
                "That doesn't look like a valid guess. "
                f"Send exactly {CODE_LENGTH} colors, e.g.: "
                "`red blue green yellow` or `rbgy`"
            ),
        }

    return {"kind": "ignore"}


def parsed_kind_is_guess(parsed: dict[str, Any]) -> bool:
    return parsed.get("kind") == "guess"


# ── Room variant / theme extraction ──────────────────────────────────


def parse_room_variant(issue_body: str | None) -> str:
    """Extract room variant from issue form body.

    Supports free-text key/value and GitHub form sections. Falls back to
    classic mode when no explicit hint selection is found.
    """
    text = issue_body or ""
    probes: list[str] = []

    patterns = (
        r"(?im)^(?:gameplay\s*)?mode:\s*([^\r\n]+)",
        r"(?im)^variant:\s*([^\r\n]+)",
        r"(?is)###\s*(?:gameplay\s*)?mode[^\r\n]*\r?\n+([^\r\n]+)",
        r"(?is)###\s*hint(?:\s|-)?mode[^\r\n]*\r?\n+([^\r\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            probes.append(match.group(1).strip().lower())

    for probe in probes:
        if "perfectionist" in probe:
            return "perfectionist"
        if "hint" in probe:
            return "hint"
        if "classic" in probe or "standard" in probe:
            return "classic"

    if re.search(r"(?i)\b(?:gameplay\s*)?mode\s*[:=-]?\s*perfectionist\b", text):
        return "perfectionist"
    if re.search(r"(?i)\b(?:gameplay\s*)?mode\s*[:=-]?\s*hint\b", text):
        return "hint"

    return "classic"


def parse_board_theme(issue_body: str | None) -> str:
    """Extract board theme choice from issue form body."""
    text = issue_body or ""
    probes: list[str] = []

    patterns = (
        r"(?im)^board\s*theme:\s*([^\r\n]+)",
        r"(?im)^theme:\s*([^\r\n]+)",
        r"(?is)###\s*board\s*theme[^\r\n]*\r?\n+([^\r\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            probes.append(match.group(1).strip().lower())

    for probe in probes:
        if "terminal" in probe:
            return "terminal"
        if "ocean" in probe:
            return "ocean-v5"
        if "classic" in probe or "wood" in probe:
            return "classic"

    return DEFAULT_BOARD_THEME


# ── Variant/theme normalization helpers ──────────────────────────────


def state_variant(state: dict[str, Any]) -> str:
    raw = str(state.get("variant", "classic")).strip().lower()
    return raw if raw in ROOM_VARIANTS else "classic"


def room_hints_enabled(state: dict[str, Any]) -> bool:
    return state_variant(state) == "hint"


def room_perfectionist_enabled(state: dict[str, Any]) -> bool:
    return state_variant(state) == "perfectionist"


def normalize_room_variant(raw: str | None) -> str:
    value = (raw or "classic").strip().lower()
    return value if value in ROOM_VARIANTS else "classic"


def normalize_board_theme(raw: str | None) -> str:
    value = (raw or DEFAULT_BOARD_THEME).strip().lower()
    if value in BOARD_THEMES:
        return value
    if "terminal" in value:
        return "terminal"
    if "ocean" in value:
        return "ocean-v5"
    return DEFAULT_BOARD_THEME


def state_board_theme(state: dict[str, Any]) -> str:
    raw = str(state.get("board_theme", DEFAULT_BOARD_THEME))
    return normalize_board_theme(raw)


def board_template_path_for_theme(theme: str) -> str:
    normalized = normalize_board_theme(theme)
    return BOARD_THEME_TEMPLATE_PATHS.get(normalized, BOARD_TEMPLATE_PATH)


def board_template_path_for_state(state: dict[str, Any]) -> str:
    return board_template_path_for_theme(state_board_theme(state))
