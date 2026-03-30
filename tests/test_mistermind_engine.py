import copy
import datetime as dt
import io
import unittest
import urllib.error
from unittest import mock

from mistermind import engine as mm


class ParseCommandTests(unittest.TestCase):
    # ── Prefix forms (still accepted) ────────────────────────────
    def test_parse_guess_full_names(self) -> None:
        parsed = mm.parse_command("/guess red blue green yellow")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])
        self.assertNotIn("error", parsed)

    def test_parse_guess_aliases(self) -> None:
        parsed = mm.parse_command("/guess r b g y")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])

    def test_parse_guess_without_slash(self) -> None:
        parsed = mm.parse_command("guess red blue green yellow")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])

    def test_parse_guess_with_tolerated_prefix_typo_gues(self) -> None:
        parsed = mm.parse_command("gues red blue green yellow")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])

    def test_parse_guess_with_tolerated_prefix_typo_guesss(self) -> None:
        parsed = mm.parse_command("/guesss red blue green yellow")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])

    # ── Bare forms (no prefix) ───────────────────────────────────
    def test_bare_full_names(self) -> None:
        parsed = mm.parse_command("red blue green yellow")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])

    def test_bare_single_letters(self) -> None:
        parsed = mm.parse_command("r b g y")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])

    def test_bare_mixed_words_and_letters(self) -> None:
        parsed = mm.parse_command("r orange g g")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "orange", "green", "green"])

    def test_bare_compact_adjacent_words(self) -> None:
        parsed = mm.parse_command("redredredblue")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "red", "red", "blue"])

    def test_bare_split_compact_letters(self) -> None:
        parsed = mm.parse_command("rg bp")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "green", "blue", "purple"])

    def test_bare_case_insensitive(self) -> None:
        parsed = mm.parse_command("B b g r")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["blue", "blue", "green", "red"])

    def test_bare_mixed_case_words(self) -> None:
        parsed = mm.parse_command("blue   yellow  purple OrAnge")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["blue", "yellow", "purple", "orange"])

    # ── Concatenated letters ─────────────────────────────────────
    def test_concatenated_letters(self) -> None:
        parsed = mm.parse_command("pppb")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["purple", "purple", "purple", "blue"])

    def test_concatenated_letters_mixed_case(self) -> None:
        parsed = mm.parse_command("RGBY")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "green", "blue", "yellow"])

    # ── Period tolerance (speech-to-text artifact) ───────────────
    def test_periods_as_spaces(self) -> None:
        parsed = mm.parse_command("red.blue.green.yellow")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "blue", "green", "yellow"])

    def test_mixed_periods_and_spaces(self) -> None:
        parsed = mm.parse_command("r orange.  g   g")
        self.assertEqual(parsed["kind"], "guess")
        self.assertEqual(parsed["guess"], ["red", "orange", "green", "green"])

    def test_too_many_periods_rejected(self) -> None:
        parsed = mm.parse_command("r.b.g.y.extra.period")
        # Extra non-color text survives normalization, so not a guess
        self.assertEqual(parsed["kind"], "ignore")

    # ── Error cases ──────────────────────────────────────────────
    def test_parse_guess_invalid_count_with_prefix(self) -> None:
        parsed = mm.parse_command("/guess red blue")
        self.assertEqual(parsed["kind"], "guess")
        self.assertIn("error", parsed)

    def test_bare_unknown_color_ignored(self) -> None:
        # "cyan" is not a valid color -- without /guess prefix, just ignored
        parsed = mm.parse_command("red blue cyan yellow")
        self.assertEqual(parsed["kind"], "ignore")

    def test_guess_prefix_unknown_color_errors(self) -> None:
        parsed = mm.parse_command("/guess red blue cyan yellow")
        self.assertEqual(parsed["kind"], "guess")
        self.assertIn("error", parsed)

    # ── Other commands ───────────────────────────────────────────
    def test_parse_other_commands(self) -> None:
        self.assertEqual(mm.parse_command("/status")["kind"], "status")
        self.assertEqual(mm.parse_command("status")["kind"], "status")
        self.assertEqual(mm.parse_command("/help")["kind"], "help")
        self.assertEqual(mm.parse_command("help")["kind"], "help")
        self.assertEqual(mm.parse_command("/giveup")["kind"], "giveup")
        self.assertEqual(mm.parse_command("giveup")["kind"], "giveup")

    def test_parse_other_commands_ignore_trailing_text(self) -> None:
        self.assertEqual(mm.parse_command("/help what do i do")["kind"], "help")
        self.assertEqual(mm.parse_command("status pls")["kind"], "status")
        self.assertEqual(mm.parse_command("/giveup now")["kind"], "giveup")

    def test_parse_ignores_non_command(self) -> None:
        self.assertEqual(mm.parse_command("hello world")["kind"], "ignore")
        self.assertEqual(mm.parse_command("how do i make a guess")["kind"], "ignore")


class TimeParsingTests(unittest.TestCase):
    def test_parse_iso_utc_treats_zero_as_unset(self) -> None:
        self.assertIsNone(mm.parse_iso_utc("0"))
        self.assertIsNone(mm.parse_iso_utc(" 0 "))


class AutomationLoginTests(unittest.TestCase):
    def test_find_latest_state_accepts_configured_automation_login(self) -> None:
        signing_secret = "signing-secret"
        state = mm.build_initial_state("owner/repo", 77, "owner")
        token = mm.encode_state_token(state, signing_secret)
        comments = [
            {
                "id": 101,
                "user": {"login": "mistermind-assistant[bot]"},
                "body": f"<!-- MM_STATE_V1 {token} -->",
            }
        ]

        with mock.patch.dict("os.environ", {"MM_AUTOMATION_LOGIN": "mistermind-assistant[bot]"}):
            recovered, comment_id = mm.find_latest_state_from_comments(
                comments,
                signing_secret=signing_secret,
                room_key="issue:owner/repo#77",
                owner="owner",
                issue_number=77,
            )

        self.assertEqual(comment_id, 101)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["issue_number"], 77)


class RemoteActionDispatchTests(unittest.TestCase):
    class _IssueAPI:
        def __init__(self, issue: dict[str, object]) -> None:
            self.issue = issue
            self.requested_issue_number: int | None = None

        def get_issue(self, issue_number: int) -> dict[str, object]:
            self.requested_issue_number = issue_number
            return self.issue

    @staticmethod
    def _room_issue() -> dict[str, object]:
        return {
            "number": 42,
            "labels": [{"name": "game:mistermind"}],
            "user": {"login": "owner"},
        }

    def test_remote_command_body_maps_supported_actions(self) -> None:
        self.assertEqual(mm._remote_command_body("status"), "/status")
        self.assertEqual(mm._remote_command_body("help"), "/help")
        self.assertEqual(mm._remote_command_body("giveup"), "/giveup")
        self.assertEqual(mm._remote_command_body("guess", "r b g y"), "/guess r b g y")

    def test_remote_command_body_requires_guess_payload(self) -> None:
        with self.assertRaises(ValueError):
            mm._remote_command_body("guess", "")

    def test_remote_command_body_rejects_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            mm._remote_command_body("dance")

    def test_handle_remote_action_dispatches_conduct_and_game(self) -> None:
        api = self._IssueAPI(self._room_issue())
        with (
            mock.patch("mistermind.handlers.handle_issue_comment_conduct") as conduct,
            mock.patch("mistermind.handlers.handle_issue_comment") as game,
        ):
            mm.handle_remote_action(
                api=api,  # type: ignore[arg-type]
                repo="owner/repo",
                issue_number=42,
                action="status",
                guess_text="",
                signing_secret="signing-secret",
                solution_salt="salt",
                moderation_policy={},
                apply_moderation=True,
            )

        self.assertEqual(api.requested_issue_number, 42)
        conduct.assert_called_once()
        game.assert_called_once()
        payload = game.call_args.kwargs["payload"]
        self.assertEqual(payload["comment"]["body"], "/status")
        self.assertIsInstance(payload["comment"]["id"], int)
        self.assertLess(payload["comment"]["id"], 0)

    def test_handle_remote_action_can_skip_conduct_lane(self) -> None:
        api = self._IssueAPI(self._room_issue())
        with (
            mock.patch("mistermind.handlers.handle_issue_comment_conduct") as conduct,
            mock.patch("mistermind.handlers.handle_issue_comment") as game,
        ):
            mm.handle_remote_action(
                api=api,  # type: ignore[arg-type]
                repo="owner/repo",
                issue_number=42,
                action="help",
                guess_text="",
                signing_secret="signing-secret",
                solution_salt="salt",
                moderation_policy={},
                apply_moderation=False,
            )

        conduct.assert_not_called()
        game.assert_called_once()

    def test_handle_remote_action_rejects_non_room_issue(self) -> None:
        api = self._IssueAPI({"number": 42, "labels": [], "user": {"login": "owner"}})
        with self.assertRaises(RuntimeError):
            mm.handle_remote_action(
                api=api,  # type: ignore[arg-type]
                repo="owner/repo",
                issue_number=42,
                action="status",
                guess_text="",
                signing_secret="signing-secret",
                solution_salt="salt",
                moderation_policy={},
            )


