"""
SVG board renderer.

Programmatic SVG generation for the MisterMind game board (rows, pegs,
feedback, overlays) and the template-hydration path that fills
placeholder tags in the SVG template files shipped as package assets.
"""

from __future__ import annotations

import base64
from typing import Any

from mistermind.constants import (
    BOARD_TEMPLATE_PATH,
    CODE_LENGTH,
    COLOR_HEX,
    DEFAULT_BOARD_THEME,
    EMPTY_HEX,
    FEEDBACK_COLOR_HEX,
    FEEDBACK_EXACT_HEX,
    FEEDBACK_MISS_HEX,
    MAX_ATTEMPTS,
    PALETTE,
)
from mistermind.parsing import (
    normalize_board_theme,
    state_board_theme,
)
from mistermind.rendering import feedback_matrix_rows
from mistermind.utils import resolve_runtime_path

# ── Theme label helpers ──────────────────────────────────────────────

_COLOR_LETTER_LABEL: dict[str, str] = {c: c[0].upper() for c in PALETTE}


def _theme_is_terminal(theme: str | None) -> bool:
    return normalize_board_theme(theme) == "terminal"


def _theme_is_ocean(theme: str | None) -> bool:
    return normalize_board_theme(theme) == "ocean-v5"


# ── SVG primitives ───────────────────────────────────────────────────


def _svg_defs() -> str:
    """Shared SVG <defs> for gradients, filters, and glow effects."""
    return """  <defs>
    <linearGradient id="board-bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#2d1f14"/>
      <stop offset="100%" stop-color="#1a120b"/>
    </linearGradient>
    <radialGradient id="peg-gloss" cx="35%" cy="30%" r="60%">
      <stop offset="0%" stop-color="rgba(255,255,255,0.45)"/>
      <stop offset="100%" stop-color="rgba(255,255,255,0)"/>
    </radialGradient>
    <filter id="peg-shadow" x="-20%" y="-10%" width="140%" height="150%">
      <feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.5"/>
    </filter>
    <filter id="active-glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="win-glow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <style>
      .peg-certain { stroke: #27ae60; stroke-width: 3; fill: none; }
      .peg-impossible { stroke: #e74c3c; stroke-width: 3; fill: none; stroke-dasharray: 4 3; }
    </style>
  </defs>"""


