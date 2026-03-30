#!/usr/bin/env python3
"""Build leaderboard summaries from terminal game records."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

MM_START = "<!-- MM_LEADERBOARD_START -->"
MM_END = "<!-- MM_LEADERBOARD_END -->"
GAME_RESULT_SCHEMA = "mistermind-game-result-v1"
TOP_N = 10
CARD_TOP_N = 5


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_record(path: Path, data: dict[str, Any]) -> dict[str, Any] | None:
    schema = str(data.get("schema", "")).strip()
    if schema and schema != GAME_RESULT_SCHEMA:
        return None

    issue = _safe_int(data.get("issue"), -1)
    if issue < 0:
        return None

    player = str(data.get("player", "")).strip().lower()
    if not player:
        return None

    variant = str(data.get("variant", "classic")).strip().lower() or "classic"
    if variant not in {"classic", "hint", "perfectionist"}:
        variant = "classic"

    result = str(data.get("result", "")).strip().lower()
    if result not in {"won", "lost"}:
        return None

    attempts = max(_safe_int(data.get("attempts"), 0), 0)
    completed_at = str(data.get("completed_at", "")).strip()
    if not completed_at:
        completed_at = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC).isoformat()

    return {
        "schema": GAME_RESULT_SCHEMA,
        "issue": issue,
        "player": player,
        "variant": variant,
        "result": result,
        "attempts": attempts,
        "completed_at": completed_at,
    }


def load_game_records(games_root: Path) -> list[dict[str, Any]]:
    if not games_root.exists():
        return []

    records: list[dict[str, Any]] = []
    for path in sorted(games_root.glob("*.json")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"Skipping malformed JSON: {path}")
            continue
        if not isinstance(payload, dict):
            print(f"Skipping non-object record: {path}")
            continue

        normalized = _normalize_record(path, payload)
        if normalized is None:
            print(f"Skipping invalid game record: {path}")
            continue
        records.append(normalized)

    return sorted(records, key=lambda r: (int(r["issue"]), str(r["completed_at"])))


def _sort_counts(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def build_leaderboard_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    games_completed = Counter[str]()
    classic_wins = Counter[str]()
    perfectionist_wins = Counter[str]()
    classic_wins_by_moves: dict[int, Counter[str]] = defaultdict(Counter)

    for record in records:
        player = str(record["player"])
        games_completed[player] += 1

        if record["result"] != "won":
            continue
        variant = str(record["variant"])
        attempts = _safe_int(record["attempts"], 0)
        if variant == "classic":
            classic_wins[player] += 1
            classic_wins_by_moves[attempts][player] += 1
        elif variant == "perfectionist":
            perfectionist_wins[player] += 1

    as_of = max(
        (str(record["completed_at"]) for record in records),
        default=dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
    )

    moves_rows: list[dict[str, Any]] = []
    for attempts in sorted(classic_wins_by_moves.keys()):
        winners = [
            {"player": player, "wins": wins}
            for player, wins in _sort_counts(classic_wins_by_moves[attempts])
        ]
        moves_rows.append({"attempts": attempts, "winners": winners})

    quick_draw_rows: list[dict[str, Any]] = []
    for attempts in sorted(classic_wins_by_moves.keys()):
        for player, wins in _sort_counts(classic_wins_by_moves[attempts]):
            quick_draw_rows.append({"player": player, "attempts": attempts, "wins": wins})

    return {
        "generated_at": as_of,
        "source_games": len(records),
        "most_games_completed": [
            {"player": player, "games": games}
            for player, games in _sort_counts(games_completed)[:TOP_N]
        ],
        "most_classic_wins": [
            {"player": player, "wins": wins} for player, wins in _sort_counts(classic_wins)[:TOP_N]
        ],
        "classic_wins_by_moves": moves_rows,
        "quick_draw_classic": quick_draw_rows[:TOP_N],
        "perfectionist_winners": [
            {"player": player, "wins": wins} for player, wins in _sort_counts(perfectionist_wins)
        ],
    }


def _render_leaderboard_card_svg(
    *,
    title: str,
    subtitle: str,
    rows: list[tuple[str, str]],
) -> str:
    """Render one mobile-legible leaderboard card SVG."""
    width = 760
    height = 390
    max_rows = CARD_TOP_N
    padded_rows = rows[:max_rows]
    if not padded_rows:
        padded_rows = [("No data yet", "--")]
    while len(padded_rows) < max_rows:
        padded_rows.append(("—", "—"))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f"font-family=\"'SF Mono','Fira Code','Courier New',monospace\">",
        "  <defs>",
        '    <linearGradient id="card-bg" x1="0" y1="0" x2="0" y2="1">',
        '      <stop offset="0%" stop-color="#2f1c11"/>',
        '      <stop offset="100%" stop-color="#1b120b"/>',
        "    </linearGradient>",
        '    <linearGradient id="card-panel" x1="0" y1="0" x2="1" y2="1">',
        '      <stop offset="0%" stop-color="#5a3520"/>',
        '      <stop offset="100%" stop-color="#3a2416"/>',
        "    </linearGradient>",
        '    <linearGradient id="card-gold" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#c88842"/>',
        '      <stop offset="50%" stop-color="#f6e3bc"/>',
        '      <stop offset="100%" stop-color="#b16e2b"/>',
        "    </linearGradient>",
        "  </defs>",
        '  <rect x="8" y="8" width="744" height="374" rx="20" fill="url(#card-bg)" stroke="#8d633f" stroke-width="3"/>',
        '  <rect x="22" y="22" width="716" height="92" rx="14" fill="url(#card-panel)" stroke="#a9794f" stroke-width="2"/>',
        f'  <text x="{width // 2}" y="68" text-anchor="middle" fill="url(#card-gold)" '
        'font-size="40" font-weight="700" letter-spacing="1.8" '
        f'font-family="\'Cinzel\',\'Copperplate\',\'Times New Roman\',serif">{html.escape(title)}</text>',
        f'  <text x="{width // 2}" y="96" text-anchor="middle" fill="#d9b68b" '
        'font-size="21" letter-spacing="0.8">'
        f"{html.escape(subtitle)}</text>",
    ]

    row_y = 128
    row_h = 48
    for idx, (label, value) in enumerate(padded_rows, start=1):
        fill = "#2a1a10" if idx % 2 == 1 else "#24170f"
        parts.extend(
            [
                f'  <rect x="24" y="{row_y}" width="712" height="44" rx="9" fill="{fill}" stroke="#5b3d26" stroke-width="1"/>',
                f'  <text x="42" y="{row_y + 30}" fill="#e5cda9" font-size="24" font-weight="700">{idx}</text>',
                f'  <text x="88" y="{row_y + 30}" fill="#f0dcc0" font-size="22">{html.escape(label)}</text>',
                f'  <text x="716" y="{row_y + 30}" text-anchor="end" fill="#f0dcc0" font-size="22" font-weight="700">'
                f"{html.escape(value)}</text>",
            ]
        )
        row_y += row_h

    parts.append("</svg>")
    return "\n".join(parts)


def write_leaderboard_cards(summary: dict[str, Any], cards_dir: Path) -> list[str]:
    """Generate and write the four README leaderboard cards."""
    cards_dir.mkdir(parents=True, exist_ok=True)

    games_completed = list(summary.get("most_games_completed", []))
    classic_wins = list(summary.get("most_classic_wins", []))
    quick_draw = list(summary.get("quick_draw_classic", []))
    perfectionists = list(summary.get("perfectionist_winners", []))

    champions_rows = [
        (f"@{row['player']}", f"{row['wins']} wins") for row in classic_wins[:CARD_TOP_N]
    ]
    commitment_rows = [
        (f"@{row['player']}", f"{row['games']} games") for row in games_completed[:CARD_TOP_N]
    ]
    quick_draw_rows = [
        (f"@{row['player']}", f"{row['attempts']} moves") for row in quick_draw[:CARD_TOP_N]
    ]
    perfectionist_rows = [
        (f"@{row['player']}", f"{row['wins']} wins") for row in perfectionists[:CARD_TOP_N]
    ]

    cards = {
        "readme-leaderboard-card-champions.svg": _render_leaderboard_card_svg(
            title="CHAMPIONS",
            subtitle="Most Wins (Classic Mode)",
            rows=champions_rows,
        ),
        "readme-leaderboard-card-commitment.svg": _render_leaderboard_card_svg(
            title="COMMITMENT",
            subtitle="Most Games Played (Any Mode)",
            rows=commitment_rows,
        ),
        "readme-leaderboard-card-quick-draw.svg": _render_leaderboard_card_svg(
            title="QUICK DRAW",
            subtitle="Wins in Fewest Moves (Classic Mode)",
            rows=quick_draw_rows,
        ),
        "readme-leaderboard-card-perfectionists.svg": _render_leaderboard_card_svg(
            title="PERFECTIONISTS",
            subtitle="Most Wins (Perfectionist Mode)",
            rows=perfectionist_rows,
        ),
    }

    written: list[str] = []
    for filename, content in cards.items():
        path = cards_dir / filename
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written


def render_leaderboard_markdown(summary: dict[str, Any]) -> str:
    generated_at = str(summary["generated_at"])
    source_games = _safe_int(summary["source_games"], 0)
    has_data = source_games > 0

    lines = [
        "### Leaderboards",
        f"_As of (UTC): {generated_at or 'n/a'} from {source_games} completed games_",
        """(Leaderboards are updated roughly every 15 minutes)""",
        "",
        '<table align="center">',
        "  <tr>",
        '    <td><picture><img src="assets/readme-leaderboard-card-champions.svg" alt="Champions Card" width="460" /></picture></td>',
        '    <td><picture><img src="assets/readme-leaderboard-card-commitment.svg" alt="Commitment Card" width="460" /></picture></td>',
        "  </tr>",
        "  <tr>",
        '    <td><picture><img src="assets/readme-leaderboard-card-quick-draw.svg" alt="Quick Draw Card" width="460" /></picture></td>',
        '    <td><picture><img src="assets/readme-leaderboard-card-perfectionists.svg" alt="Perfectionists Card" width="460" /></picture></td>',
        "  </tr>",
        "</table>",
    ]
    if not has_data:
        lines.append("")
        lines.append("<em>No completed games yet.</em>")

    return "\n".join(lines)


def update_readme_block(readme_path: Path, block: str) -> bool:
    content = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(MM_START)}\n.*?\n{re.escape(MM_END)}",
        flags=re.DOTALL,
    )
    replacement = f"{MM_START}\n{block}\n{MM_END}"
    updated, count = pattern.subn(replacement, content, count=1)
    if count != 1:
        raise RuntimeError(f"Could not locate unique leaderboard marker block in {readme_path}")
    if updated == content:
        return False
    readme_path.write_text(updated, encoding="utf-8")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--games-root",
        type=Path,
        required=True,
        help="Directory containing terminal game JSON records (data/games).",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        required=True,
        help="README path to update between leaderboard markers.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        required=True,
        help="Path for machine-readable leaderboard summary JSON.",
    )
    parser.add_argument(
        "--cards-dir",
        type=Path,
        required=False,
        default=None,
        help="Optional directory to write README leaderboard SVG cards.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_game_records(args.games_root)
    summary = build_leaderboard_summary(records)
    markdown_block = render_leaderboard_markdown(summary)
    changed = update_readme_block(args.readme, markdown_block)

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    written_cards: list[str] = []
    if args.cards_dir is not None:
        written_cards = write_leaderboard_cards(summary, args.cards_dir)

    print(f"Loaded {len(records)} game records from {args.games_root}")
    print(f"README updated: {changed}")
    print(f"Wrote leaderboard JSON: {args.json_out}")
    if written_cards:
        print(f"Wrote leaderboard cards: {', '.join(written_cards)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