class RoomVariantTests(unittest.TestCase):
    def test_parse_room_variant_from_issue_form_hint(self) -> None:
        body = "### Gameplay Mode\nHint Mode\n\n### Board Theme\nClassic Wood\n"
        self.assertEqual(mm.parse_room_variant(body), "hint")

    def test_parse_room_variant_from_issue_form_perfectionist(self) -> None:
        body = "### Gameplay Mode\nPerfectionist\n\n### Board Theme\nOcean\n"
        self.assertEqual(mm.parse_room_variant(body), "perfectionist")

    def test_parse_room_variant_defaults_to_classic(self) -> None:
        body = "### Gameplay Mode\nClassic\n"
        self.assertEqual(mm.parse_room_variant(body), "classic")
        self.assertEqual(mm.parse_room_variant(""), "classic")

    def test_parse_board_theme_from_issue_form(self) -> None:
        self.assertEqual(mm.parse_board_theme("### Board Theme\nTerminal\n"), "terminal")
        self.assertEqual(mm.parse_board_theme("### Board Theme\nOcean\n"), "ocean-v5")
        self.assertEqual(mm.parse_board_theme("### Board Theme\nClassic Wood\n"), "classic")

    def test_state_validation_rejects_unknown_variant(self) -> None:
        state = mm.build_initial_state("owner/repo", 99, "owner")
        state["variant"] = "mystery"
        self.assertFalse(
            mm.state_is_valid(
                state,
                room_key="issue:owner/repo#99",
                owner="owner",
                issue_number=99,
            )
        )

    def test_state_validation_rejects_unknown_board_theme(self) -> None:
        state = mm.build_initial_state("owner/repo", 99, "owner")
        state["board_theme"] = "ultraviolet"
        self.assertFalse(
            mm.state_is_valid(
                state,
                room_key="issue:owner/repo#99",
                owner="owner",
                issue_number=99,
            )
        )

    def test_room_perfectionist_enabled(self) -> None:
        state = mm.build_initial_state("owner/repo", 77, "owner", variant="perfectionist")
        self.assertTrue(mm.room_perfectionist_enabled(state))

    def test_game_rules_text_mentions_perfectionist(self) -> None:
        rules = mm.game_rules_text(variant="perfectionist")
        self.assertIn("Perfectionist mode enabled", rules)
        self.assertIn("unless that guess solves the full code", rules)

    def test_game_rules_text_classic_uses_verbose_result_key(self) -> None:
        rules = mm.game_rules_text(variant="classic")
        self.assertIn("Result key", rules)
        self.assertIn("Color and position", rules)
        self.assertIn("Color only", rules)
        self.assertIn("Neither color nor position", rules)
        self.assertNotIn("| ✓ | ✓ |", rules)


class ScoringTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        secret = ["red", "blue", "green", "yellow"]
        guess = ["red", "blue", "green", "yellow"]
        self.assertEqual(mm.score_guess(secret, guess), (4, 0))

    def test_no_match(self) -> None:
        secret = ["red", "red", "red", "red"]
        guess = ["blue", "blue", "blue", "blue"]
        self.assertEqual(mm.score_guess(secret, guess), (0, 0))

    def test_duplicate_edge_case(self) -> None:
        secret = ["red", "red", "blue", "green"]
        guess = ["red", "blue", "red", "yellow"]
        self.assertEqual(mm.score_guess(secret, guess), (1, 2))


class EnvelopeIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signing_secret = "signing-secret"
        self.state = mm.build_initial_state("owner/repo", 7, "octocat")

    def test_encode_decode_round_trip(self) -> None:
        token = mm.encode_state_token(self.state, self.signing_secret)
        decoded = mm.decode_state_token(token, self.signing_secret)
        self.assertEqual(decoded, self.state)

    def test_tamper_payload_rejected(self) -> None:
        token = mm.encode_state_token(self.state, self.signing_secret)
        payload, sig = token.split(".", 1)
        tampered_payload = payload[:-1] + ("A" if payload[-1] != "A" else "B")
        decoded = mm.decode_state_token(f"{tampered_payload}.{sig}", self.signing_secret)
        self.assertIsNone(decoded)

    def test_tamper_signature_rejected(self) -> None:
        token = mm.encode_state_token(self.state, self.signing_secret)
        payload, sig = token.split(".", 1)
        tampered_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
        decoded = mm.decode_state_token(f"{payload}.{tampered_sig}", self.signing_secret)
        self.assertIsNone(decoded)

    def test_conduct_token_round_trip(self) -> None:
        conduct_state = mm.build_initial_conduct_state("owner/repo", 9, "owner")
        token = mm.encode_conduct_token(conduct_state, self.signing_secret)
        decoded = mm.decode_conduct_token(token, self.signing_secret)
        self.assertEqual(decoded, conduct_state)


class TransitionValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = mm.build_initial_state("owner/repo", 12, "player")
        self.next_state = copy.deepcopy(self.base)
        self.next_state["seq"] = 1
        self.next_state["updated_at"] = mm.now_iso()

    def test_accepts_forward_seq_same_attempt(self) -> None:
        self.assertTrue(mm.is_valid_state_transition(self.base, self.next_state))

    def test_rejects_seq_rollback(self) -> None:
        candidate = copy.deepcopy(self.base)
        candidate["seq"] = self.base["seq"]
        self.assertFalse(mm.is_valid_state_transition(self.base, candidate))

    def test_rejects_attempt_rollback(self) -> None:
        candidate = copy.deepcopy(self.next_state)
        candidate["attempt"] = -1
        self.assertFalse(mm.is_valid_state_transition(self.base, candidate))


class RoomTimeoutTests(unittest.TestCase):
    def test_room_times_out_after_configured_lifetime(self) -> None:
        state = mm.build_initial_state("owner/repo", 13, "owner")
        now = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
        started = now - dt.timedelta(minutes=mm.GAME_TIMEOUT_MINUTES + 1)
        state["created_at"] = started.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.assertTrue(mm._is_room_timed_out(state, now=now))

    def test_room_not_timed_out_before_deadline(self) -> None:
        state = mm.build_initial_state("owner/repo", 14, "owner")
        now = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
        started = now - dt.timedelta(minutes=mm.GAME_TIMEOUT_MINUTES - 1)
        state["created_at"] = started.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.assertFalse(mm._is_room_timed_out(state, now=now))

    def test_terminal_room_never_counts_as_timed_out(self) -> None:
        state = mm.build_initial_state("owner/repo", 15, "owner")
        state["phase"] = "won"
        now = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
        started = now - dt.timedelta(minutes=mm.GAME_TIMEOUT_MINUTES + 60)
        state["created_at"] = started.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.assertFalse(mm._is_room_timed_out(state, now=now))