def _svg_peg(
    cx: int,
    cy: int,
    color: str,
    *,
    radius: int = 14,
    deduction_status: str | None = None,
    theme: str = DEFAULT_BOARD_THEME,
) -> str:
    """Render a colored peg with gloss, shadow, and a center letter
    for accessibility (helps color-deficient players)."""
    terminal = _theme_is_terminal(theme)
    fill_color = f"#{COLOR_HEX.get(color, EMPTY_HEX)}"
    letter = _COLOR_LETTER_LABEL.get(color, "")
    # Use a semitransparent dark fill for contrast on bright pegs,
    # lighter for dark pegs (blue, purple).
    text_fill = "rgba(255,255,255,0.82)" if color in ("blue", "purple") else "rgba(0,0,0,0.45)"
    deduced_class = ""
    deduced_ring = ""
    motion = ""
    if _theme_is_ocean(theme):
        # Subtle ambient drift (ocean theme only): deterministic per peg position
        # so animation feels organic without per-render randomness.
        seed = (cx * 31 + cy * 17) % 997
        amp_x = 2 + (seed % 3)  # 2..4 px
        amp_y = 1 + ((seed // 5) % 2)  # 1..2 px
        dur = 5.2 + ((seed % 6) * 0.55)  # 5.2s .. 7.95s
        begin = (seed % 10) * 0.19  # small phase offset
        motion = (
            '<animateTransform attributeName="transform" type="translate" '
            f'values="0,0; {amp_x},{amp_y}; {-amp_x},-{amp_y}; 0,0" '
            f'dur="{dur:.2f}s" begin="{begin:.2f}s" repeatCount="indefinite" '
            'calcMode="spline" keySplines="0.42 0 0.58 1;0.42 0 0.58 1;0.42 0 0.58 1"/>'
        )
    if deduction_status == "certain":
        deduced_class = " mm-deduction-certain"
        deduced_ring = (
            f'<circle cx="{cx}" cy="{cy}" r="{radius + 3}" class="peg-certain" '
            f'fill="none" stroke="#27ae60" stroke-width="3"/>'
        )
    elif deduction_status == "impossible":
        deduced_class = " mm-deduction-impossible"
        deduced_ring = (
            f'<circle cx="{cx}" cy="{cy}" r="{radius + 3}" class="peg-impossible" '
            f'fill="none" stroke="#e74c3c" stroke-width="3" stroke-dasharray="4 3"/>'
        )

    return (
        f'<g class="mm-peg{deduced_class}" data-deduction="{deduction_status or "none"}">'
        f"{motion}"
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{fill_color}" '
        f'filter="url(#peg-shadow)"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="url(#peg-gloss)"'
        f' opacity="{0.25 if terminal else 1}"/>'
        f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" '
        f'fill="{text_fill}" font-size="11" font-weight="bold">'
        f"{letter}</text>"
        f"{deduced_ring}"
        f"</g>"
    )


def _svg_empty_socket(
    cx: int,
    cy: int,
    *,
    radius: int = 14,
    theme: str = DEFAULT_BOARD_THEME,
) -> str:
    """Render an empty peg socket (dashed outline)."""
    if _theme_is_terminal(theme):
        stroke = "#2ecf66"
        width = 1.6
    elif _theme_is_ocean(theme):
        stroke = "#6f9ec5"
        width = 2.3
    else:
        stroke = "#bc9060"
        width = 2.4
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
        f'stroke="{stroke}" stroke-width="{width}" stroke-dasharray="4 3"/>'
    )


def _svg_feedback_peg(
    cx: int,
    cy: int,
    kind: str,
    *,
    theme: str = DEFAULT_BOARD_THEME,
) -> str:
    """Render a small feedback peg: 'exact' (black), 'color' (white), or 'miss'."""
    if _theme_is_terminal(theme):
        size = 10
        x = cx - size // 2
        y = cy - size // 2
        if kind == "exact":
            return (
                f'<rect x="{x}" y="{y}" width="{size}" height="{size}" '
                f'fill="#00ff44" stroke="#00ff44" stroke-width="1.2"/>'
            )
        if kind == "color":
            return (
                f'<rect x="{x}" y="{y}" width="{size}" height="{size}" '
                f'fill="#00aa33" opacity="0.65" stroke="#00aa33" stroke-width="1.2"/>'
            )
        return (
            f'<rect x="{x}" y="{y}" width="{size}" height="{size}" '
            f'fill="none" stroke="#0f1a0f" stroke-width="1.2"/>'
        )

    r = 6
    if kind == "exact":
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="#{FEEDBACK_EXACT_HEX}" stroke="#888" stroke-width="1.5"/>'
        )
    elif kind == "color":
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="#{FEEDBACK_COLOR_HEX}" stroke="#fff" stroke-width="1.5"/>'
        )
    else:
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="none" stroke="#{FEEDBACK_MISS_HEX}" stroke-width="2"/>'
        )


def _svg_feedback_matrix(
    black: int,
    white: int,
    *,
    theme: str = DEFAULT_BOARD_THEME,
    placeholder: bool = False,
) -> str:
    """Render the product-facing feedback matrix in the result column."""
    if _theme_is_terminal(theme):
        fill = "#25e067"
        opacity = "0.62" if placeholder else "0.95"
    elif _theme_is_ocean(theme):
        fill = "#93c0df"
        opacity = "0.62" if placeholder else "0.9"
    else:
        fill = "#f0dcbf"
        opacity = "0.62" if placeholder else "0.9"

    rows = (
        (("·", "·", "·"),) * 3
        if placeholder
        else tuple(
            (color, location, str(count))
            for color, location, count in feedback_matrix_rows(black, white)
        )
    )
    x_positions = (314, 334, 356)
    y_positions = (15, 24, 33)

    parts: list[str] = []
    for y, row in zip(y_positions, rows, strict=False):
        for x, value in zip(x_positions, row, strict=False):
            parts.append(
                f'    <text x="{x}" y="{y}" fill="{fill}" opacity="{opacity}" '
                'font-size="8.5" text-anchor="middle" font-weight="bold">'
                f"{value}</text>"
            )
    return "\n".join(parts)


