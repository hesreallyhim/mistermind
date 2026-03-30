#!/usr/bin/env python3
"""
Mistermind issue-thread engine.

This module is the **public API facade** for the engine package.
All symbols that were historically importable from ``engine`` are
re-exported here so that existing callers (tests, __main__.py, GitHub
Actions workflow) continue to work unchanged.

The actual implementation now lives in focused sub-modules:

  constants   - game-rule parameters, visual tokens, asset paths
  utils       - small helpers (b64, JSON, time, path resolution)
  parsing     - command parsing, room-variant / theme extraction
  scoring     - guess scoring, hint / optimizer integration
  state       - state building, validation, token encode/decode
  conduct     - moderation policy, conduct state machine
  rendering   - Markdown board / comment assembly
  svg         - SVG board renderer and template hydration
  stats       - stats accumulation, leaderboard ranking / SVG
  github_api  - GitHubAPI class, board-asset lifecycle
  handlers    - event handlers, routing, main() entry point
"""

from __future__ import annotations

# ── Module-level mutable state (tests poke at these) ────────────────
# Some tests monkey-patch ``mm._OPTIMIZER_SOLVER_CLASS``,
# ``mm._OPTIMIZER_SOLVER_LOAD_ERROR``, and ``mm._load_optimizer_solver_class``
# (and the hint-engine equivalents) on the *engine* module.  We proxy
# reads and writes of these names through to the ``scoring`` sub-module
# so that the actual functions see the patched values.
import mistermind.scoring as _scoring_mod

# ── commands ─────────────────────────────────────────────────────────
from mistermind.commands import (  # noqa: F401
    apply_command_to_state,
    apply_owner_guardrail_to_state,
    apply_perfectionist_gate,
)

# ── conduct ──────────────────────────────────────────────────────────
from mistermind.conduct import (  # noqa: F401
    apply_conduct_input,
    conduct_note_event,
    default_moderation_policy,
    load_moderation_policy,
    normalize_moderation_policy,
    owner_cooldown_active,
    render_conduct_state_comment,
    set_owner_cooldown,
    set_owner_disqualified,
    sync_conduct_labels,
)

# ── constants ────────────────────────────────────────────────────────
from mistermind.constants import (  # noqa: F401
    _COLOR_LETTERS,
    ALIASES,
    ALL_NUMERIC_CODES,
    BLACK_PEG,
    BOARD_ASSET_BRANCH,
    BOARD_ASSET_DIR,
    BOARD_TEMPLATE_PATH,
    BOARD_THEME_TEMPLATE_PATHS,
    BOARD_THEMES,
    CODE_LENGTH,
    COLOR_EMOJI,
    COLOR_HEX,
    COLOR_TO_INDEX,
    CONDUCT_CHAIN_VERSION,
    CONDUCT_MARKER,
    CONDUCT_SCHEMA,
    CONDUCT_VERSION,
    DEFAULT_BOARD_THEME,
    DEFAULT_POLICY_PATH,
    EMPTY_HEX,
    EMPTY_SLOT,
    FEEDBACK_COLOR_HEX,
    FEEDBACK_EXACT_HEX,
    FEEDBACK_MISS_HEX,
    GAME_TIMEOUT_MINUTES,
    GITHUB_REST_PRIMARY_LIMIT_PER_HOUR,
    GITHUB_TOKEN_HOURLY_LIMIT,
    GUESS_LINE_RE,
    GUESS_TOKEN_RE,
    HALL_OF_FAME_CAP,
    INDEX_TO_COLOR,
    LEADERBOARD_MIN_GAMES,
    LEADERBOARD_SVG_PATH,
    LEADERBOARD_TOP_N,
    MAX_ATTEMPTS,
    MAX_PERIODS_ALLOWED,
    MISS_PEG,
    PACKAGE_ASSETS_ROOT,
    PACKAGE_CONFIG_ROOT,
    PACKAGE_ROOT,
    PALETTE,
    PHASE_LABEL,
    PROGRESS_EMPTY,
    PROGRESS_FULL,
    PROGRESS_LOST,
    PROGRESS_WON,
    PROJECT_ROOT,
    RATE_HEAT_WATERMARK,
    RATE_LOW_WATERMARK,
    RECENT_GAMES_CAP,
    ROOM_VARIANTS,
    STATE_CHAIN_VERSION,
    STATE_MARKER,
    STATE_SCHEMA,
    STATE_VERSION,
    STATS_PATH,
    STATS_SCHEMA,
    TERMINAL_CLOSE_GRACE_MINUTES,
    TERMINAL_PHASES,
    WHITE_PEG,
)

# ── github_api ───────────────────────────────────────────────────────
from mistermind.github_api import (  # noqa: F401
    GitHubAPI,
    SecondaryRateLimitError,
    board_asset_path,
    board_raw_url,
    cleanup_board_svg,
    ensure_asset_branch,
    upload_board_svg,
)