class OpeningMessageDeadlineTests(unittest.TestCase):
    class _DummyAPI:
        def __init__(self) -> None:
            self.comments: list[tuple[int, str]] = []
            self.labels_added: list[tuple[int, list[str]]] = []
            self.labels_removed: list[tuple[int, str]] = []

        def create_issue_comment(self, issue_number: int, body: str) -> dict[str, object]:
            self.comments.append((issue_number, body))
            return {"id": 1}

        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            self.labels_added.append((issue_number, labels))

        def remove_label(self, issue_number: int, label: str) -> None:
            self.labels_removed.append((issue_number, label))

    def test_opening_comment_announces_exact_timeout_deadline(self) -> None:
        api = self._DummyAPI()
        state = mm.build_initial_state("owner/repo", 200, "owner")
        state["created_at"] = "2026-01-01T12:00:00Z"

        payload = {
            "issue": {
                "number": 200,
                "user": {"login": "owner"},
                "body": "",
            }
        }

        with (
            mock.patch("mistermind.handlers.build_initial_state", return_value=state),
            mock.patch(
                "mistermind.handlers._upload_board_and_render",
                side_effect=lambda **kwargs: kwargs["headline"],
            ),
        ):
            mm.handle_issue_opened(
                api=api,  # type: ignore[arg-type]
                repo="owner/repo",
                payload=payload,
                signing_secret="signing-secret",
                solution_salt="salt",
            )

        self.assertEqual(len(api.comments), 1)
        _, comment_body = api.comments[0]
        self.assertIn("Room timeout at `2026-01-01 12:30 UTC` if unfinished.", comment_body)

    def test_opening_comment_uses_fallback_when_created_at_missing(self) -> None:
        api = self._DummyAPI()
        state = mm.build_initial_state("owner/repo", 201, "owner")
        state["created_at"] = "not-a-date"

        payload = {
            "issue": {
                "number": 201,
                "user": {"login": "owner"},
                "body": "",
            }
        }

        with (
            mock.patch("mistermind.handlers.build_initial_state", return_value=state),
            mock.patch(
                "mistermind.handlers._upload_board_and_render",
                side_effect=lambda **kwargs: kwargs["headline"],
            ),
        ):
            mm.handle_issue_opened(
                api=api,  # type: ignore[arg-type]
                repo="owner/repo",
                payload=payload,
                signing_secret="signing-secret",
                solution_salt="salt",
            )

        self.assertEqual(len(api.comments), 1)
        _, comment_body = api.comments[0]
        self.assertIn("Room timeout: 30 minutes from room creation.", comment_body)


class TerminalLifecycleTests(unittest.TestCase):
    class _DummyAPI:
        def __init__(self) -> None:
            self.closed: list[int] = []
            self.locked: list[tuple[int, str]] = []

        def close_issue(self, issue_number: int) -> None:
            self.closed.append(issue_number)

        def lock_issue(self, issue_number: int, *, reason: str = "resolved") -> None:
            self.locked.append((issue_number, reason))

    def test_locks_on_first_terminal_transition(self) -> None:
        api = self._DummyAPI()
        mm._lock_on_terminal_transition(
            api=api,  # type: ignore[arg-type]
            issue_number=42,
            previous_phase="active",
            phase="lost",
        )
        self.assertEqual(api.closed, [])
        self.assertEqual(api.locked, [(42, "resolved")])

    def test_skips_when_already_terminal(self) -> None:
        api = self._DummyAPI()
        mm._lock_on_terminal_transition(
            api=api,  # type: ignore[arg-type]
            issue_number=42,
            previous_phase="lost",
            phase="lost",
        )
        self.assertEqual(api.closed, [])
        self.assertEqual(api.locked, [])


class SweepLifecycleTests(unittest.TestCase):
    class _SweepAPI:
        def __init__(self, issues: list[dict[str, object]]) -> None:
            self._issues = issues
            self.closed: list[int] = []
            self.locked: list[tuple[int, str]] = []
            self.labels_added: list[tuple[int, list[str]]] = []
            self.labels_removed: list[tuple[int, str]] = []

        def list_open_issues_with_label(self, label: str) -> list[dict[str, object]]:
            if label != "game:mistermind":
                return []
            return self._issues

        def close_issue(self, issue_number: int) -> None:
            self.closed.append(issue_number)

        def lock_issue(self, issue_number: int, *, reason: str = "resolved") -> None:
            self.locked.append((issue_number, reason))

        def add_labels(self, issue_number: int, labels: list[str]) -> None:
            self.labels_added.append((issue_number, labels))

        def remove_label(self, issue_number: int, label: str) -> None:
            self.labels_removed.append((issue_number, label))

    def test_sweep_closes_only_locked_terminal_rooms_after_grace(self) -> None:
        now = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
        issues: list[dict[str, object]] = [
            {
                "number": 1,
                "locked": True,
                "labels": [{"name": "game:mistermind"}, {"name": "mm:won"}],
                "updated_at": "2026-01-01T11:30:00Z",
            },
            {
                "number": 2,
                "locked": True,
                "labels": [{"name": "game:mistermind"}, {"name": "mm:lost"}],
                "updated_at": "2026-01-01T11:50:00Z",
            },
            {
                "number": 3,
                "locked": False,
                "labels": [{"name": "game:mistermind"}, {"name": "mm:won"}],
                "updated_at": "2026-01-01T11:20:00Z",
            },
            {
                "number": 4,
                "locked": True,
                "labels": [{"name": "game:mistermind"}, {"name": "mm:active"}],
                "updated_at": "2026-01-01T11:20:00Z",
            },
        ]
        api = self._SweepAPI(issues)

        closed = mm.handle_terminal_room_sweep(
            api=api,  # type: ignore[arg-type]
            now=now,
            close_after_minutes=20,
        )

        self.assertEqual(closed, [1])
        self.assertEqual(api.closed, [1])

    def test_sweep_times_out_active_room_and_locks_it(self) -> None:
        now = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
        issues: list[dict[str, object]] = [
            {
                "number": 9,
                "locked": False,
                "labels": [{"name": "game:mistermind"}, {"name": "mm:active"}],
                "created_at": "2026-01-01T11:20:00Z",
                "updated_at": "2026-01-01T11:58:00Z",
            }
        ]
        api = self._SweepAPI(issues)

        closed = mm.handle_terminal_room_sweep(
            api=api,  # type: ignore[arg-type]
            now=now,
            close_after_minutes=20,
            active_timeout_minutes=30,
        )

        self.assertEqual(closed, [])
        self.assertEqual(api.closed, [])
        self.assertEqual(api.locked, [(9, "resolved")])
        self.assertIn((9, ["game:mistermind", "mm:lost"]), api.labels_added)
        self.assertIn((9, "mm:active"), api.labels_removed)


class RoutingFixtureTests(unittest.TestCase):
    def test_room_issue_routes(self) -> None:
        payload = {
            "action": "opened",
            "issue": {
                "title": "[MM ROOM] test",
                "user": {"login": "owner"},
                "labels": [{"name": "game:mistermind"}],
            },
        }
        self.assertTrue(mm.should_process_issue_open(payload))

    def test_issue_without_label_rejected(self) -> None:
        payload = {
            "action": "opened",
            "issue": {"title": "[MM ROOM] test", "user": {"login": "owner"}, "labels": []},
        }
        self.assertFalse(mm.should_process_issue_open(payload))

    def test_owner_comment_guess_routes(self) -> None:
        payload = {
            "action": "created",
            "issue": {
                "user": {"login": "owner"},
                "pull_request": None,
                "labels": [{"name": "game:mistermind"}],
            },
            "comment": {"user": {"login": "owner"}, "body": "/guess red blue green yellow"},
        }
        self.assertTrue(mm.should_process_issue_comment(payload))

    def test_owner_comment_guess_without_slash_routes(self) -> None:
        payload = {
            "action": "created",
            "issue": {
                "user": {"login": "owner"},
                "pull_request": None,
                "labels": [{"name": "game:mistermind"}],
            },
            "comment": {"user": {"login": "owner"}, "body": "guess red, blue.green-yellow"},
        }
        self.assertTrue(mm.should_process_issue_comment(payload))

    def test_non_owner_comment_rejected(self) -> None:
        payload = {
            "action": "created",
            "issue": {
                "user": {"login": "owner"},
                "pull_request": None,
                "labels": [{"name": "game:mistermind"}],
            },
            "comment": {"user": {"login": "other"}, "body": "/guess red blue green yellow"},
        }
        self.assertFalse(mm.should_process_issue_comment(payload))

    def test_pr_comment_rejected(self) -> None:
        payload = {
            "action": "created",
            "issue": {
                "user": {"login": "owner"},
                "pull_request": {"url": "x"},
                "labels": [{"name": "game:mistermind"}],
            },
            "comment": {"user": {"login": "owner"}, "body": "/guess red blue green yellow"},
        }
        self.assertFalse(mm.should_process_issue_comment(payload))

    def test_comment_without_label_rejected(self) -> None:
        payload = {
            "action": "created",
            "issue": {"user": {"login": "owner"}, "pull_request": None, "labels": []},
            "comment": {"user": {"login": "owner"}, "body": "/guess red blue green yellow"},
        }
        self.assertFalse(mm.should_process_issue_comment(payload))


class CommandApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = mm.build_initial_state("owner/repo", 21, "owner")
        self.solution = ["red", "blue", "green", "yellow"]

    def test_duplicate_comment_id_is_idempotent(self) -> None:
        self.state["processed_comment_ids"] = [100]
        next_state, _, _, should_emit = mm.apply_command_to_state(
            previous=self.state,
            parsed_command={"kind": "status"},
            comment_id=100,
            solution=self.solution,
        )
        self.assertFalse(should_emit)
        self.assertEqual(next_state, self.state)

    def test_win_transition(self) -> None:
        next_state, headline, reveal, should_emit = mm.apply_command_to_state(
            previous=self.state,
            parsed_command={"kind": "guess", "guess": self.solution},
            comment_id=101,
            solution=self.solution,
        )
        self.assertTrue(should_emit)
        self.assertEqual(next_state["phase"], "won")
        self.assertEqual(next_state["attempt"], 1)
        self.assertTrue(reveal)
        self.assertIn("solved", headline)
        self.assertIn("4 exact, 0 color-only, 0 absent", headline)

    def test_loss_transition_on_max_attempt(self) -> None:
        state = copy.deepcopy(self.state)
        state["attempt"] = mm.MAX_ATTEMPTS - 1
        state["history"] = [
            {"attempt": i, "guess": ["red"] * 4, "black": 0, "white": 0}
            for i in range(1, mm.MAX_ATTEMPTS)
        ]
        state["seq"] = mm.MAX_ATTEMPTS - 1
        next_state, _, reveal, should_emit = mm.apply_command_to_state(
            previous=state,
            parsed_command={"kind": "guess", "guess": ["blue", "blue", "blue", "blue"]},
            comment_id=102,
            solution=self.solution,
        )
        self.assertTrue(should_emit)
        self.assertEqual(next_state["phase"], "lost")
        self.assertEqual(next_state["attempt"], mm.MAX_ATTEMPTS)
        self.assertTrue(reveal)

    def test_invalid_guess_does_not_increment_attempt(self) -> None:
        next_state, _, _, should_emit = mm.apply_command_to_state(
            previous=self.state,
            parsed_command={"kind": "guess", "error": "bad guess"},
            comment_id=103,
            solution=self.solution,
        )
        self.assertTrue(should_emit)
        self.assertEqual(next_state["attempt"], 0)
        self.assertEqual(len(next_state["history"]), 0)


class DeductiveHintTests(unittest.TestCase):
    def test_marks_impossible_peg_from_prior_history(self) -> None:
        state = mm.build_initial_state("owner/repo", 61, "owner", variant="hint")
        state["history"] = [
            {
                "attempt": 1,
                "guess": ["red", "red", "red", "red"],
                "black": 0,
                "white": 0,
            }
        ]
        state["attempt"] = 1

        summary = mm.compute_deductive_hint_summary(
            previous_state=state,
            guess=["red", "blue", "green", "yellow"],
        )
        self.assertIsNotNone(summary)
        impossible = summary["impossible"]  # type: ignore[index]
        self.assertTrue(
            any(item["position"] == 1 and item["color"] == "red" for item in impossible)
        )

    def test_marks_certain_pegs_when_fully_determined(self) -> None:
        state = mm.build_initial_state("owner/repo", 62, "owner", variant="hint")
        solved_guess = ["blue", "green", "yellow", "orange"]
        state["history"] = [
            {
                "attempt": 1,
                "guess": solved_guess,
                "black": 4,
                "white": 0,
            }
        ]
        state["attempt"] = 1

        summary = mm.compute_deductive_hint_summary(
            previous_state=state,
            guess=solved_guess,
        )
        self.assertIsNotNone(summary)
        certain = summary["certain"]  # type: ignore[index]
        self.assertEqual(len(certain), 4)
        self.assertEqual({item["position"] for item in certain}, {1, 2, 3, 4})


class PerfectionistModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = mm.build_initial_state("owner/repo", 91, "owner", variant="perfectionist")
        self.solution = ["purple", "orange", "red", "blue"]

    def _find_non_optimal_guess(self) -> list[str]:
        candidates = [
            ["red", "red", "red", "red"],
            ["red", "blue", "green", "yellow"],
            ["orange", "orange", "orange", "orange"],
            ["purple", "yellow", "blue", "green"],
        ]
        for guess in candidates:
            summary = mm.compute_perfectionist_optimality_summary(
                previous_state=self.state,
                guess=guess,
            )
            if summary and summary.get("available") and not summary.get("is_optimal"):
                return guess
        self.fail("Could not identify a non-optimal opening guess for test fixture.")

    def test_perfectionist_summary_reports_optimality(self) -> None:
        summary = mm.compute_perfectionist_optimality_summary(
            previous_state=self.state,
            guess=["red", "red", "blue", "blue"],
        )
        self.assertIsNotNone(summary)
        self.assertTrue(summary["available"])  # type: ignore[index]
        self.assertIn("is_optimal", summary)
        self.assertIn("optimal_worst_case", summary)

    def test_perfectionist_gate_rejects_non_optimal_guess(self) -> None:
        bad_guess = self._find_non_optimal_guess()
        gated = mm.apply_perfectionist_gate(
            previous=self.state,
            parsed_command={"kind": "guess", "guess": bad_guess},
            comment_id=5001,
            solution=self.solution,
        )
        self.assertIsNotNone(gated)
        (next_state, _headline, reveal_solution, should_emit), hint_block = gated  # type: ignore[misc]
        self.assertTrue(should_emit)
        self.assertTrue(reveal_solution)
        self.assertEqual(next_state["phase"], "lost")
        self.assertEqual(next_state["last_action"], "guess_perfectionist_fail")
        self.assertIn("Perfectionist Verdict", hint_block or "")

    def test_perfectionist_gate_allows_solving_guess(self) -> None:
        gated = mm.apply_perfectionist_gate(
            previous=self.state,
            parsed_command={"kind": "guess", "guess": self.solution},
            comment_id=5002,
            solution=self.solution,
        )
        self.assertIsNone(gated)

    def test_perfectionist_gate_unavailable_sets_retry_message(self) -> None:
        original_loader = mm._load_optimizer_solver_class  # type: ignore[attr-defined]
        original_class = mm._OPTIMIZER_SOLVER_CLASS  # type: ignore[attr-defined]
        original_error = mm._OPTIMIZER_SOLVER_LOAD_ERROR  # type: ignore[attr-defined]
        try:
            mm._OPTIMIZER_SOLVER_CLASS = None  # type: ignore[attr-defined]
            mm._OPTIMIZER_SOLVER_LOAD_ERROR = "test-unavailable"  # type: ignore[attr-defined]
            mm._load_optimizer_solver_class = lambda: None  # type: ignore[assignment]
            gated = mm.apply_perfectionist_gate(
                previous=self.state,
                parsed_command={"kind": "guess", "guess": ["red", "red", "red", "red"]},
                comment_id=5003,
                solution=self.solution,
            )
        finally:
            mm._load_optimizer_solver_class = original_loader  # type: ignore[assignment]
            mm._OPTIMIZER_SOLVER_CLASS = original_class  # type: ignore[attr-defined]
            mm._OPTIMIZER_SOLVER_LOAD_ERROR = original_error  # type: ignore[attr-defined]

        self.assertIsNotNone(gated)
        (next_state, _headline, reveal_solution, should_emit), hint_block = gated  # type: ignore[misc]
        self.assertTrue(should_emit)
        self.assertFalse(reveal_solution)
        self.assertEqual(next_state["phase"], "active")
        self.assertEqual(next_state["attempt"], 0)
        self.assertEqual(next_state["last_action"], "perfectionist_eval_retry")
        self.assertIn("temporarily unavailable", (hint_block or "").lower())