def _svg_feedback_terminal_grid(
    black: int,
    white: int,
    *,
    placeholder: bool = False,
) -> str:
    """Render terminal-theme feedback as bright/dim/off squares."""
    positions = [(330, 14), (350, 14), (330, 33), (350, 33)]
    if placeholder:
        return "\n".join(
            f'    <rect x="{cx - 5}" y="{cy - 5}" width="10" height="10" fill="none" '
            'stroke="#0f1a0f" stroke-width="1.2"/>'
            for cx, cy in positions
        )

    misses = max(0, CODE_LENGTH - black - white)
    kinds: list[str] = ["exact"] * black + ["color"] * white + ["miss"] * misses
    return "\n".join(
        f"    {_svg_feedback_peg(cx, cy, kinds[idx], theme='terminal')}"
        for idx, (cx, cy) in enumerate(positions)
    )


# ── Row renderers ────────────────────────────────────────────────────


def _svg_row_guessed(
    y_offset: int,
    slot: int,
    guess: list[str],
    black: int,
    white: int,
    hint_status_by_pos: dict[int, str] | None = None,
    theme: str = DEFAULT_BOARD_THEME,
) -> str:
    """Render a completed guess row."""
    row_y = y_offset
    parts: list[str] = []
    row_class = "mm-row-guessed"
    if hint_status_by_pos:
        row_class += " mm-row-deduced"

    if _theme_is_terminal(theme):
        row_fill = "#0c140c"
        row_opacity = "0.58"
        row_num_fill = "#00aa33"
        divider = "#0f1a0f"
    elif _theme_is_ocean(theme):
        row_fill = "#0a1525"
        row_opacity = "0.56"
        row_num_fill = "#5f85ab"
        divider = "#1b3048"
    else:
        row_fill = "#251a10"
        row_opacity = "0.6"
        row_num_fill = "#a08060"
        divider = "#3d2a1a"

    # Row background
    parts.append(
        f'  <g transform="translate(0, {row_y})" class="{row_class}">'
        f'\n    <rect x="22" y="2" width="416" height="42" rx="8" '
        f'fill="{row_fill}" opacity="{row_opacity}"/>'
    )

    # Row number
    parts.append(
        f'    <text x="42" y="28" fill="{row_num_fill}" font-size="12" '
        f'text-anchor="middle" font-weight="bold">{slot:02d}</text>'
    )

    # Guess pegs
    peg_positions = [100, 140, 180, 220]
    for i, color in enumerate(guess):
        status = None
        if hint_status_by_pos is not None:
            status = hint_status_by_pos.get(i)
        parts.append(
            f"    {_svg_peg(peg_positions[i], 23, color, deduction_status=status, theme=theme)}"
        )

    # Divider
    parts.append(
        f'    <line x1="270" y1="6" x2="270" y2="40" stroke="{divider}" stroke-width="2"/>'
    )

    if _theme_is_terminal(theme):
        parts.append(_svg_feedback_terminal_grid(black, white))
    else:
        parts.append(_svg_feedback_matrix(black, white, theme=theme))

    parts.append("  </g>")
    return "\n".join(parts)


def _svg_row_active(y_offset: int, slot: int, *, theme: str = DEFAULT_BOARD_THEME) -> str:
    """Render the current active row (pulsing glow)."""
    if _theme_is_terminal(theme):
        row_fill = "#0a1a0a"
        active_text = "#00ff44"
        active_prefix = "▶"
        divider = "#0f1a0f"
    elif _theme_is_ocean(theme):
        row_fill = "#11314f"
        active_text = "#8ad9ff"
        active_prefix = "▶"
        divider = "#1b3048"
    else:
        row_fill = "#3a2a10"
        active_text = "#f0c060"
        active_prefix = "▶"
        divider = "#3d2a1a"

    return (
        f'  <g transform="translate(0, {y_offset})">\n'
        f'    <rect x="22" y="2" width="416" height="42" rx="8" '
        f'fill="{row_fill}" filter="url(#active-glow)">\n'
        f'      <animate attributeName="opacity" values="0.4;0.75;0.4" '
        f'dur="2s" repeatCount="indefinite" '
        f'calcMode="spline" keySplines="0.42 0 0.58 1;0.42 0 0.58 1"/>\n'
        f"    </rect>\n"
        f'    <text x="42" y="28" fill="{active_text}" font-size="12" '
        f'text-anchor="middle" font-weight="bold">'
        f"{active_prefix} {slot:02d}</text>\n"
        + "\n".join(f"    {_svg_empty_socket(cx, 23, theme=theme)}" for cx in [100, 140, 180, 220])
        + '\n    <line x1="270" y1="6" x2="270" y2="40" '
        f'stroke="{divider}" stroke-width="2"/>\n'
        + "\n"
        + (
            _svg_feedback_terminal_grid(0, 0, placeholder=True)
            if _theme_is_terminal(theme)
            else _svg_feedback_matrix(0, 0, theme=theme, placeholder=True)
        )
        + "\n  </g>"
    )