# ── handlers ─────────────────────────────────────────────────────────
from mistermind.handlers import (  # noqa: F401
    _is_room_timed_out,
    _lock_on_terminal_transition,
    _on_game_terminal,
    _remote_command_body,
    _upload_board_and_render,
    find_latest_conduct_state_from_comments,
    find_latest_game_state_comment_id,
    find_latest_state_from_comments,
    handle_issue_comment,
    handle_issue_comment_conduct,
    handle_issue_opened,
    handle_remote_action,
    handle_terminal_room_sweep,
    issue_has_label,
    main,
    owner_has_prior_unanswered_command,
    should_process_issue_comment,
    should_process_issue_open,
    sync_room_labels,
)

# ── parsing ──────────────────────────────────────────────────────────
from mistermind.parsing import (  # noqa: F401
    _tokenize_guess,
    board_template_path_for_state,
    board_template_path_for_theme,
    normalize_board_theme,
    normalize_color,
    normalize_room_variant,
    parse_board_theme,
    parse_command,
    parse_room_variant,
    parsed_kind_is_guess,
    room_hints_enabled,
    room_perfectionist_enabled,
    state_board_theme,
    state_variant,
)

# ── rendering ────────────────────────────────────────────────────────
from mistermind.rendering import (  # noqa: F401
    _render_progress_bar_md,
    _render_rich_board,
    command_help_text,
    format_guess,
    format_history_table,
    format_visual_board,
    game_rules_text,
    render_comment,
)

# ── scoring ──────────────────────────────────────────────────────────
from mistermind.scoring import (  # noqa: F401
    _HintSolverView,
    _history_entry_to_numeric_guess,
    _load_hint_engine_class,
    _load_optimizer_solver_class,
    _remaining_candidates_from_history,
    _score_guess_numeric,
    compute_deductive_hint_summary,
    compute_perfectionist_optimality_summary,
    render_deductive_hints_markdown,
    render_perfectionist_failure_markdown,
    render_perfectionist_retry_markdown,
    score_guess,
)

# ── state ────────────────────────────────────────────────────────────
from mistermind.state import (  # noqa: F401
    build_initial_conduct_state,
    build_initial_state,
    conduct_state_is_valid,
    decode_conduct_token,
    decode_state_token,
    derive_signing_secret,
    derive_solution,
    encode_conduct_token,
    encode_state_token,
    extract_conduct_token,
    extract_state_token,
    is_valid_state_transition,
    state_is_valid,
    token_signature,
)

# ── stats ────────────────────────────────────────────────────────────
from mistermind.stats import (  # noqa: F401
    _empty_stats,
    load_stats,
    player_leaderboard_rank,
    player_stats_line,
    render_leaderboard_svg,
    save_stats,
    update_stats,
    upload_leaderboard_svg,
)

# ── svg ──────────────────────────────────────────────────────────────
from mistermind.svg import (  # noqa: F401
    _hint_status_map_for_slot,
    _svg_column_headers,
    _svg_defs,
    _svg_empty_socket,
    _svg_feedback_peg,
    _svg_legend,
    _svg_loss_overlay,
    _svg_peg,
    _svg_progress_bar,
    _svg_row_active,
    _svg_row_future,
    _svg_row_guessed,
    _svg_title_bar,
    _svg_win_overlay,
    hydrate_board_template,
    render_svg_board,
    svg_board_as_img_tag,
)

# ── utils ────────────────────────────────────────────────────────────
from mistermind.utils import (  # noqa: F401
    _as_int,
    b64url_decode,
    b64url_encode,
    canonical_json,
    issue_room_key,
    merge_dict,
    now_iso,
    parse_iso_utc,
    resolve_runtime_path,
)

_PROXY_ATTRS = {
    "_HINT_ENGINE_CLASS",
    "_HINT_ENGINE_LOAD_ERROR",
    "_OPTIMIZER_SOLVER_CLASS",
    "_OPTIMIZER_SOLVER_LOAD_ERROR",
    "_load_hint_engine_class",
    "_load_optimizer_solver_class",
}

import sys as _sys  # noqa: E402

_this = _sys.modules[__name__]
_original_setattr = type(_this).__setattr__


class _EngineFacadeModule(type(_this)):  # type: ignore[misc]
    """Custom module type that proxies mutable scoring globals."""

    def __getattr__(self, name: str) -> object:
        if name in _PROXY_ATTRS:
            return getattr(_scoring_mod, name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: object) -> None:
        if name in _PROXY_ATTRS:
            setattr(_scoring_mod, name, value)
            return
        _original_setattr(self, name, value)


_this.__class__ = _EngineFacadeModule


if __name__ == "__main__":
    raise SystemExit(main())
