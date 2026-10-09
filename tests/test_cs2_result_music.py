from __future__ import annotations

import json
import random
import shutil
import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = PROJECT_ROOT / "work" / "test-temp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT_ROOT))

import cs2_result_music as app  # noqa: E402


def make_temp_dir() -> Path:
    path = TEST_TEMP_ROOT / f"case-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def gsi_payload(
    *,
    phase: str,
    team: str = "CT",
    score_ct: int | None = 13,
    score_t: int | None = 8,
    mode: str = "competitive",
    map_name: str = "de_mirage",
    timestamp: int = 100,
    round_number: int | None = None,
    round_phase: str | None = None,
    round_winner: str | None = None,
    player_name: str | None = None,
    mvps: int | None = None,
    bomb_state: str | None = None,
    bomb_countdown: float | None = None,
    phase_countdowns: dict | None = None,
) -> dict:
    payload = {
        "provider": {"timestamp": timestamp},
        "map": {
            "name": map_name,
            "mode": mode,
            "phase": phase,
        },
        "player": {"team": team},
    }
    if score_ct is not None:
        payload["map"]["team_ct"] = {"score": score_ct}
    if score_t is not None:
        payload["map"]["team_t"] = {"score": score_t}
    if round_number is not None:
        payload["map"]["round"] = round_number
    if round_phase is not None or round_winner is not None:
        payload["round"] = {}
        if round_phase is not None:
            payload["round"]["phase"] = round_phase
        if round_winner is not None:
            payload["round"]["win_team"] = round_winner
    if player_name is not None:
        payload["player"]["name"] = player_name
    if mvps is not None:
        payload["player"]["match_stats"] = {"mvps": mvps}
    if bomb_state is not None or bomb_countdown is not None:
        payload["bomb"] = {}
        if bomb_state is not None:
            payload["bomb"]["state"] = bomb_state
        if bomb_countdown is not None:
            payload["bomb"]["countdown"] = bomb_countdown
    if phase_countdowns is not None:
        payload["phase_countdowns"] = phase_countdowns
    return payload


def event_names(events: list[app.MusicEvent]) -> list[str]:
    return [event.event for event in events]