def _svg_row_future(y_offset: int, slot: int, *, theme: str = DEFAULT_BOARD_THEME) -> str:
    """Render a future (dimmed) row."""
    if _theme_is_terminal(theme):
        row_fill = "#0d160f"
        row_num_fill = "#39d872"
        peg_stroke = "#2d7f4a"
        divider = "#215d39"
        opacity = "0.58"
    elif _theme_is_ocean(theme):
        row_fill = "#0e2239"
        row_num_fill = "#9fc9ea"
        peg_stroke = "#6d9ec6"
        divider = "#4b7296"
        opacity = "0.68"
    else:
        row_fill = "#2b1d11"
        row_num_fill = "#d8b384"
        peg_stroke = "#c69a67"
        divider = "#7f603d"
        opacity = "0.86"

    return (
        f'  <g transform="translate(0, {y_offset})" opacity="{opacity}">\n'
        f'    <rect x="22" y="2" width="416" height="42" rx="8" '
        f'fill="{row_fill}" opacity="0.68"/>\n'
        f'    <text x="42" y="28" fill="{row_num_fill}" font-size="12" '
        f'text-anchor="middle" font-weight="bold">{slot:02d}</text>\n'
        + "\n".join(
            f'    <circle cx="{cx}" cy="23" r="14" fill="none" '
            f'stroke="{peg_stroke}" stroke-width="2.3"/>'
            for cx in [100, 140, 180, 220]
        )
        + '\n    <line x1="270" y1="6" x2="270" y2="40" '
        f'stroke="{divider}" stroke-width="1.5"/>\n'
        + "\n"
        + (
            _svg_feedback_terminal_grid(0, 0, placeholder=True)
            if _theme_is_terminal(theme)
            else _svg_feedback_matrix(0, 0, theme=theme, placeholder=True)
        )
        + "\n  </g>"
    )


# ── Board furniture ──────────────────────────────────────────────────


def _svg_progress_bar(attempt: int, phase: str, *, theme: str = DEFAULT_BOARD_THEME) -> str:
    """Render the footer progress bar and status text."""
    remaining = max(0, MAX_ATTEMPTS - attempt)

    # Progress bar fill width (max 400px)
    fill_pct = attempt / MAX_ATTEMPTS
    fill_width = int(400 * fill_pct)

    if phase == "won":
        bar_color = "#27ae60"
        status_text = f"CRACKED on turn {attempt}!"
    elif phase == "lost":
        bar_color = "#e74c3c"
        status_text = "CODE UNBROKEN — game over"
    else:
        bar_color = "#e67e22"
        status_text = (
            f"Turn {attempt + 1} of {MAX_ATTEMPTS}  \u00b7  "
            f"{remaining} guess{'es' if remaining != 1 else ''} remaining"
        )

    if _theme_is_terminal(theme):
        track = "#091409"
        status_fill = "#00aa33"
        if phase == "active":
            bar_color = "#00ff44"
        elif phase == "lost":
            bar_color = "#00aa33"
    elif _theme_is_ocean(theme):
        track = "#071a2e"
        status_fill = "#5e86ab"
        if phase == "active":
            bar_color = "#4aa6d8"
    else:
        track = "#1a120b"
        status_fill = "#a08060"

    return (
        f'  <g transform="translate(0, 565)">\n'
        f'    <rect x="30" y="4" width="400" height="8" rx="4" '
        f'fill="{track}"/>\n'
        f'    <rect x="30" y="4" width="{fill_width}" height="8" rx="4" '
        f'fill="{bar_color}"/>\n'
        f'    <text x="230" y="30" text-anchor="middle" fill="{status_fill}" '
        f'font-size="11">{status_text}</text>\n'
        f"  </g>"
    )


