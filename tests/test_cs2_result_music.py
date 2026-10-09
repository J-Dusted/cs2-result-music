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
    return payload


class GameStateTests(unittest.TestCase):
    def test_only_transition_into_gameover_triggers(self) -> None:
        state = app.GameState()
        self.assertIsNone(state.observe(gsi_payload(phase="live", timestamp=1)))
        result = state.observe(gsi_payload(phase="gameover", timestamp=2))
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "win")
        self.assertIsNone(state.observe(gsi_payload(phase="gameover", timestamp=3)))

    def test_new_round_resets_handled_state(self) -> None:
        state = app.GameState()
        state.observe(gsi_payload(phase="live", timestamp=1))
        self.assertEqual(
            state.observe(gsi_payload(phase="gameover", timestamp=2)).outcome,
            "win",
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
        result = state.observe(
            gsi_payload(
                phase="gameover",
                team="T",
                score_ct=5,
                score_t=13,
                timestamp=4,
            )
        )
        self.assertEqual(result.outcome, "win")

    def test_draw_does_not_trigger(self) -> None:
        state = app.GameState()
        state.observe(gsi_payload(phase="live", timestamp=1))
        self.assertIsNone(
            state.observe(
                gsi_payload(phase="gameover", score_ct=13, score_t=13, timestamp=2)
            )
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
                gsi_payload(
                    phase="gameover",
                    mode="deathmatch",
                    timestamp=1,
                )
            )
        )
        self.assertIsNone(
            app.determine_match_result(
                gsi_payload(
                    phase="gameover",
                    mode="armsrace",
                    timestamp=1,
                )
            )
        )

    def test_old_out_of_order_timestamp_is_ignored(self) -> None:
        state = app.GameState()
        state.observe(gsi_payload(phase="live", timestamp=10))
        state.observe(gsi_payload(phase="live", timestamp=20))
        self.assertIsNone(
            state.observe(gsi_payload(phase="gameover", timestamp=15))
        )

    def test_round_mode_triggers_once_on_each_round_result(self) -> None:
        state = app.GameState("round")
        self.assertIsNone(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=1,
                    round_phase="live",
                    timestamp=1,
                )
            )
        )
        result = state.observe(
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
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "win")
        self.assertIsNone(
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
            )
        )
        result = state.observe(
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
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "lose")

    def test_round_mode_requires_a_valid_winner(self) -> None:
        state = app.GameState("round")
        self.assertIsNone(
            state.observe(
                gsi_payload(
                    phase="live",
                    round_number=1,
                    round_phase="over",
                    timestamp=1,
                )
            )
        )


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_temp_dir()
        self.win_dir = self.root / "win"
        self.lose_dir = self.root / "lose"
        self.win_dir.mkdir()
        self.lose_dir.mkdir()
        for name in ("a.mp3", "b.mp3"):
            (self.win_dir / name).write_bytes(b"test")
        for name in ("a.wav", "b.wav"):
            (self.lose_dir / name).write_bytes(b"test")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_random_avoids_previous_track(self) -> None:
        library = app.MusicLibrary(
            self.win_dir,
            self.lose_dir,
            app.PlaybackRule("random"),
            app.PlaybackRule("random"),
            app.SequenceStore(self.root / "sequence.json"),
            random.Random(123),
        )
        first = library.choose("win")
        second = library.choose("win")
        self.assertNotEqual(first, second)

    def test_sequential_advances_and_persists(self) -> None:
        state_path = self.root / "sequence.json"
        first_library = app.MusicLibrary(
            self.win_dir,
            self.lose_dir,
            app.PlaybackRule("sequential"),
            app.PlaybackRule("sequential"),
            app.SequenceStore(state_path),
        )
        self.assertEqual(first_library.choose("win").read_bytes(), b"test")
        first = first_library.choose("win").name
        second = first_library.choose("win").name
        self.assertNotEqual(first, second)

        second_library = app.MusicLibrary(
            self.win_dir,
            self.lose_dir,
            app.PlaybackRule("sequential"),
            app.PlaybackRule("sequential"),
            app.SequenceStore(state_path),
        )
        self.assertEqual(second_library.choose("win").name, first)

    def test_specific_track_is_allowed_inside_directory(self) -> None:
        library = app.MusicLibrary(
            self.win_dir,
            self.lose_dir,
            app.PlaybackRule("specific", "b.mp3"),
            app.PlaybackRule("random"),
            app.SequenceStore(self.root / "sequence.json"),
        )
        self.assertEqual(library.choose("win"), self.win_dir / "b.mp3")

    def test_specific_track_cannot_escape_directory(self) -> None:
        library = app.MusicLibrary(
            self.win_dir,
            self.lose_dir,
            app.PlaybackRule("specific", "../lose/a.wav"),
            app.PlaybackRule("random"),
            app.SequenceStore(self.root / "sequence.json"),
        )
        with self.assertRaises(FileNotFoundError):
            library.choose("win")


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

        staged = app._stage_track(source, staging, "win")

        self.assertEqual(staged.parent, staging)
        self.assertEqual(staged.suffix, ".mp3")
        self.assertTrue(staged.name.startswith("win-"))
        self.assertNotEqual(staged, source)
        self.assertEqual(staged.read_bytes(), b"audio-data")

    def test_stage_track_uses_a_new_name_for_each_request(self) -> None:
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        source = root / "song.wav"
        source.write_bytes(b"audio-data")

        first = app._stage_track(source, root / "staging", "lose")
        second = app._stage_track(source, root / "staging", "lose")

        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
