"""
MisterMind engine constants, configuration values, and regex patterns.

All game-rule parameters (palette, code length, attempts), visual tokens
(emoji, hex colors), asset paths, and compiled regular expressions live
here so every other module can import a single, authoritative source of
truth without circular dependencies.
"""

from __future__ import annotations

import re
from itertools import product
from pathlib import Path

# ── Package layout ────────────────────────────────────────────────────
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ASSETS_ROOT = PACKAGE_ROOT / "assets"
PACKAGE_CONFIG_ROOT = PACKAGE_ROOT / "config"


# ── State envelope markers / schema ──────────────────────────────────
STATE_MARKER = "MM_STATE_V1"
CONDUCT_MARKER = "MM_CONDUCT_V1"
STATE_SCHEMA = "mistermind_state_v1"
CONDUCT_SCHEMA = "mistermind_conduct_v1"
STATE_VERSION = 1
STATE_CHAIN_VERSION = 1
CONDUCT_VERSION = 1
CONDUCT_CHAIN_VERSION = 1


# ── Game rules ────────────────────────────────────────────────────────
CODE_LENGTH = 4
MAX_ATTEMPTS = 10
GAME_TIMEOUT_MINUTES = 30
TERMINAL_CLOSE_GRACE_MINUTES = 20
PALETTE = ["red", "blue", "green", "yellow", "orange", "purple"]
ALIASES: dict[str, str] = {
    "r": "red",
    "b": "blue",
    "g": "green",
    "y": "yellow",
    "o": "orange",
    "p": "purple",
}

TERMINAL_PHASES = {"won", "lost"}

PHASE_LABEL: dict[str, str] = {
    "active": "mm:active",
    "won": "mm:won",
    "lost": "mm:lost",
}

ROOM_VARIANTS = {"classic", "hint", "perfectionist"}


# ── Emoji / visual tokens ────────────────────────────────────────────
COLOR_EMOJI: dict[str, str] = {
    "red": "🔴",
    "blue": "🔵",
    "green": "🟢",
    "yellow": "🟡",
    "orange": "🟠",
    "purple": "🟣",
}
EMPTY_SLOT = "⬜"
BLACK_PEG = "⚫"
WHITE_PEG = "⚪"
MISS_PEG = "·"


# ── Hex colors (Shields.io badges, SVG board) ───────────────────────
COLOR_HEX: dict[str, str] = {
    "red": "e74c3c",
    "blue": "3498db",
    "green": "27ae60",
    "yellow": "f1c40f",
    "orange": "e67e22",
    "purple": "9b59b6",
}
EMPTY_HEX = "3d2a1a"
FEEDBACK_EXACT_HEX = "111111"  # black peg
FEEDBACK_COLOR_HEX = "dddddd"  # white peg
FEEDBACK_MISS_HEX = "3d2a1a"  # empty socket (board color)


# ── Progress-bar glyphs ──────────────────────────────────────────────
PROGRESS_FULL = "\u2593"  # dark shade block
PROGRESS_EMPTY = "\u2591"  # light shade block
PROGRESS_LOST = "\u2593"  # dark shade block (same glyph, context gives meaning)
PROGRESS_WON = "\u2588"  # full block


# ── Board asset hosting ──────────────────────────────────────────────
BOARD_ASSET_BRANCH = "game-boards"
BOARD_ASSET_DIR = "boards"
BOARD_TEMPLATE_PATH = "assets/mistermind-board-template.svg"
BOARD_THEME_TEMPLATE_PATHS: dict[str, str] = {
    "classic": "assets/mistermind-board-template.svg",
    "ocean-v5": "assets/mistermind-board-template-ocean-v5.svg",
    "terminal": "assets/mistermind-board-template-terminal.svg",
}
BOARD_THEMES = set(BOARD_THEME_TEMPLATE_PATHS.keys())
DEFAULT_BOARD_THEME = "classic"


# ── Stats and leaderboard ────────────────────────────────────────────
STATS_PATH = "data/mistermind-stats.json"
LEADERBOARD_SVG_PATH = "leaderboard.svg"
STATS_SCHEMA = "mistermind-stats-v1"
HALL_OF_FAME_CAP = 10
RECENT_GAMES_CAP = 20
LEADERBOARD_TOP_N = 5
LEADERBOARD_MIN_GAMES = 2


# ── Parsing regex ────────────────────────────────────────────────────
GUESS_TOKEN_RE = r"(?:red|blue|green|yellow|orange|purple|r|b|g|y|o|p)"
GUESS_LINE_RE = re.compile(
    rf"^/?guess\s+({GUESS_TOKEN_RE})\s+({GUESS_TOKEN_RE})\s+({GUESS_TOKEN_RE})\s+({GUESS_TOKEN_RE})$",
    re.IGNORECASE,
)
_COLOR_LETTERS = set(ALIASES.keys())
MAX_PERIODS_ALLOWED = 4
DEFAULT_POLICY_PATH = "config/moderation-policy.v1.json"


# ── Numeric-code lookup tables (for solver/hint engine) ──────────────
COLOR_TO_INDEX: dict[str, int] = {color: idx for idx, color in enumerate(PALETTE)}
INDEX_TO_COLOR: dict[int, str] = {idx: color for color, idx in COLOR_TO_INDEX.items()}
ALL_NUMERIC_CODES: tuple[tuple[int, ...], ...] = tuple(
    product(range(len(PALETTE)), repeat=CODE_LENGTH)
)


# ── API rate-limit controls ──────────────────────────────────────────
GITHUB_REST_PRIMARY_LIMIT_PER_HOUR = 5000
GITHUB_TOKEN_HOURLY_LIMIT = GITHUB_REST_PRIMARY_LIMIT_PER_HOUR  # legacy export name
RATE_HEAT_WATERMARK = 500
RATE_LOW_WATERMARK = 100