def _svg_title_bar(state: dict[str, Any]) -> str:
    """Render the title bar at the top of the board."""
    phase = state.get("phase", "active")
    issue_number = state.get("issue_number", "?")
    theme = state_board_theme(state)

    if phase == "won":
        label = "SOLVED"
    elif phase == "lost":
        label = "GAME OVER"
    else:
        label = f"GAME #{issue_number}"

    if theme == "terminal":
        top_fill = "#0c120c"
        bottom_fill = "#0c120c"
        text_fill = "#2efc71"
        title_prefix = "MISTERMIND v4.2  //  "
    elif theme == "ocean-v5":
        top_fill = "#0f2038"
        bottom_fill = "#0a1a30"
        text_fill = "#9bc7e3"
        title_prefix = "MISTERMIND  ·  "
    else:
        top_fill = "#3d2a1a"
        bottom_fill = "#3d2a1a"
        text_fill = "#f0debf"
        title_prefix = "MISTERMIND  ·  "

    return (
        f'  <rect x="10" y="10" width="440" height="50" rx="18" '
        f'fill="{top_fill}"/>\n'
        f'  <rect x="10" y="42" width="440" height="18" fill="{bottom_fill}"/>\n'
        f'  <text x="230" y="43" text-anchor="middle" fill="{text_fill}" '
        f'font-size="15" font-weight="bold">'
        f"{title_prefix}{label}</text>"
    )


def _svg_column_headers(*, theme: str = DEFAULT_BOARD_THEME) -> str:
    """Render the column header labels."""
    if _theme_is_terminal(theme):
        fill = "#39d872"
    elif _theme_is_ocean(theme):
        fill = "#9ec4e0"
    else:
        fill = "#d0ab7f"
    return (
        f'  <text x="42" y="80" fill="{fill}" font-size="10" '
        'font-weight="bold">#</text>\n'
        f'  <text x="155" y="80" fill="{fill}" font-size="10" '
        'font-weight="bold" text-anchor="middle">GUESS</text>\n'
        f'  <text x="340" y="80" fill="{fill}" font-size="10" '
        'font-weight="bold" text-anchor="middle">RESULT</text>'
    )


def _svg_legend(*, theme: str = DEFAULT_BOARD_THEME) -> str:
    """Render the color legend at the bottom."""
    if _theme_is_terminal(theme):
        fill = "#39d872"
        return (
            '  <g transform="translate(0, 595)">\n'
            '    <rect x="84" y="3" width="9" height="9" fill="#00ff44" stroke="#00ff44" stroke-width="1"/>\n'
            '    <text x="110" y="10" fill="#39d872" font-size="9">bright exact</text>\n'
            '    <rect x="192" y="3" width="9" height="9" fill="#00aa33" opacity="0.65" stroke="#00aa33" stroke-width="1"/>\n'
            '    <text x="216" y="10" fill="#39d872" font-size="9">dim color-only</text>\n'
            '    <rect x="329" y="3" width="9" height="9" fill="none" stroke="#2d7f4a" stroke-width="1.2"/>\n'
            '    <text x="350" y="10" fill="#39d872" font-size="9">off absent</text>\n'
            "  </g>"
        )
    elif _theme_is_ocean(theme):
        fill = "#92bddc"
    else:
        fill = "#c7a074"
    return (
        '  <g transform="translate(0, 595)">\n'
        f'    <text x="230" y="10" text-anchor="middle" fill="{fill}" '
        'font-size="9">'
        "C/L/#   \u2713/\u2713 exact   \u2713/\u2717 color-only   \u2717/\u2717 absent"
        "</text>\n"
        "  </g>"
    )


# ── Win / loss overlays ──────────────────────────────────────────────