class VisualBoardTests(unittest.TestCase):
    def test_board_renders_all_attempt_rows(self) -> None:
        state = mm.build_initial_state("owner/repo", 30, "owner")
        board = mm.format_visual_board(state)
        self.assertIn("01", board)
        self.assertIn(f"{mm.MAX_ATTEMPTS:02d}", board)
        self.assertEqual(
            sum(1 for line in board.splitlines() if line[:2].isdigit()),
            mm.MAX_ATTEMPTS,
        )

    def test_board_renders_guess_and_feedback_icons(self) -> None:
        state = mm.build_initial_state("owner/repo", 31, "owner")
        state["history"] = [
            {
                "attempt": 1,
                "guess": ["red", "blue", "green", "yellow"],
                "black": 2,
                "white": 1,
            }
        ]
        state["attempt"] = 1
        board = mm.format_visual_board(state)
        self.assertIn("🔴 🔵 🟢 🟡", board)
        self.assertIn("✓/✓ 2", board)
        self.assertIn("✓/✗ 1", board)
        self.assertIn("✗/✗ 1", board)

    def test_render_comment_has_hidden_state_token(self) -> None:
        state = mm.build_initial_state("owner/repo", 32, "owner")
        body = mm.render_comment(
            headline="status",
            state=state,
            token="payload.sig",
            reveal_solution=False,
            solution=["red", "blue", "green", "yellow"],
        )
        # State token is in an HTML comment, not a visible <details> block
        self.assertIn("<!-- MM_STATE_V1 payload.sig -->", body)
        self.assertNotIn("Engine Envelope", body)
        self.assertNotIn("Commands</summary>", body)
        self.assertNotIn("Quick Copy</summary>", body)

    def test_render_rich_board_shows_active_marker(self) -> None:
        state = mm.build_initial_state("owner/repo", 33, "owner")
        body = mm.render_comment(
            headline="Room initialized.",
            state=state,
            token="tok",
            reveal_solution=False,
            solution=["red", "blue", "green", "yellow"],
        )
        # Active row should show the pointer marker
        self.assertIn("▶01", body)
        # Title should include game number
        self.assertIn("GAME #33", body)
        # Progress bar should show turn 1
        self.assertIn("Turn 1", body)

    def test_render_rich_board_shows_guess_emoji(self) -> None:
        state = mm.build_initial_state("owner/repo", 34, "owner")
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 2, "white": 1},
        ]
        state["attempt"] = 1
        body = mm.render_comment(
            headline="Attempt 1: 2 exact, 1 color-only, 1 absent.",
            state=state,
            token="tok",
            reveal_solution=False,
            solution=["red", "blue", "green", "yellow"],
        )
        # All four guess colors should be present as emoji
        self.assertIn("🔴", body)
        self.assertIn("🔵", body)
        self.assertIn("🟢", body)
        self.assertIn("🟡", body)
        # Classic mode uses verbose feedback labels (warmup style).
        self.assertIn("Color and position", body)
        self.assertIn("Color only", body)
        self.assertIn("Neither color nor position", body)
        self.assertIn("<code>2</code>", body)
        self.assertIn("<code>1</code>", body)
        # Active row for next guess
        self.assertIn("▶02", body)

    def test_render_rich_board_hint_mode_uses_verbose_feedback_labels(self) -> None:
        state = mm.build_initial_state("owner/repo", 34, "owner", variant="hint")
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 2, "white": 1},
        ]
        state["attempt"] = 1
        body = mm.render_comment(
            headline="Attempt 1: 2 exact, 1 color-only, 1 absent.",
            state=state,
            token="tok",
            reveal_solution=False,
            solution=["red", "blue", "green", "yellow"],
        )
        self.assertIn("Color and position", body)
        self.assertIn("Color only", body)
        self.assertIn("Neither color nor position", body)
        self.assertNotIn("✓/✓", body)

    def test_render_rich_board_win_state(self) -> None:
        state = mm.build_initial_state("owner/repo", 35, "owner")
        state["phase"] = "won"
        state["attempt"] = 3
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 1, "white": 2},
            {"attempt": 2, "guess": ["blue", "red", "yellow", "green"], "black": 0, "white": 4},
            {"attempt": 3, "guess": ["purple", "orange", "red", "blue"], "black": 4, "white": 0},
        ]
        body = mm.render_comment(
            headline="Attempt 3: solved (4 exact, 0 color-only, 0 absent).",
            state=state,
            token="tok",
            reveal_solution=True,
            solution=["purple", "orange", "red", "blue"],
        )
        # Win celebration
        self.assertIn("CODE CRACKED", body)
        self.assertIn("CRACKED", body)
        # Title change
        self.assertIn("MISTERMIND", body)
        # Solution reveal
        self.assertIn("🟣", body)
        self.assertIn("🟠", body)

    def test_render_rich_board_loss_state(self) -> None:
        state = mm.build_initial_state("owner/repo", 36, "owner")
        state["phase"] = "lost"
        state["attempt"] = 1
        state["history"] = [
            {"attempt": 1, "guess": ["red", "red", "red", "red"], "black": 0, "white": 0},
        ]
        body = mm.render_comment(
            headline="You gave up.",
            state=state,
            token="tok",
            reveal_solution=True,
            solution=["blue", "green", "yellow", "orange"],
        )
        # Loss banner
        self.assertIn("UNBROKEN", body)
        # Solution reveal
        self.assertIn("🔵", body)
        self.assertIn("🟢", body)
        # Commands should NOT appear for terminal games
        self.assertNotIn("Commands</summary>", body)
        self.assertNotIn("Quick Copy</summary>", body)

    def test_render_svg_board_produces_valid_svg(self) -> None:
        state = mm.build_initial_state("owner/repo", 37, "owner")
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 2, "white": 1},
        ]
        state["attempt"] = 1
        svg = mm.render_svg_board(state)
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.strip().endswith("</svg>"))
        # Should contain peg colors
        self.assertIn(mm.COLOR_HEX["red"], svg)
        self.assertIn(mm.COLOR_HEX["blue"], svg)
        # Should contain active row marker
        self.assertIn("▶ 02", svg)

    def test_render_svg_board_terminal_theme_uses_terminal_feedback_and_valid_colors(self) -> None:
        state = mm.build_initial_state("owner/repo", 37, "owner", board_theme="terminal")
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 2, "white": 1},
        ]
        state["attempt"] = 1
        svg = mm.render_svg_board(state)
        self.assertIn(f'fill="#{mm.COLOR_HEX["red"]}"', svg)
        self.assertIn(f'fill="#{mm.COLOR_HEX["blue"]}"', svg)
        self.assertNotIn('fill="##', svg)
        self.assertIn('width="10" height="10" fill="#00ff44"', svg)
        self.assertNotIn("✓/✓", svg)

    def test_render_svg_board_terminal_pegs_use_color_initial_letters(self) -> None:
        state = mm.build_initial_state("owner/repo", 37, "owner", board_theme="terminal")
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 0, "white": 0},
            {"attempt": 2, "guess": ["orange", "purple", "red", "blue"], "black": 0, "white": 0},
        ]
        state["attempt"] = 2
        svg = mm.render_svg_board(state)
        for initial in ("R", "B", "G", "Y", "O", "P"):
            self.assertIn(f">{initial}</text>", svg)
        self.assertNotIn("◆", svg)
        self.assertNotIn("▲", svg)
        self.assertNotIn("★", svg)
        self.assertNotIn("●", svg)
        self.assertNotIn("■", svg)
        self.assertNotIn("◇", svg)

    def test_render_svg_board_future_rows_use_higher_contrast_sockets(self) -> None:
        state = mm.build_initial_state("owner/repo", 37, "owner")
        svg = mm.render_svg_board(state)
        self.assertIn('stroke="#bc9060" stroke-width="2.4"', svg)
        self.assertIn('stroke="#c69a67" stroke-width="2.3"', svg)
        self.assertNotIn('stroke="#5a4020"', svg)
        self.assertNotIn('stroke="#3a2818"', svg)

    def test_render_svg_board_terminal_title_has_no_text_filter(self) -> None:
        state = mm.build_initial_state("owner/repo", 37, "owner", board_theme="terminal")
        svg = mm.render_svg_board(state)
        self.assertNotIn('filter="url(#phosphor)"', svg)

    def test_render_svg_board_win_overlay(self) -> None:
        state = mm.build_initial_state("owner/repo", 38, "owner")
        state["phase"] = "won"
        state["attempt"] = 5
        state["history"] = [
            {"attempt": i, "guess": ["red", "blue", "green", "yellow"], "black": i, "white": 0}
            for i in range(1, 6)
        ]
        svg = mm.render_svg_board(
            state, reveal_solution=True, solution=["red", "blue", "green", "yellow"]
        )
        self.assertIn("CODE CRACKED", svg)

    def test_render_svg_board_with_deduction_overlay_classes(self) -> None:
        state = mm.build_initial_state("owner/repo", 63, "owner", variant="hint")
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 1, "white": 1},
        ]
        state["attempt"] = 1
        svg = mm.render_svg_board(
            state,
            hint_overlay={"attempt": 1, "certain_positions": [2], "impossible_positions": [4]},
        )
        self.assertIn('data-deduction="certain"', svg)
        self.assertIn('data-deduction="impossible"', svg)
        self.assertIn('class="peg-certain"', svg)
        self.assertIn('class="peg-impossible"', svg)

    def test_svg_board_as_img_tag(self) -> None:
        state = mm.build_initial_state("owner/repo", 39, "owner")
        tag = mm.svg_board_as_img_tag(state)
        self.assertIn("<img", tag)
        self.assertIn("data:image/svg+xml;base64,", tag)
        self.assertIn('width="460"', tag)

    def test_hydrate_board_template(self) -> None:
        state = mm.build_initial_state("owner/repo", 42, "owner")
        state["history"] = [
            {"attempt": 1, "guess": ["red", "blue", "green", "yellow"], "black": 2, "white": 1},
        ]
        state["attempt"] = 1
        svg = mm.hydrate_board_template(state)
        # Template placeholders should be fully replaced
        self.assertNotIn("{{", svg)
        self.assertNotIn("}}", svg)
        # Should be valid SVG
        self.assertTrue(svg.strip().startswith("<svg"))
        self.assertTrue(svg.strip().endswith("</svg>"))
        # Should contain guess peg colors
        self.assertIn(mm.COLOR_HEX["red"], svg)
        self.assertIn(mm.COLOR_HEX["blue"], svg)
        # Active row marker
        self.assertIn("02", svg)

    def test_hydrate_board_template_with_explicit_template(self) -> None:
        # Minimal template with just one placeholder
        template = "<svg>{{TITLE}}{{ROW_01}}{{ROW_02}}{{ROW_03}}{{ROW_04}}{{ROW_05}}{{ROW_06}}{{ROW_07}}{{ROW_08}}{{ROW_09}}{{ROW_10}}{{PROGRESS}}{{OVERLAY}}</svg>"
        state = mm.build_initial_state("owner/repo", 99, "owner")
        result = mm.hydrate_board_template(state, template=template)
        self.assertNotIn("{{", result)
        self.assertIn("GAME #99", result)

    def test_hydrate_terminal_template_has_no_text_filter(self) -> None:
        state = mm.build_initial_state("owner/repo", 67, "owner", board_theme="terminal")
        svg = mm.hydrate_board_template(
            state, template_path="assets/mistermind-board-template-terminal.svg"
        )
        self.assertNotIn('filter="url(#phosphor)"', svg)

    def test_board_template_path_for_theme_selection(self) -> None:
        state = mm.build_initial_state("owner/repo", 65, "owner", board_theme="terminal")
        path = mm.board_template_path_for_state(state)
        self.assertTrue(path.endswith("assets/mistermind-board-template-terminal.svg"))

        state = mm.build_initial_state("owner/repo", 66, "owner", board_theme="ocean-v5")
        path = mm.board_template_path_for_state(state)
        self.assertTrue(path.endswith("assets/mistermind-board-template-ocean-v5.svg"))

    def test_board_asset_path(self) -> None:
        self.assertEqual(mm.board_asset_path(42, seq=7), "boards/42-007.svg")

    def test_board_raw_url(self) -> None:
        url = mm.board_raw_url("owner/repo", 42, seq=7)
        self.assertEqual(url, "../blob/game-boards/boards/42-007.svg?raw=1")

    def test_board_raw_url_with_nonce(self) -> None:
        url = mm.board_raw_url("owner/repo", 42, seq=7, nonce=7)
        self.assertEqual(url, "../blob/game-boards/boards/42-007.svg?raw=1&v=7")

    def test_upload_board_svg_returns_board_url(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        captured: dict[str, object] = {}

        def fake_get_file_contents(path: str, *, ref: str | None = None) -> None:
            captured["get_path"] = path
            captured["get_ref"] = ref
            return None

        def fake_create_or_update_file(
            path: str,
            *,
            content_b64: str,
            message: str,
            branch: str,
            sha: str | None = None,
        ) -> None:
            captured["path"] = path
            captured["message"] = message
            captured["branch"] = branch
            captured["sha"] = sha

        api.get_file_contents = fake_get_file_contents  # type: ignore[method-assign]
        api.create_or_update_file = fake_create_or_update_file  # type: ignore[method-assign]

        url = mm.upload_board_svg(
            api,
            repo="owner/repo",
            issue_number=42,
            seq=7,
            svg_content="<svg />",
        )

        self.assertEqual(url, "../blob/game-boards/boards/42-007.svg?raw=1")
        self.assertEqual(captured["path"], "boards/42-007.svg")
        self.assertEqual(captured["message"], "board: issue 42 turn 7")
        self.assertEqual(captured["branch"], "game-boards")
        self.assertIsNone(captured["sha"])

    def test_upload_board_svg_retries_once_after_conflict(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        calls: list[tuple[str, str | None]] = []
        attempts = {"count": 0}

        def fake_get_file_contents(path: str, *, ref: str | None = None) -> None:
            return None

        def fake_create_or_update_file(
            path: str,
            *,
            content_b64: str,
            message: str,
            branch: str,
            sha: str | None = None,
        ) -> None:
            calls.append((path, sha))
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise urllib.error.HTTPError(
                    url=f"https://api.github.com/repos/owner/repo/contents/{path}",
                    code=409,
                    msg="Conflict",
                    hdrs=None,
                    fp=io.BytesIO(b"conflict"),
                )

        api.get_file_contents = fake_get_file_contents  # type: ignore[method-assign]
        api.create_or_update_file = fake_create_or_update_file  # type: ignore[method-assign]

        with mock.patch("mistermind.github_api.time.sleep") as sleep_mock:
            url = mm.upload_board_svg(
                api,
                repo="owner/repo",
                issue_number=3,
                seq=2,
                svg_content="<svg />",
            )

        self.assertEqual(url, "../blob/game-boards/boards/3-002.svg?raw=1")
        self.assertEqual(calls, [("boards/3-002.svg", None), ("boards/3-002.svg", None)])
        sleep_mock.assert_called_once()

    def test_render_comment_with_board_image_url(self) -> None:
        state = mm.build_initial_state("owner/repo", 50, "owner")
        body = mm.render_comment(
            headline="Room initialized.",
            state=state,
            token="tok",
            reveal_solution=False,
            solution=["red", "blue", "green", "yellow"],
            board_image_url="https://example.com/board.svg",
        )
        # Should embed the image via <picture>/<img>
        self.assertIn("<picture>", body)
        self.assertIn('src="https://example.com/board.svg"', body)
        # Markdown table should be in a collapsible details as fallback
        self.assertIn("Board (text)</summary>", body)

    def test_render_comment_without_board_image_url(self) -> None:
        state = mm.build_initial_state("owner/repo", 51, "owner")
        body = mm.render_comment(
            headline="Room initialized.",
            state=state,
            token="tok",
            reveal_solution=False,
            solution=["red", "blue", "green", "yellow"],
        )
        # No image tag
        self.assertNotIn("![Mistermind Board]", body)
        # Markdown table should NOT be collapsible
        self.assertNotIn("Board (text)</summary>", body)
        # But table content should still be present
        self.assertIn("MISTERMIND", body)

    def test_render_comment_with_hint_block(self) -> None:
        state = mm.build_initial_state("owner/repo", 64, "owner", variant="hint")
        body = mm.render_comment(
            headline="Attempt 1: 1 exact, 1 color-only, 2 absent.",
            state=state,
            token="tok",
            reveal_solution=False,
            solution=["red", "blue", "green", "yellow"],
            hint_block="### Deductive Hints\n\n- test hint",
        )
        self.assertIn("Deductive Hints", body)


class ConductModerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = mm.build_initial_conduct_state("owner/repo", 40, "owner")
        self.policy = mm.default_moderation_policy()

    def test_owner_malformed_streak_and_reset(self) -> None:
        next_state, messages = mm.apply_conduct_input(
            previous=self.state,
            actor="owner",
            owner="owner",
            parsed_command={"kind": "guess", "error": "bad"},
            pre_response_spam=False,
            policy=self.policy,
        )
        self.assertEqual(next_state["owner_state"]["malformed_streak"], 1)
        self.assertEqual(next_state["owner_state"]["malformed_total"], 1)
        self.assertTrue(messages)

        reset_state, _ = mm.apply_conduct_input(
            previous=next_state,
            actor="owner",
            owner="owner",
            parsed_command={"kind": "guess", "guess": ["red", "blue", "green", "yellow"]},
            pre_response_spam=False,
            policy=self.policy,
        )
        self.assertEqual(reset_state["owner_state"]["malformed_streak"], 0)

    def test_owner_spam_second_warning_only_logs(self) -> None:
        s1, _ = mm.apply_conduct_input(
            previous=self.state,
            actor="owner",
            owner="owner",
            parsed_command={"kind": "status"},
            pre_response_spam=True,
            policy=self.policy,
        )
        self.assertEqual(s1["owner_state"]["pre_response_spam_warnings"], 1)
        s2, messages = mm.apply_conduct_input(
            previous=s1,
            actor="owner",
            owner="owner",
            parsed_command={"kind": "status"},
            pre_response_spam=True,
            policy=self.policy,
        )
        self.assertEqual(s2["owner_state"]["pre_response_spam_warnings"], 2)
        self.assertIsNone(s2["owner_state"]["cooldown_until"])
        self.assertTrue(any("logged" in msg for msg in messages))

    def test_non_owner_attempts_accumulate(self) -> None:
        s1, messages1 = mm.apply_conduct_input(
            previous=self.state,
            actor="other",
            owner="owner",
            parsed_command={"kind": "guess", "guess": ["red", "blue", "green", "yellow"]},
            pre_response_spam=False,
            policy=self.policy,
        )
        self.assertEqual(s1["actors"]["other"]["non_owner_attempts"], 1)
        self.assertTrue(messages1)

        s2, messages2 = mm.apply_conduct_input(
            previous=s1,
            actor="other",
            owner="owner",
            parsed_command={"kind": "status"},
            pre_response_spam=False,
            policy=self.policy,
        )
        self.assertEqual(s2["actors"]["other"]["non_owner_attempts"], 2)
        self.assertIsNone(s2["actors"]["other"]["muted_until"])
        self.assertTrue(messages2)

    def test_malformed_third_strike_only_logs(self) -> None:
        s1, _ = mm.apply_conduct_input(
            previous=self.state,
            actor="owner",
            owner="owner",
            parsed_command={"kind": "guess", "error": "bad1"},
            pre_response_spam=False,
            policy=self.policy,
        )
        s2, _ = mm.apply_conduct_input(
            previous=s1,
            actor="owner",
            owner="owner",
            parsed_command={"kind": "guess", "error": "bad2"},
            pre_response_spam=False,
            policy=self.policy,
        )
        s3, messages = mm.apply_conduct_input(
            previous=s2,
            actor="owner",
            owner="owner",
            parsed_command={"kind": "guess", "error": "bad3"},
            pre_response_spam=False,
            policy=self.policy,
        )
        self.assertFalse(s3["owner_state"]["disqualified"])
        self.assertTrue(any("logged for review" in msg for msg in messages))


class ModerationPolicyConfigTests(unittest.TestCase):
    def test_normalize_policy_fills_missing_and_orders_thresholds(self) -> None:
        policy = mm.normalize_moderation_policy(
            {
                "version": 7,
                "owner_malformed": {
                    "warning_streak": 4,
                    "cooldown_streak": 2,
                    "violation_streak": 1,
                },
                "owner_spam": {"warning_count": 5, "cooldown_count": 3, "violation_count": 4},
                "non_owner": {"mute_minutes": 30},
                "retention": {"max_actor_records": 10, "max_recent_events": 80},
            }
        )
        self.assertEqual(policy["version"], 7)
        self.assertGreaterEqual(
            policy["owner_malformed"]["cooldown_streak"],
            policy["owner_malformed"]["warning_streak"],
        )
        self.assertGreaterEqual(
            policy["owner_malformed"]["violation_streak"],
            policy["owner_malformed"]["cooldown_streak"],
        )
        self.assertGreaterEqual(
            policy["owner_spam"]["cooldown_count"], policy["owner_spam"]["warning_count"]
        )
        self.assertGreaterEqual(
            policy["owner_spam"]["violation_count"], policy["owner_spam"]["cooldown_count"]
        )


class StatsTests(unittest.TestCase):
    """Tests for the stats accumulation, leaderboard ranking, and SVG renderer."""

    def test_empty_stats_schema(self) -> None:
        stats = mm._empty_stats()
        self.assertEqual(stats["schema"], mm.STATS_SCHEMA)
        self.assertEqual(stats["games_played"], 0)
        self.assertEqual(stats["players"], {})
        self.assertEqual(stats["hall_of_fame"], [])
        self.assertEqual(stats["recent_games"], [])

    def test_update_stats_single_win(self) -> None:
        stats = mm._empty_stats()
        stats = mm.update_stats(stats, player="alice", result="won", attempts=4, issue_number=10)
        self.assertEqual(stats["games_played"], 1)
        self.assertEqual(stats["games_won"], 1)
        self.assertEqual(stats["games_lost"], 0)
        p = stats["players"]["alice"]
        self.assertEqual(p["games"], 1)
        self.assertEqual(p["wins"], 1)
        self.assertEqual(p["best_score"], 4)
        self.assertEqual(p["current_streak"], 1)
        self.assertEqual(p["best_streak"], 1)
        self.assertEqual(len(stats["hall_of_fame"]), 1)
        self.assertEqual(len(stats["recent_games"]), 1)

    def test_update_stats_single_loss(self) -> None:
        stats = mm._empty_stats()
        stats = mm.update_stats(stats, player="bob", result="lost", attempts=10, issue_number=5)
        self.assertEqual(stats["games_lost"], 1)
        p = stats["players"]["bob"]
        self.assertEqual(p["losses"], 1)
        self.assertEqual(p["current_streak"], 0)
        self.assertEqual(p["best_score"], 0)  # never won
        # Losses don't appear in hall of fame
        self.assertEqual(len(stats["hall_of_fame"]), 0)

    def test_update_stats_streak_tracking(self) -> None:
        stats = mm._empty_stats()
        # Win 3 in a row
        for i in range(3):
            stats = mm.update_stats(
                stats, player="alice", result="won", attempts=5, issue_number=i + 1
            )
        p = stats["players"]["alice"]
        self.assertEqual(p["current_streak"], 3)
        self.assertEqual(p["best_streak"], 3)

        # Loss breaks the streak
        stats = mm.update_stats(stats, player="alice", result="lost", attempts=10, issue_number=4)
        p = stats["players"]["alice"]
        self.assertEqual(p["current_streak"], 0)
        self.assertEqual(p["best_streak"], 3)  # preserved

    def test_update_stats_best_score_tracks_minimum(self) -> None:
        stats = mm._empty_stats()
        stats = mm.update_stats(stats, player="alice", result="won", attempts=6, issue_number=1)
        stats = mm.update_stats(stats, player="alice", result="won", attempts=3, issue_number=2)
        stats = mm.update_stats(stats, player="alice", result="won", attempts=5, issue_number=3)
        self.assertEqual(stats["players"]["alice"]["best_score"], 3)

    def test_hall_of_fame_capped(self) -> None:
        stats = mm._empty_stats()
        for i in range(15):
            stats = mm.update_stats(
                stats, player=f"p{i}", result="won", attempts=i + 1, issue_number=i
            )
        self.assertEqual(len(stats["hall_of_fame"]), mm.HALL_OF_FAME_CAP)
        # Should be sorted by attempts ascending
        attempts_list = [e["attempts"] for e in stats["hall_of_fame"]]
        self.assertEqual(attempts_list, sorted(attempts_list))

    def test_recent_games_capped(self) -> None:
        stats = mm._empty_stats()
        for i in range(30):
            stats = mm.update_stats(stats, player="alice", result="won", attempts=5, issue_number=i)
        self.assertEqual(len(stats["recent_games"]), mm.RECENT_GAMES_CAP)

    def test_player_stats_line_formatting(self) -> None:
        stats = mm._empty_stats()
        stats = mm.update_stats(stats, player="alice", result="won", attempts=3, issue_number=1)
        stats = mm.update_stats(stats, player="alice", result="won", attempts=5, issue_number=2)
        line = mm.player_stats_line(stats, "alice")
        self.assertIsNotNone(line)
        self.assertIn("2W / 0L", line)
        self.assertIn("Best: 3", line)

    def test_player_stats_line_unknown_player(self) -> None:
        stats = mm._empty_stats()
        self.assertIsNone(mm.player_stats_line(stats, "nobody"))

    def test_player_leaderboard_rank(self) -> None:
        stats = mm._empty_stats()
        # Alice: 3 wins, 0 losses (100%)
        for i in range(3):
            stats = mm.update_stats(stats, player="alice", result="won", attempts=5, issue_number=i)
        # Bob: 2 wins, 1 loss (67%)
        for i in range(2):
            stats = mm.update_stats(
                stats, player="bob", result="won", attempts=4, issue_number=10 + i
            )
        stats = mm.update_stats(stats, player="bob", result="lost", attempts=10, issue_number=12)
        rank_alice = mm.player_leaderboard_rank(stats, "alice")
        rank_bob = mm.player_leaderboard_rank(stats, "bob")
        self.assertIsNotNone(rank_alice)
        self.assertIn("#1", rank_alice)
        self.assertIn("#2", rank_bob)

    def test_player_leaderboard_rank_below_threshold(self) -> None:
        stats = mm._empty_stats()
        stats = mm.update_stats(stats, player="alice", result="won", attempts=3, issue_number=1)
        # Only 1 game -- below LEADERBOARD_MIN_GAMES
        rank = mm.player_leaderboard_rank(stats, "alice")
        self.assertIsNone(rank)

    def test_render_leaderboard_svg_empty(self) -> None:
        stats = mm._empty_stats()
        svg = mm.render_leaderboard_svg(stats)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        self.assertIn("MISTERMIND LEADERBOARD", svg)
        self.assertIn("No qualified players yet", svg)

    def test_render_leaderboard_svg_with_data(self) -> None:
        stats = mm._empty_stats()
        for i in range(3):
            stats = mm.update_stats(stats, player="alice", result="won", attempts=4, issue_number=i)
        for i in range(3):
            stats = mm.update_stats(
                stats, player="bob", result="won", attempts=6, issue_number=10 + i
            )
        svg = mm.render_leaderboard_svg(stats)
        self.assertIn("@alice", svg)
        self.assertIn("@bob", svg)
        self.assertIn("FASTEST SOLVES", svg)

    def test_avg_score_running_average(self) -> None:
        stats = mm._empty_stats()
        stats = mm.update_stats(stats, player="alice", result="won", attempts=4, issue_number=1)
        self.assertEqual(stats["players"]["alice"]["avg_score"], 4.0)
        stats = mm.update_stats(stats, player="alice", result="won", attempts=6, issue_number=2)
        self.assertEqual(stats["players"]["alice"]["avg_score"], 5.0)
        stats = mm.update_stats(stats, player="alice", result="won", attempts=2, issue_number=3)
        self.assertAlmostEqual(stats["players"]["alice"]["avg_score"], 4.0, places=1)


class APICallCounterTests(unittest.TestCase):
    """Tests for the lightweight per-session call counter on GitHubAPI."""

    def test_count_increments(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        api.calls = {}
        api._count("GET")
        api._count("GET")
        api._count("POST")
        self.assertEqual(api.calls, {"GET": 2, "POST": 1})
        self.assertEqual(api.total_calls, 3)

    def test_call_summary_format(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        api.calls = {"GET": 5, "POST": 2, "PUT": 1}
        api.remaining = None
        summary = api.call_summary()
        self.assertIn("GET:5", summary)
        self.assertIn("POST:2", summary)
        self.assertIn("PUT:1", summary)
        self.assertIn("total 8", summary)

    def test_call_summary_includes_remaining(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        api.calls = {"GET": 3}
        api.remaining = 742
        summary = api.call_summary()
        self.assertIn("remaining: 742", summary)

    def test_empty_counter(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        api.calls = {}
        api.remaining = None
        self.assertEqual(api.total_calls, 0)
        self.assertIn("total 0", api.call_summary())

    def test_budget_low_thresholds(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        api.calls = {}
        api.remaining = None
        # Unknown budget is not low
        self.assertFalse(api.budget_low)
        # Above watermark
        api.remaining = mm.RATE_LOW_WATERMARK + 1
        self.assertFalse(api.budget_low)
        # Below watermark
        api.remaining = mm.RATE_LOW_WATERMARK - 1
        self.assertTrue(api.budget_low)
        # Exactly at watermark
        api.remaining = mm.RATE_LOW_WATERMARK
        self.assertFalse(api.budget_low)  # < not <=

    def test_secondary_rate_limit_error(self) -> None:
        err = mm.SecondaryRateLimitError(reset_at=1234567890)
        self.assertEqual(err.reset_at, 1234567890)
        self.assertIn("1234567890", str(err))

    def test_close_issue_calls_patch_endpoint(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        captured: dict[str, object] = {}

        def fake_request(
            method: str, path: str, payload: dict[str, object] | None = None
        ) -> dict[str, object]:
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return {"state": "closed"}

        api._request = fake_request  # type: ignore[method-assign]
        out = api.close_issue(88)
        self.assertEqual(captured["method"], "PATCH")
        self.assertEqual(captured["path"], "/issues/88")
        self.assertEqual(captured["payload"], {"state": "closed"})
        self.assertEqual(out["state"], "closed")

    def test_set_repo_interaction_limit_calls_put_endpoint(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        captured: dict[str, object] = {}

        def fake_request(
            method: str, path: str, payload: dict[str, object] | None = None
        ) -> dict[str, object]:
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return {"limit": "collaborators_only", "expiry": "one_day"}

        api._request = fake_request  # type: ignore[method-assign]
        out = api.set_repo_interaction_limit(limit="collaborators_only", expiry="one_day")
        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(captured["path"], "/interaction-limits")
        self.assertEqual(
            captured["payload"],
            {"limit": "collaborators_only", "expiry": "one_day"},
        )
        self.assertEqual(out["limit"], "collaborators_only")

    def test_lock_issue_calls_put_endpoint(self) -> None:
        api = mm.GitHubAPI.__new__(mm.GitHubAPI)
        captured: dict[str, object] = {}

        def fake_request(method: str, path: str, payload: dict[str, object] | None = None) -> None:
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return None

        api._request = fake_request  # type: ignore[method-assign]
        api.lock_issue(77, reason="resolved")
        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(captured["path"], "/issues/77/lock")
        self.assertEqual(captured["payload"], {"lock_reason": "resolved"})


if __name__ == "__main__":
    unittest.main()
