"""
Stats accumulation, leaderboard ranking, and SVG leaderboard renderer.

Tracks per-player win/loss records, streaks, hall-of-fame entries, and
recent-game logs.  Provides pure-function update_stats() and rendering
helpers for embedding stats in comments and generating the standalone
leaderboard SVG widget.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import TYPE_CHECKING, Any

from mistermind.constants import (
    BOARD_ASSET_BRANCH,
    HALL_OF_FAME_CAP,
    LEADERBOARD_MIN_GAMES,
    LEADERBOARD_SVG_PATH,
    LEADERBOARD_TOP_N,
    RECENT_GAMES_CAP,
    STATS_PATH,
    STATS_SCHEMA,
)

if TYPE_CHECKING:
    from mistermind.github_api import GitHubAPI


# ── Data structures ──────────────────────────────────────────────────


def _empty_stats() -> dict[str, Any]:
    """Return a fresh stats structure."""
    return {
        "schema": STATS_SCHEMA,
        "updated_at": "",
        "games_played": 0,
        "games_won": 0,
        "games_lost": 0,
        "players": {},
        "hall_of_fame": [],
        "recent_games": [],
    }


def _empty_player_stats() -> dict[str, Any]:
    return {
        "games": 0,
        "wins": 0,
        "losses": 0,
        "best_streak": 0,
        "current_streak": 0,
        "best_score": 0,  # fewest attempts to win (0 = never won)
        "avg_score": 0.0,
        "total_guesses": 0,
        "last_game": "",
        "last_issue": 0,
    }


# ── Persistence ──────────────────────────────────────────────────────


def load_stats(api: GitHubAPI) -> tuple[dict[str, Any], str | None]:
    """Load stats from the game-boards branch.

    Returns (stats_dict, sha) where sha is needed for conditional update.
    If the file doesn't exist, returns (empty_stats, None).
    """
    existing = api.get_file_contents(STATS_PATH, ref=BOARD_ASSET_BRANCH)
    if existing is None:
        return _empty_stats(), None
    try:
        raw = base64.b64decode(existing["content"]).decode("utf-8")
        data = json.loads(raw)
        return data, existing["sha"]
    except Exception as exc:
        print(f"Stats file corrupt, resetting: {exc}")
        return _empty_stats(), existing.get("sha")


def save_stats(
    api: GitHubAPI,
    stats: dict[str, Any],
    *,
    sha: str | None,
) -> bool:
    """Write stats back to the game-boards branch.

    Uses SHA-based conditional update for optimistic concurrency.
    Returns True on success.
    """
    content = json.dumps(stats, indent=2, sort_keys=False)
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    try:
        api.create_or_update_file(
            STATS_PATH,
            content_b64=content_b64,
            message="stats: update mistermind leaderboard",
            branch=BOARD_ASSET_BRANCH,
            sha=sha,
        )
        print("Stats saved successfully.")
        return True
    except Exception as exc:
        print(f"Failed to save stats: {exc}")
        return False


# ── Pure update function ─────────────────────────────────────────────


def update_stats(
    stats: dict[str, Any],
    *,
    player: str,
    result: str,  # "won" or "lost"
    attempts: int,
    issue_number: int,
) -> dict[str, Any]:
    """Apply a completed game result to the stats dict (pure function)."""
    now = dt.datetime.now(dt.UTC).isoformat()
    stats["updated_at"] = now
    stats["games_played"] = stats.get("games_played", 0) + 1

    if result == "won":
        stats["games_won"] = stats.get("games_won", 0) + 1
    else:
        stats["games_lost"] = stats.get("games_lost", 0) + 1

    # ── Player record ────────────────────────────────────────────────
    players = stats.setdefault("players", {})
    p = players.get(player)
    if p is None:
        p = _empty_player_stats()
        players[player] = p

    p["games"] = p.get("games", 0) + 1
    p["total_guesses"] = p.get("total_guesses", 0) + attempts
    p["last_game"] = now
    p["last_issue"] = issue_number

    if result == "won":
        p["wins"] = p.get("wins", 0) + 1
        p["current_streak"] = p.get("current_streak", 0) + 1
        p["best_streak"] = max(p.get("best_streak", 0), p["current_streak"])
        prev_best = p.get("best_score", 0)
        if prev_best == 0 or attempts < prev_best:
            p["best_score"] = attempts
        # Running average of winning scores
        total_wins = p["wins"]
        if total_wins == 1:
            p["avg_score"] = float(attempts)
        else:
            old_avg = p.get("avg_score", 0.0)
            p["avg_score"] = round(old_avg + (attempts - old_avg) / total_wins, 2)
    else:
        p["losses"] = p.get("losses", 0) + 1
        p["current_streak"] = 0

    # ── Hall of fame (fastest wins) ──────────────────────────────────
    if result == "won":
        hof: list[dict[str, Any]] = stats.setdefault("hall_of_fame", [])
        hof.append(
            {
                "player": player,
                "attempts": attempts,
                "issue": issue_number,
                "date": now,
            }
        )
        hof.sort(key=lambda e: (e["attempts"], e["date"]))
        stats["hall_of_fame"] = hof[:HALL_OF_FAME_CAP]

    # ── Recent games (rolling window) ────────────────────────────────
    recent: list[dict[str, Any]] = stats.setdefault("recent_games", [])
    recent.append(
        {
            "issue": issue_number,
            "player": player,
            "result": result,
            "attempts": attempts,
            "date": now,
        }
    )
    stats["recent_games"] = recent[-RECENT_GAMES_CAP:]

    return stats


# ── Display helpers ──────────────────────────────────────────────────


def player_stats_line(stats: dict[str, Any], player: str) -> str | None:
    """Format a one-line personal stats summary for embedding in comments.

    Returns None if the player has no recorded games.
    """
    players = stats.get("players", {})
    p = players.get(player)
    if p is None:
        return None

    wins = p.get("wins", 0)
    losses = p.get("losses", 0)
    best = p.get("best_score", 0)
    streak = p.get("current_streak", 0)
    avg = p.get("avg_score", 0.0)

    parts = [f"{wins}W / {losses}L"]
    if best > 0:
        parts.append(f"Best: {best}")
    if avg > 0:
        parts.append(f"Avg: {avg:.1f}")
    if streak > 1:
        parts.append(f"Streak: {streak}")

    return " | ".join(parts)


def player_leaderboard_rank(stats: dict[str, Any], player: str) -> str | None:
    """Return a string like '#2 of 15 players' or None if unranked."""
    players = stats.get("players", {})
    if player not in players:
        return None

    # Rank by win rate (minimum LEADERBOARD_MIN_GAMES games)
    eligible = [
        (name, p) for name, p in players.items() if p.get("games", 0) >= LEADERBOARD_MIN_GAMES
    ]
    if not eligible:
        return None

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float, int]:
        _, p = item
        games = p.get("games", 1)
        win_rate = p.get("wins", 0) / max(games, 1)
        avg = p.get("avg_score", 10.0)
        return (-win_rate, avg, -p.get("wins", 0))

    ranked = sorted(eligible, key=sort_key)
    for i, (name, _) in enumerate(ranked, 1):
        if name == player:
            return f"#{i} of {len(ranked)} players"
    return None


# ── Leaderboard SVG ──────────────────────────────────────────────────


def render_leaderboard_svg(stats: dict[str, Any]) -> str:
    """Generate a self-contained SVG leaderboard widget."""
    players = stats.get("players", {})
    total_games = stats.get("games_played", 0)
    total_won = stats.get("games_won", 0)
    hof = stats.get("hall_of_fame", [])

    # Build ranked player list (by win rate, min games threshold)
    eligible = [
        (name, p) for name, p in players.items() if p.get("games", 0) >= LEADERBOARD_MIN_GAMES
    ]

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float, int]:
        _, p = item
        games = p.get("games", 1)
        win_rate = p.get("wins", 0) / max(games, 1)
        avg = p.get("avg_score", 10.0)
        return (-win_rate, avg, -p.get("wins", 0))

    ranked = sorted(eligible, key=sort_key)[:LEADERBOARD_TOP_N]

    qualified_count = len(eligible)

    width = 760
    center_x = width // 2
    frame_x = 14
    frame_w = width - 2 * frame_x

    # Calculate SVG height dynamically
    header_h = 138
    table_header_h = 38
    row_h = 42
    table_rows = max(len(ranked), 1)  # at least 1 for "no data" row
    table_h = table_header_h + table_rows * row_h
    section_gap = 16
    hof_header_h = 48
    hof_row_h = 32
    hof_rows = min(len(hof), 3)
    hof_h = hof_header_h + max(hof_rows, 1) * hof_row_h
    footer_h = 64
    total_h = header_h + table_h + section_gap + hof_h + footer_h + 24
    win_pct_global = round(100 * total_won / max(total_games, 1))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_h}" '
        f"font-family=\"'SF Mono','Fira Code','Courier New',monospace\">",
        "  <defs>",
        '    <linearGradient id="lb-bg" x1="0" y1="0" x2="0" y2="1">',
        '      <stop offset="0%" stop-color="#27180f"/>',
        '      <stop offset="55%" stop-color="#1f130c"/>',
        '      <stop offset="100%" stop-color="#160e08"/>',
        "    </linearGradient>",
        '    <linearGradient id="lb-panel" x1="0" y1="0" x2="1" y2="1">',
        '      <stop offset="0%" stop-color="#4e2d1c"/>',
        '      <stop offset="100%" stop-color="#311c12"/>',
        "    </linearGradient>",
        '    <linearGradient id="lb-gold" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#d9a356"/>',
        '      <stop offset="50%" stop-color="#f4ddb0"/>',
        '      <stop offset="100%" stop-color="#bf7c35"/>',
        "    </linearGradient>",
        '    <linearGradient id="lb-ribbon" x1="0" y1="0" x2="0" y2="1">',
        '      <stop offset="0%" stop-color="#7d4025"/>',
        '      <stop offset="100%" stop-color="#5a2f1b"/>',
        "    </linearGradient>",
        '    <linearGradient id="lb-row-even" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#2c1b11"/>',
        '      <stop offset="100%" stop-color="#23160e"/>',
        "    </linearGradient>",
        '    <linearGradient id="lb-row-odd" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#25170f"/>',
        '      <stop offset="100%" stop-color="#1f130c"/>',
        "    </linearGradient>",
        '    <linearGradient id="lb-chip" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#3a2417"/>',
        '      <stop offset="100%" stop-color="#2b1b12"/>',
        "    </linearGradient>",
        "  </defs>",
        "",
        f'  <rect x="{frame_x}" y="8" width="{frame_w}" height="{total_h - 16}" rx="18" '
        'fill="url(#lb-bg)" stroke="#735033" stroke-width="3"/>',
        '  <polygon points="34,46 124,28 124,94 34,112" fill="url(#lb-ribbon)" stroke="#a15d35" stroke-width="2"/>',
        f'  <polygon points="{width - 34},46 {width - 124},28 {width - 124},94 {width - 34},112" '
        'fill="url(#lb-ribbon)" stroke="#a15d35" stroke-width="2"/>',
        '  <rect x="74" y="18" width="612" height="106" rx="20" fill="url(#lb-panel)" stroke="#ad7646" stroke-width="2"/>',
        f'  <text x="{center_x}" y="50" text-anchor="middle" fill="#f2dfc2" '
        "font-size=\"14\" letter-spacing=\"3\" font-family=\"'Cinzel','Copperplate','Times New Roman',serif\">GRAND CIRCLE</text>",
        f'  <text x="{center_x}" y="83" text-anchor="middle" fill="url(#lb-gold)" '
        'font-size="34" font-weight="700" letter-spacing="1.5" font-family="\'Cinzel\',\'Copperplate\',\'Times New Roman\',serif">MISTERMIND LEADERBOARD</text>',
        f'  <text x="{center_x}" y="108" text-anchor="middle" fill="#d8ad72" '
        f'font-size="12">CHAMPIONS TABLE  •  Ranked by win rate (min {LEADERBOARD_MIN_GAMES} games)</text>',
        "",
        "  <!-- Header stat chips -->",
        '  <rect x="46" y="102" width="208" height="24" rx="12" fill="url(#lb-chip)" stroke="#8b603e" stroke-width="1.2"/>',
        f'  <text x="150" y="118" text-anchor="middle" fill="#e7c99c" font-size="11">Games: {total_games}</text>',
        '  <rect x="276" y="102" width="208" height="24" rx="12" fill="url(#lb-chip)" stroke="#8b603e" stroke-width="1.2"/>',
        f'  <text x="380" y="118" text-anchor="middle" fill="#e7c99c" font-size="11">Wins: {total_won}</text>',
        '  <rect x="506" y="102" width="208" height="24" rx="12" fill="url(#lb-chip)" stroke="#8b603e" stroke-width="1.2"/>',
        f'  <text x="610" y="118" text-anchor="middle" fill="#e7c99c" font-size="11">Qualified: {qualified_count}</text>',
    ]

    y = header_h

    # ── Column headers ───────────────────────────────────────────────
    parts.extend(
        [
            "",
            f'  <text x="70" y="{y + 24}" fill="#c9a373" font-size="12" font-weight="bold" text-anchor="middle">#</text>',
            f'  <text x="120" y="{y + 24}" fill="#c9a373" font-size="12" font-weight="bold">PLAYER</text>',
            f'  <text x="365" y="{y + 24}" fill="#c9a373" font-size="12" font-weight="bold" text-anchor="middle">W-L</text>',
            f'  <text x="460" y="{y + 24}" fill="#c9a373" font-size="12" font-weight="bold" text-anchor="middle">WIN %</text>',
            f'  <text x="555" y="{y + 24}" fill="#c9a373" font-size="12" font-weight="bold" text-anchor="middle">BEST</text>',
            f'  <text x="645" y="{y + 24}" fill="#c9a373" font-size="12" font-weight="bold" text-anchor="middle">AVG</text>',
        ]
    )
    y += table_header_h

    # ── Player rows ──────────────────────────────────────────────────
    if not ranked:
        parts.extend(
            [
                f'  <rect x="34" y="{y}" width="692" height="{row_h}" rx="8" fill="url(#lb-row-even)" stroke="#4f3422" stroke-width="1"/>',
                f'  <text x="{center_x}" y="{y + 27}" text-anchor="middle" '
                f'fill="#86684a" font-size="14" font-style="italic">'
                f"No qualified players yet (min {LEADERBOARD_MIN_GAMES} games)</text>",
            ]
        )
        y += row_h
    else:
        for i, (name, p) in enumerate(ranked):
            row_y = y + i * row_h
            rank_num = i + 1

            row_fill = "url(#lb-row-even)" if i % 2 == 0 else "url(#lb-row-odd)"
            parts.append(
                f'  <rect x="34" y="{row_y}" width="692" height="{row_h}" '
                f'rx="8" fill="{row_fill}" stroke="#4f3422" stroke-width="1"/>'
            )

            wins = p.get("wins", 0)
            losses = p.get("losses", 0)
            games = p.get("games", 1)
            win_pct = round(100 * wins / max(games, 1))
            best = p.get("best_score", 0)
            avg = p.get("avg_score", 0.0)

            rank_fill = "#9f7b58"
            if rank_num == 1:
                rank_fill = "#d7b15f"
            elif rank_num == 2:
                rank_fill = "#b9bec6"
            elif rank_num == 3:
                rank_fill = "#bc7d45"

            text_y = row_y + 27

            parts.extend(
                [
                    f'  <circle cx="70" cy="{row_y + 21}" r="13" fill="#22150d" stroke="{rank_fill}" stroke-width="2"/>',
                    f'  <text x="70" y="{text_y}" fill="{rank_fill}" '
                    f'font-size="12" font-weight="bold" text-anchor="middle">{rank_num}</text>',
                    f'  <text x="120" y="{text_y}" fill="#ead6b7" font-size="13">@{name[:20]}</text>',
                    f'  <text x="365" y="{text_y}" fill="#d4ac7d" font-size="12" text-anchor="middle">{wins}-{losses}</text>',
                    f'  <text x="460" y="{text_y}" fill="#d4ac7d" font-size="12" text-anchor="middle">{win_pct}%</text>',
                    f'  <text x="555" y="{text_y}" fill="#d4ac7d" font-size="12" text-anchor="middle">{best if best > 0 else "-"}</text>',
                    f'  <text x="645" y="{text_y}" fill="#d4ac7d" font-size="12" text-anchor="middle">{avg:.1f}</text>',
                ]
            )
        y += len(ranked) * row_h

    y += section_gap

    # ── Hall of Fame ─────────────────────────────────────────────────
    parts.extend(
        [
            "",
            f'  <line x1="44" y1="{y}" x2="716" y2="{y}" stroke="#5d3f29" stroke-width="1.5"/>',
            f'  <text x="{center_x}" y="{y + 28}" text-anchor="middle" fill="#e4bd74" '
            f"font-size=\"17\" font-weight=\"bold\" font-family=\"'Cinzel','Copperplate','Times New Roman',serif\">FASTEST SOLVES</text>",
            f'  <text x="{center_x}" y="{y + 44}" text-anchor="middle" fill="#ba8f60" font-size="10">Hall of Fame top clears</text>',
        ]
    )
    y += hof_header_h

    if not hof:
        parts.append(
            f'  <text x="{center_x}" y="{y + 20}" text-anchor="middle" '
            f'fill="#86684a" font-size="13" font-style="italic">'
            f"No wins recorded yet</text>"
        )
    else:
        medals = ["I", "II", "III"]
        medal_colors = ["#d7b15f", "#b9bec6", "#bc7d45"]
        for i, entry in enumerate(hof[:3]):
            ey = y + i * hof_row_h
            marker = medals[i] if i < 3 else str(i + 1)
            marker_color = medal_colors[i] if i < 3 else "#9f7b58"

            parts.extend(
                [
                    f'  <rect x="42" y="{ey}" width="676" height="{hof_row_h - 2}" rx="8" fill="url(#lb-row-odd)" stroke="#4f3422" stroke-width="1"/>',
                    f'  <circle cx="70" cy="{ey + 15}" r="10" fill="#24170f" stroke="{marker_color}" stroke-width="1.7"/>',
                    f'  <text x="70" y="{ey + 19}" text-anchor="middle" fill="{marker_color}" font-size="10" font-weight="bold">{marker}</text>',
                    f'  <text x="90" y="{ey + 20}" fill="#d8b383" font-size="12">@{entry["player"]}</text>',
                    f'  <text x="692" y="{ey + 20}" text-anchor="end" fill="#d8b383" font-size="12">'
                    f"{entry['attempts']} attempt{'s' if entry['attempts'] != 1 else ''}  •  #{entry['issue']}</text>",
                ]
            )
    y += max(hof_rows, 1) * hof_row_h

    # ── Footer ───────────────────────────────────────────────────────
    parts.extend(
        [
            "",
            f'  <line x1="44" y1="{y + 10}" x2="716" y2="{y + 10}" stroke="#5d3f29" stroke-width="1.5"/>',
            f'  <text x="{center_x}" y="{y + 35}" text-anchor="middle" fill="#b69166" font-size="11">'
            f"{total_games} games played  |  {win_pct_global}% win rate</text>",
            f'  <text x="{center_x}" y="{y + 53}" text-anchor="middle" fill="#b69166" font-size="11">'
            f"Open a new issue with the MisterMind template to play</text>",
        ]
    )

    parts.append("</svg>")
    return "\n".join(parts)


def upload_leaderboard_svg(
    api: GitHubAPI,
    stats: dict[str, Any],
) -> None:
    """Regenerate and upload the leaderboard SVG to the asset branch."""
    svg = render_leaderboard_svg(stats)
    content_b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    existing = api.get_file_contents(LEADERBOARD_SVG_PATH, ref=BOARD_ASSET_BRANCH)
    sha = existing["sha"] if existing else None
    try:
        api.create_or_update_file(
            LEADERBOARD_SVG_PATH,
            content_b64=content_b64,
            message="leaderboard: regenerate",
            branch=BOARD_ASSET_BRANCH,
            sha=sha,
        )
        print("Leaderboard SVG updated.")
    except Exception as exc:
        print(f"Failed to update leaderboard SVG: {exc}")