def _svg_win_overlay(attempt: int) -> str:
    """Render an animated celebratory overlay when the player wins."""
    return (
        "  <!-- Animated win sequence -->\n"
        "\n"
        "  <!-- Gold border reveal -->\n"
        '  <rect x="10" y="10" width="440" height="600" rx="18" '
        'fill="none" stroke="#c8a84e" stroke-width="3" opacity="0">\n'
        '    <animate attributeName="opacity" from="0" to="1" '
        'dur="0.4s" begin="0s" fill="freeze"/>\n'
        '    <animate attributeName="stroke-width" '
        'values="3;4;3" dur="2s" begin="1.2s" repeatCount="indefinite"/>\n'
        '    <animate attributeName="stroke" '
        'values="#c8a84e;#e8c84e;#c8a84e" dur="2s" begin="1.2s" '
        'repeatCount="indefinite"/>\n'
        "  </rect>\n"
        "\n"
        "  <!-- Overlay panel -->\n"
        '  <g filter="url(#win-glow)" opacity="0">\n'
        '    <animate attributeName="opacity" from="0" to="1" '
        'dur="0.3s" begin="0.3s" fill="freeze"/>\n'
        '    <rect x="60" y="250" width="340" height="120" rx="16" '
        'fill="#1a120b" opacity="0.94"/>\n'
        '    <rect x="62" y="252" width="336" height="116" rx="14" '
        'fill="none" stroke="#27ae60" stroke-width="2" opacity="0">\n'
        '      <animate attributeName="opacity" from="0" to="0.9" '
        'dur="0.3s" begin="0.3s" fill="freeze"/>\n'
        '      <animate attributeName="stroke" '
        'values="#27ae60;#4edc82;#27ae60" dur="2.5s" begin="1.2s" '
        'repeatCount="indefinite"/>\n'
        "    </rect>\n"
        "  </g>\n"
        "\n"
        '  <!-- "CODE CRACKED!" with scale bounce -->\n'
        '  <text x="230" y="295" text-anchor="middle" fill="#27ae60" '
        'font-size="24" font-weight="bold" opacity="0">\n'
        "    CODE CRACKED!\n"
        '    <animate attributeName="opacity" from="0" to="1" '
        'dur="0.15s" begin="0.6s" fill="freeze"/>\n'
        '    <animate attributeName="font-size" '
        'values="18;26;24" dur="0.35s" begin="0.6s" fill="freeze" '
        'calcMode="spline" keySplines="0.34 1.56 0.64 1;0.5 0 0.5 1"/>\n'
        "  </text>\n"
        "\n"
        "  <!-- Stats line -->\n"
        f'  <text x="230" y="325" text-anchor="middle" fill="#a08060" '
        f'font-size="13" opacity="0">\n'
        f"    Solved in {attempt} attempt{'s' if attempt != 1 else ''}\n"
        f'    <animate attributeName="opacity" from="0" to="1" '
        f'dur="0.3s" begin="1.0s" fill="freeze"/>\n'
        f"  </text>\n"
        "\n"
        "  <!-- Subtle star accents -->\n"
        '  <text x="120" y="295" text-anchor="middle" fill="#c8a84e" '
        'font-size="16" opacity="0">\n'
        "    *\n"
        '    <animate attributeName="opacity" values="0;0.8;0" '
        'dur="1.5s" begin="0.8s" repeatCount="indefinite"/>\n'
        "  </text>\n"
        '  <text x="340" y="295" text-anchor="middle" fill="#c8a84e" '
        'font-size="16" opacity="0">\n'
        "    *\n"
        '    <animate attributeName="opacity" values="0;0.8;0" '
        'dur="1.5s" begin="1.3s" repeatCount="indefinite"/>\n'
        "  </text>\n"
        "\n"
        "  <!-- Play again prompt -->\n"
        '  <text x="230" y="350" text-anchor="middle" fill="#605040" '
        'font-size="10" opacity="0">\n'
        "    Open a new issue to play again\n"
        '    <animate attributeName="opacity" from="0" to="0.7" '
        'dur="0.5s" begin="1.5s" fill="freeze"/>\n'
        "  </text>"
    )