class ConfigTests(unittest.TestCase):
    def test_old_config_gets_new_event_defaults(self) -> None:
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        path = root / "config.json"
        path.write_text(
            json.dumps(
                {
                    "gsi": {"port": 3001, "token": "secret"},
                    "music": {
                        "root": "music",
                        "win_dir": "win",
                        "lose_dir": "lose",
                    },
                    "playback": {
                        "win": {"mode": "random"},
                        "lose": {"mode": "random"},
                    },
                    "trigger": {"mode": "round"},
                }
            ),
            encoding="utf-8",
        )

        config = app.AppConfig.load(path)

        self.assertEqual(config.start_dir, (root / "music" / "start").resolve())
        self.assertEqual(config.mvp_dir, (root / "music" / "mvp").resolve())
        self.assertEqual(config.bomb10_dir, (root / "music" / "bomb10").resolve())
        self.assertTrue(config.start_enabled)
        self.assertTrue(config.mvp_enabled)
        self.assertTrue(config.bomb10_enabled)
        self.assertEqual(config.bomb10_lead_seconds, 10.0)

    def test_new_config_parses_names_and_rules(self) -> None:
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        path = root / "config.json"
        path.write_text(
            json.dumps(
                {
                    "gsi": {"port": 3001, "token": "secret"},
                    "music": {
                        "root": "library",
                        "win_dir": "W",
                        "lose_dir": "L",
                        "start_dir": "S",
                        "mvp_dir": "M",
                        "bomb10_dir": "B",
                    },
                    "playback": {
                        "win": {"mode": "random"},
                        "lose": {"mode": "random"},
                        "start": {"mode": "sequential"},
                        "mvp": {"mode": "specific", "specific": "mvp.mp3"},
                        "bomb10": {"mode": "random"},
                    },
                    "trigger": {
                        "mode": "round",
                        "start": False,
                        "mvp": {
                            "enabled": True,
                            "player_names": [" Player One ", "player one", "Two"],
                        },
                        "bomb10": {
                            "enabled": True,
                            "lead_seconds": 8,
                            "fallback_plant_seconds": 28,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        config = app.AppConfig.load(path)

        self.assertFalse(config.start_enabled)
        self.assertEqual(config.mvp_player_names, ("Player One", "Two"))
        self.assertEqual(config.mvp_rule.mode, "specific")
        self.assertEqual(config.bomb10_lead_seconds, 8.0)
        self.assertEqual(config.bomb10_fallback_plant_seconds, 28.0)


class GameStateTests(unittest.TestCase):
    def test_only_transition_into_gameover_triggers(self) -> None:
        state = app.GameState()
        self.assertEqual(state.observe(gsi_payload(phase="live", timestamp=1)), [])
        self.assertEqual(
            event_names(state.observe(gsi_payload(phase="gameover", timestamp=2))),
            ["win"],
        )
        self.assertEqual(
            state.observe(gsi_payload(phase="gameover", timestamp=3)),
            [],
        )

    def test_new_round_resets_handled_state(self) -> None:
        state = app.GameState()
        state.observe(gsi_payload(phase="live", timestamp=1))
        self.assertEqual(
            event_names(state.observe(gsi_payload(phase="gameover", timestamp=2))),
            ["win"],
        )
        state.observe(
            gsi_payload(
                phase="live",
                team="T",
                score_ct=5,
                score_t=13,
                timestamp=3,
            )
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="gameover",
                        team="T",
                        score_ct=5,
                        score_t=13,
                        timestamp=4,
                    )
                )
            ),
            ["win"],
        )

    def test_draw_does_not_trigger(self) -> None:
        state = app.GameState()
        state.observe(gsi_payload(phase="live", timestamp=1))
        self.assertEqual(
            state.observe(
                gsi_payload(phase="gameover", score_ct=13, score_t=13, timestamp=2)
            ),
            [],
        )

    def test_missing_team_and_score_do_not_trigger(self) -> None:
        self.assertIsNone(
            app.determine_match_result(
                gsi_payload(phase="gameover", team="", timestamp=1)
            )
        )
        self.assertIsNone(
            app.determine_match_result(
                gsi_payload(phase="gameover", score_ct=None, timestamp=1)
            )
        )

    def test_solo_modes_do_not_trigger(self) -> None:
        self.assertIsNone(
            app.determine_match_result(
                gsi_payload(phase="gameover", mode="deathmatch", timestamp=1)
            )
        )
        self.assertIsNone(
            app.determine_match_result(
                gsi_payload(phase="gameover", mode="armsrace", timestamp=1)
            )
        )

    def test_old_out_of_order_timestamp_is_ignored(self) -> None:
        state = app.GameState()
        state.observe(gsi_payload(phase="live", timestamp=10))
        state.observe(gsi_payload(phase="live", timestamp=20))
        self.assertEqual(
            state.observe(gsi_payload(phase="gameover", timestamp=15)),
            [],
        )

    def test_round_mode_triggers_once_on_each_round_result(self) -> None:
        state = app.GameState("round")
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=1,
                    round_phase="live",
                    timestamp=1,
                )
            ),
            [],
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        round_number=1,
                        round_phase="over",
                        round_winner="CT",
                        score_ct=1,
                        score_t=0,
                        timestamp=2,
                    )
                )
            ),
            ["win"],
        )
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=1,
                    round_phase="over",
                    round_winner="CT",
                    score_ct=1,
                    score_t=0,
                    timestamp=3,
                )
            ),
            [],
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        round_number=2,
                        round_phase="over",
                        round_winner="T",
                        score_ct=1,
                        score_t=1,
                        timestamp=4,
                    )
                )
            ),
            ["lose"],
        )

    def test_round_mode_requires_a_valid_winner(self) -> None:
        state = app.GameState("round")
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=1,
                    round_phase="over",
                    timestamp=1,
                )
            ),
            [],
        )

    def test_start_triggers_after_warmup_once_per_side(self) -> None:
        state = app.GameState("round")
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="warmup",
                    round_number=0,
                    round_phase="freezetime",
                    timestamp=1,
                )
            ),
            [],
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        round_number=0,
                        round_phase="freezetime",
                        timestamp=2,
                    )
                )
            ),
            ["start"],
        )
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=1,
                    round_phase="freezetime",
                    timestamp=3,
                )
            ),
            [],
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        team="T",
                        round_number=12,
                        round_phase="freezetime",
                        timestamp=4,
                    )
                )
            ),
            ["start"],
        )

    def test_start_can_fill_initial_round_but_not_mid_match(self) -> None:
        early = app.GameState("round")
        self.assertEqual(
            event_names(
                early.observe(
                    gsi_payload(
                        phase="live",
                        round_number=0,
                        round_phase="freezetime",
                        timestamp=1,
                    )
                )
            ),
            ["start"],
        )

        # A map whose first round index is 1 must not be treated as the first
        # round unless warmup was observed, otherwise a mid-map launch replays
        # the start music on the second round.
        second = app.GameState("round")
        self.assertEqual(
            second.observe(
                gsi_payload(
                    phase="live",
                    round_number=1,
                    round_phase="freezetime",
                    timestamp=1,
                )
            ),
            [],
        )

        late = app.GameState("round")
        self.assertEqual(
            late.observe(
                gsi_payload(
                    phase="live",
                    round_number=7,
                    round_phase="freezetime",
                    timestamp=1,
                )
            ),
            [],
        )

    def test_mvp_requires_growth_and_matching_name(self) -> None:
        state = app.GameState("round", mvp_player_names=("  Player One ",))
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    player_name="player one",
                    mvps=2,
                    timestamp=1,
                )
            ),
            [],
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        player_name="PLAYER ONE",
                        mvps=3,
                        timestamp=2,
                    )
                )
            ),
            ["mvp"],
        )
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    player_name="PLAYER ONE",
                    mvps=3,
                    timestamp=3,
                )
            ),
            [],
        )

    def test_empty_mvp_names_means_local_player(self) -> None:
        state = app.GameState("round", mvp_player_names=())
        state.observe(
            gsi_payload(
                phase="live",
                player_name="Any Local Name",
                mvps=1,
                timestamp=1,
            )
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        player_name="Any Local Name",
                        mvps=2,
                        timestamp=2,
                    )
                )
            ),
            ["mvp"],
        )

    def test_mvp_has_priority_over_round_result(self) -> None:
        state = app.GameState("round", mvp_player_names=("Player",))
        state.observe(
            gsi_payload(
                phase="live",
                round_number=1,
                round_phase="live",
                player_name="Player",
                mvps=0,
                timestamp=1,
            )
        )
        events = state.observe(
            gsi_payload(
                phase="live",
                round_number=1,
                round_phase="over",
                round_winner="CT",
                score_ct=1,
                score_t=0,
                player_name="Player",
                mvps=1,
                timestamp=2,
            )
        )
        self.assertEqual(event_names(events), ["mvp", "win"])

    def test_bomb_countdown_triggers_once_at_threshold(self) -> None:
        state = app.GameState("round", bomb10_lead_seconds=10)
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=3,
                    round_phase="live",
                    bomb_state="planted",
                    bomb_countdown=11,
                    timestamp=1,
                )
            ),
            [],
        )
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        round_number=3,
                        round_phase="live",
                        bomb_state="planted",
                        bomb_countdown=9.9,
                        timestamp=2,
                    )
                )
            ),
            ["bomb10"],
        )
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=3,
                    round_phase="live",
                    bomb_state="planted",
                    bomb_countdown=9.8,
                    timestamp=3,
                )
            ),
            [],
        )

    def test_phase_countdowns_can_drive_bomb_event(self) -> None:
        state = app.GameState("round", bomb10_lead_seconds=10)
        self.assertEqual(
            event_names(
                state.observe(
                    gsi_payload(
                        phase="live",
                        round_number=3,
                        round_phase="live",
                        bomb_state="planted",
                        phase_countdowns={"phase": "bomb", "phase_ends_in": 9.5},
                        timestamp=1,
                    )
                )
            ),
            ["bomb10"],
        )

    def test_bomb_fallback_uses_local_30_second_timer(self) -> None:
        now = [0.0]
        state = app.GameState(
            "round",
            bomb10_lead_seconds=10,
            bomb10_fallback_plant_seconds=30,
            clock=lambda: now[0],
        )
        self.assertEqual(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=4,
                    round_phase="live",
                    bomb_state="planted",
                    timestamp=1,
                )
            ),
            [],
        )
        now[0] = 29.9
        self.assertEqual(state.poll(), [])
        now[0] = 30.0
        self.assertEqual(event_names(state.poll()), ["bomb10"])
        self.assertEqual(state.poll(), [])

    def test_bomb_fallback_cancels_when_defused_or_round_ends(self) -> None:
        now = [0.0]
        state = app.GameState("round", clock=lambda: now[0])
        state.observe(
            gsi_payload(
                phase="live",
                round_number=4,
                round_phase="live",
                bomb_state="planted",
                timestamp=1,
            )
        )
        state.observe(
            gsi_payload(
                phase="live",
                round_number=4,
                round_phase="live",
                bomb_state="defused",
                timestamp=2,
            )
        )
        now[0] = 31.0
        self.assertEqual(state.poll(), [])

        state.observe(
            gsi_payload(
                phase="live",
                round_number=5,
                round_phase="live",
                bomb_state="planted",
                timestamp=3,
            )
        )
        state.observe(
            gsi_payload(
                phase="live",
                round_number=5,
                round_phase="over",
                round_winner="CT",
                score_ct=1,
                score_t=0,
                bomb_state="planted",
                timestamp=4,
            )
        )
        now[0] = 100.0
        self.assertEqual(state.poll(), [])


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_temp_dir()
        self.win_dir = self.root / "win"
        self.lose_dir = self.root / "lose"
        self.start_dir = self.root / "start"
        self.mvp_dir = self.root / "mvp"
        self.bomb10_dir = self.root / "bomb10"
        for directory in (
            self.win_dir,
            self.lose_dir,
            self.start_dir,
            self.mvp_dir,
            self.bomb10_dir,
        ):
            directory.mkdir()
        for name in ("a.mp3", "b.mp3"):
            (self.win_dir / name).write_bytes(b"test")
        for name in ("a.wav", "b.wav"):
            (self.lose_dir / name).write_bytes(b"test")
        for name in ("s1.mp3", "s2.mp3"):
            (self.start_dir / name).write_bytes(b"test")
        for name in ("m1.mp3", "m2.mp3"):
            (self.mvp_dir / name).write_bytes(b"test")
        for name in ("b1.mp3", "b2.mp3"):
            (self.bomb10_dir / name).write_bytes(b"test")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def make_library(self, rules: dict[str, app.PlaybackRule]) -> app.MusicLibrary:
        directories = {
            "win": self.win_dir,
            "lose": self.lose_dir,
            "start": self.start_dir,
            "mvp": self.mvp_dir,
            "bomb10": self.bomb10_dir,
        }
        return app.MusicLibrary(
            directories,
            rules,
            app.SequenceStore(self.root / "sequence.json"),
        )

    def test_random_avoids_previous_track(self) -> None:
        rules = {event: app.PlaybackRule("random") for event in app.EVENT_KINDS}
        library = app.MusicLibrary(
            {
                "win": self.win_dir,
                "lose": self.lose_dir,
                "start": self.start_dir,
                "mvp": self.mvp_dir,
                "bomb10": self.bomb10_dir,
            },
            rules,
            app.SequenceStore(self.root / "sequence.json"),
            random.Random(123),
        )
        first = library.choose("win")
        second = library.choose("win")
        self.assertNotEqual(first, second)

    def test_sequential_advances_and_persists_per_event(self) -> None:
        rules = {event: app.PlaybackRule("sequential") for event in app.EVENT_KINDS}
        first_library = self.make_library(rules)
        first = first_library.choose("mvp").name
        second = first_library.choose("mvp").name
        self.assertNotEqual(first, second)
        self.assertEqual(first_library.choose("bomb10").name, "b1.mp3")

        second_library = self.make_library(rules)
        self.assertEqual(second_library.choose("mvp").name, first)

    def test_specific_track_is_allowed_inside_directory(self) -> None:
        rules = {event: app.PlaybackRule("random") for event in app.EVENT_KINDS}
        rules["mvp"] = app.PlaybackRule("specific", "m2.mp3")
        library = self.make_library(rules)
        self.assertEqual(library.choose("mvp"), self.mvp_dir / "m2.mp3")

    def test_specific_track_cannot_escape_directory(self) -> None:
        rules = {event: app.PlaybackRule("random") for event in app.EVENT_KINDS}
        rules["mvp"] = app.PlaybackRule("specific", "../lose/a.wav")
        library = self.make_library(rules)
        with self.assertRaises(FileNotFoundError):
            library.choose("mvp")


