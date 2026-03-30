from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path


def _write_record(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_build_leaderboards_script(tmp_path: Path) -> None:
    games_root = tmp_path / "games"
    games_root.mkdir(parents=True, exist_ok=True)

    _write_record(
        games_root / "1.json",
        {
            "schema": "mistermind-game-result-v1",
            "issue": 1,
            "player": "alice",
            "variant": "classic",
            "result": "won",
            "attempts": 4,
            "completed_at": "2026-04-01T00:00:00+00:00",
        },
    )
    _write_record(
        games_root / "2.json",
        {
            "schema": "mistermind-game-result-v1",
            "issue": 2,
            "player": "bob",
            "variant": "classic",
            "result": "won",
            "attempts": 4,
            "completed_at": "2026-04-01T00:10:00+00:00",
        },
    )
    _write_record(
        games_root / "3.json",
        {
            "schema": "mistermind-game-result-v1",
            "issue": 3,
            "player": "alice",
            "variant": "perfectionist",
            "result": "won",
            "attempts": 5,
            "completed_at": "2026-04-01T00:20:00+00:00",
        },
    )
    _write_record(
        games_root / "4.json",
        {
            "schema": "mistermind-game-result-v1",
            "issue": 4,
            "player": "alice",
            "variant": "hint",
            "result": "lost",
            "attempts": 10,
            "completed_at": "2026-04-01T00:30:00+00:00",
        },
    )
    _write_record(
        games_root / "5.json",
        {
            "schema": "mistermind-game-result-v1",
            "issue": 5,
            "player": "zoe",
            "variant": "classic",
            "result": "won",
            "attempts": 3,
            "completed_at": "2026-04-01T00:40:00+00:00",
        },
    )

    readme = tmp_path / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# test",
                "",
                "<!-- MM_LEADERBOARD_START -->",
                "- placeholder",
                "<!-- MM_LEADERBOARD_END -->",
                "",
            ]
        ),
        encoding="utf-8",
    )
    json_out = tmp_path / "data" / "leaderboards.json"
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "build_leaderboards.py"

    subprocess.run(
        [
            sys.executable,
            str(script),
            "--games-root",
            str(games_root),
            "--readme",
            str(readme),
            "--json-out",
            str(json_out),
            "--cards-dir",
            str(tmp_path / "assets"),
        ],
        cwd=repo_root,
        check=True,
    )

    summary = json.loads(json_out.read_text(encoding="utf-8"))
    assert summary["source_games"] == 5
    assert summary["most_games_completed"][0] == {"player": "alice", "games": 3}
    assert summary["most_classic_wins"][0]["player"] == "alice"
    assert summary["most_classic_wins"][0]["wins"] == 1
    assert summary["most_classic_wins"][1]["player"] == "bob"
    assert summary["most_classic_wins"][2]["player"] == "zoe"

    moves = {row["attempts"]: row["winners"] for row in summary["classic_wins_by_moves"]}
    assert 3 in moves
    assert moves[3] == [{"player": "zoe", "wins": 1}]
    assert moves[4] == [{"player": "alice", "wins": 1}, {"player": "bob", "wins": 1}]

    assert summary["perfectionist_winners"] == [{"player": "alice", "wins": 1}]

    readme_text = readme.read_text(encoding="utf-8")
    assert "### Leaderboards" in readme_text
    assert "_As of (UTC): 2026-04-01T00:40:00+00:00 from 5 completed games_" in readme_text
    assert "assets/readme-leaderboard-card-champions.svg" in readme_text
    assert "assets/readme-leaderboard-card-commitment.svg" in readme_text
    assert "assets/readme-leaderboard-card-quick-draw.svg" in readme_text
    assert "assets/readme-leaderboard-card-perfectionists.svg" in readme_text
    assert "#### CHAMPIONS" not in readme_text


def test_build_leaderboards_script_empty_games_has_generated_timestamp(tmp_path: Path) -> None:
    games_root = tmp_path / "games"
    games_root.mkdir(parents=True, exist_ok=True)

    readme = tmp_path / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# test",
                "",
                "<!-- MM_LEADERBOARD_START -->",
                "- placeholder",
                "<!-- MM_LEADERBOARD_END -->",
                "",
            ]
        ),
        encoding="utf-8",
    )
    json_out = tmp_path / "data" / "leaderboards.json"
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "build_leaderboards.py"

    subprocess.run(
        [
            sys.executable,
            str(script),
            "--games-root",
            str(games_root),
            "--readme",
            str(readme),
            "--json-out",
            str(json_out),
            "--cards-dir",
            str(tmp_path / "assets"),
        ],
        cwd=repo_root,
        check=True,
    )

    summary = json.loads(json_out.read_text(encoding="utf-8"))
    assert summary["source_games"] == 0
    assert summary["generated_at"]
    dt.datetime.fromisoformat(summary["generated_at"])

    readme_text = readme.read_text(encoding="utf-8")
    assert "_As of (UTC): n/a" not in readme_text
    assert "from 0 completed games" in readme_text
    assert "<em>No completed games yet.</em>" in readme_text