def _svg_loss_overlay(solution: list[str] | None = None) -> str:
    """Render an animated game-over overlay when the player loses."""
    parts: list[str] = [
        "  <!-- Animated loss sequence -->",
        "",
        "  <!-- Dark red border fade -->",
        '  <rect x="10" y="10" width="440" height="600" rx="18" '
        'fill="none" stroke="#8b2020" stroke-width="3" opacity="0">',
        '    <animate attributeName="opacity" from="0" to="0.7" '
        'dur="0.8s" begin="0s" fill="freeze"/>',
        "  </rect>",
        "",
        "  <!-- Overlay panel -->",
        '  <g filter="url(#win-glow)" opacity="0">',
        '    <animate attributeName="opacity" from="0" to="1" '
        'dur="0.5s" begin="0.4s" fill="freeze"/>',
        '    <rect x="60" y="250" width="340" height="120" rx="16" fill="#1a120b" opacity="0.94"/>',
        '    <rect x="62" y="252" width="336" height="116" rx="14" '
        'fill="none" stroke="#e74c3c" stroke-width="2" opacity="0.6"/>',
        "  </g>",
        "",
        '  <!-- "GAME OVER" text -->',
        '  <text x="230" y="290" text-anchor="middle" fill="#e74c3c" '
        'font-size="22" font-weight="bold" opacity="0">',
        "    THE CODE STANDS UNBROKEN",
        '    <animate attributeName="opacity" from="0" to="1" '
        'dur="0.4s" begin="0.7s" fill="freeze"/>',
        "  </text>",
    ]

    # Solution reveal: colored pegs with staggered curtain-lift
    if solution:
        peg_cx_start = 230 - (len(solution) - 1) * 20  # center the group
        parts.append("")
        parts.append("  <!-- Solution reveal -->")
        parts.append(
            '  <text x="230" y="315" text-anchor="middle" fill="#605040" '
            'font-size="10" opacity="0">'
        )
        parts.append("    The secret was:")
        parts.append(
            '    <animate attributeName="opacity" from="0" to="0.8" '
            'dur="0.4s" begin="1.2s" fill="freeze"/>'
        )
        parts.append("  </text>")

        for i, color in enumerate(solution):
            cx = peg_cx_start + i * 40
            cy = 345
            hex_color = COLOR_HEX.get(color, EMPTY_HEX)
            delay = f"{1.4 + i * 0.15:.2f}s"
            parts.extend(
                [
                    f'  <circle cx="{cx}" cy="{cy}" r="12" '
                    f'fill="#{hex_color}" filter="url(#peg-shadow)" opacity="0">',
                    f'    <animate attributeName="opacity" from="0" to="1" '
                    f'dur="0.25s" begin="{delay}" fill="freeze"/>',
                    f'    <animate attributeName="cy" from="{cy + 10}" to="{cy}" '
                    f'dur="0.25s" begin="{delay}" fill="freeze"/>',
                    "  </circle>",
                    f'  <circle cx="{cx}" cy="{cy}" r="12" fill="url(#peg-gloss)" opacity="0">',
                    f'    <animate attributeName="opacity" from="0" to="1" '
                    f'dur="0.25s" begin="{delay}" fill="freeze"/>',
                    "  </circle>",
                ]
            )

    parts.extend(
        [
            "",
            "  <!-- Play again prompt -->",
            '  <text x="230" y="355" text-anchor="middle" fill="#605040" '
            'font-size="10" opacity="0">',
            "    Open a new issue to play again",
            '    <animate attributeName="opacity" from="0" to="0.7" '
            'dur="0.5s" begin="2.0s" fill="freeze"/>',
            "  </text>",
        ]
    )

    return "\n".join(parts)


# ── Hint overlay mapping ─────────────────────────────────────────────


def _hint_status_map_for_slot(
    slot: int,
    hint_overlay: dict[str, Any] | None,
) -> dict[int, str] | None:
    """Return a zero-indexed position->status map for one guess row."""
    if not hint_overlay:
        return None
    if int(hint_overlay.get("attempt", -1)) != slot:
        return None

    mapping: dict[int, str] = {}

    for pos in hint_overlay.get("impossible_positions", []):
        idx = int(pos) - 1
        if 0 <= idx < CODE_LENGTH:
            mapping[idx] = "impossible"

    for pos in hint_overlay.get("certain_positions", []):
        idx = int(pos) - 1
        if 0 <= idx < CODE_LENGTH:
            # Certain takes precedence if both are present.
            mapping[idx] = "certain"

    return mapping if mapping else None


# ── Full board renderer ──────────────────────────────────────────────