class CommandTests(unittest.TestCase):
    def test_publish_play_and_stop_are_atomic_json(self) -> None:
        directory = make_temp_dir()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "command.txt"
        publisher = app.CommandPublisher(path)
        request = publisher.publish_play(Path("C:/music/win/song.mp3"), -6.0)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["request"], request)
        self.assertEqual(payload["op"], "play")
        self.assertEqual(payload["gain_db"], -6.0)
        self.assertEqual(payload["file"], "C:\\music\\win\\song.mp3")

        stop_request = publisher.publish_stop()
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["request"], stop_request)
        self.assertEqual(payload["op"], "stop")


class GsiCaptureTests(unittest.TestCase):
    def test_capture_appends_one_json_object_per_line(self) -> None:
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        path = root / "gsi-capture.jsonl"
        capture = app.GsiCapture(path)

        capture.append({"provider": {"timestamp": 1}, "player": {"team": "CT"}})
        capture.append({"provider": {"timestamp": 2}, "player": {"team": "T"}})

        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["player"]["team"], "CT")
        self.assertEqual(json.loads(lines[1])["player"]["team"], "T")


class GsiAuthTests(unittest.TestCase):
    def test_body_token_is_accepted(self) -> None:
        self.assertEqual(
            app._extract_gsi_token({"auth": {"token": "abc"}}, {}),
            "abc",
        )

    def test_query_token_still_works(self) -> None:
        self.assertEqual(
            app._extract_gsi_token({}, {"auth": ["abc"]}),
            "abc",
        )


class StagingTests(unittest.TestCase):
    def test_stage_track_copies_content_and_keeps_extension(self) -> None:
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        source = root / "music" / "song.mp3"
        source.parent.mkdir()
        source.write_bytes(b"audio-data")
        staging = root / "staging"

        staged = app._stage_track(source, staging, "start")

        self.assertEqual(staged.parent, staging)
        self.assertEqual(staged.suffix, ".mp3")
        self.assertTrue(staged.name.startswith("start-"))
        self.assertNotEqual(staged, source)
        self.assertEqual(staged.read_bytes(), b"audio-data")

    def test_stage_track_uses_a_new_name_for_each_request(self) -> None:
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        source = root / "song.wav"
        source.write_bytes(b"audio-data")

        first = app._stage_track(source, root / "staging", "bomb10")
        second = app._stage_track(source, root / "staging", "bomb10")

        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