def render_svg_board(
    state: dict[str, Any],
    *,
    reveal_solution: bool = False,
    solution: list[str] | None = None,
    hint_overlay: dict[str, Any] | None = None,
) -> str:
    """Generate a complete SVG board image for the current game state."""
    history = state.get("history", [])
    attempt = int(state.get("attempt", 0))
    phase = state.get("phase", "active")
    theme = state_board_theme(state)

    parts: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 460 620" '
        "font-family=\"'SF Mono','Fira Code','Courier New',monospace\">",
        _svg_defs(),
        "",
        "  <!-- Board body -->",
        '  <rect x="10" y="10" width="440" height="600" rx="18" '
        'fill="url(#board-bg)" stroke="#3d2a1a" stroke-width="3"/>',
        "",
        "  <!-- Title bar -->",
        _svg_title_bar(state),
        "",
        "  <!-- Column headers -->",
        _svg_column_headers(theme=theme),
        "",
    ]

    # ── Rows ──────────────────────────────────────────────────────────
    ROW_START_Y = 90
    ROW_HEIGHT = 46

    for slot in range(1, MAX_ATTEMPTS + 1):
        y = ROW_START_Y + (slot - 1) * ROW_HEIGHT

        if slot <= len(history):
            entry = history[slot - 1]
            parts.append(
                _svg_row_guessed(
                    y,
                    slot,
                    entry["guess"],
                    int(entry["black"]),
                    int(entry["white"]),
                    _hint_status_map_for_slot(slot, hint_overlay),
                    theme=theme,
                )
            )
        elif slot == attempt + 1 and phase == "active":
            parts.append(_svg_row_active(y, slot, theme=theme))
        else:
            parts.append(_svg_row_future(y, slot, theme=theme))

    parts.append("")

    # ── Footer ────────────────────────────────────────────────────────
    parts.append(_svg_progress_bar(attempt, phase, theme=theme))
    parts.append("")
    parts.append(_svg_legend(theme=theme))

    # ── Win / loss overlay ────────────────────────────────────────────
    if phase == "won":
        parts.append("")
        parts.append(_svg_win_overlay(attempt))
    elif phase == "lost" and reveal_solution and solution:
        parts.append("")
        parts.append(_svg_loss_overlay(solution))

    parts.append("</svg>")
    return "\n".join(parts)


def svg_board_as_img_tag(
    state: dict[str, Any],
    *,
    reveal_solution: bool = False,
    solution: list[str] | None = None,
    alt: str = "MisterMind Board",
    hint_overlay: dict[str, Any] | None = None,
) -> str:
    """Render the SVG board and wrap it in a base64-encoded <img> tag."""
    svg = render_svg_board(
        state,
        reveal_solution=reveal_solution,
        solution=solution,
        hint_overlay=hint_overlay,
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{encoded}" alt="{alt}" width="460"/>'


# ── Template hydration ───────────────────────────────────────────────


def _load_board_template(template_path: str = BOARD_TEMPLATE_PATH) -> str:
    """Load the SVG board template from the assets directory."""
    resolved = resolve_runtime_path(template_path)
    return resolved.read_text(encoding="utf-8")


def hydrate_board_template(
    state: dict[str, Any],
    *,
    reveal_solution: bool = False,
    solution: list[str] | None = None,
    template: str | None = None,
    hint_overlay: dict[str, Any] | None = None,
    template_path: str = BOARD_TEMPLATE_PATH,
) -> str:
    """Fill the SVG board template placeholders with the current game state."""
    if template is None:
        template = _load_board_template(template_path)

    history = state.get("history", [])
    attempt = int(state.get("attempt", 0))
    phase = state.get("phase", "active")
    theme = state_board_theme(state)

    # Title
    title_svg = _svg_title_bar(state)
    template = template.replace("{{TITLE}}", title_svg)

    # Rows
    ROW_START_Y = 90
    ROW_HEIGHT = 46
    for slot in range(1, MAX_ATTEMPTS + 1):
        y = ROW_START_Y + (slot - 1) * ROW_HEIGHT
        tag = f"{{{{ROW_{slot:02d}}}}}"

        if slot <= len(history):
            entry = history[slot - 1]
            row_svg = _svg_row_guessed(
                y,
                slot,
                entry["guess"],
                int(entry["black"]),
                int(entry["white"]),
                _hint_status_map_for_slot(slot, hint_overlay),
                theme=theme,
            )
        elif slot == attempt + 1 and phase == "active":
            row_svg = _svg_row_active(y, slot, theme=theme)
        else:
            row_svg = _svg_row_future(y, slot, theme=theme)

        template = template.replace(tag, row_svg)

    # Progress bar
    template = template.replace(
        "{{PROGRESS}}",
        _svg_progress_bar(attempt, phase, theme=theme),
    )

    # Overlay
    if phase == "won":
        overlay = _svg_win_overlay(attempt)
    elif phase == "lost" and reveal_solution and solution:
        overlay = _svg_loss_overlay(solution)
    else:
        overlay = ""
    template = template.replace("{{OVERLAY}}", overlay)

    return template
