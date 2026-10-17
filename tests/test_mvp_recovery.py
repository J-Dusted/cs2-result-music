import json
import queue
from types import SimpleNamespace

import pytest

from cs2music.config import Settings
from cs2music.controller import Controller
from cs2music.events import EventEngine
from cs2music.result_log import ResultLog


def state(phase="live", number=0, own=True, mvps=2, team="CT", winner="CT"):
    return {"provider": {"appid": 730, "steamid": "owner-secret-id", "timestamp": 1234},
            "map": {"name": "de_test", "phase": "live", "round": number,
                    "team_ct": {"score": number}, "team_t": {"score": 0}},
            "round": {"phase": phase, "win_team": winner},
            "player": {"steamid": "owner-secret-id" if own else "teammate-secret-id",
                       "name": "private-name", "team": team,
                       "match_stats": {"mvps": mvps}}}


def events(actions):
    return [a.event for a in actions if a.kind == "play"]


def waiting_engine():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    assert not events(engine.ingest(state("over", own=False, mvps=99), 1))
    return engine


def test_spectator_recovery_after_seven_seconds_only_plays_mvp():
    engine = waiting_engine()
    for now in range(2, 8):
        assert not events(engine.ingest(state("over", own=False, mvps=99), now))
        assert not events(engine.tick(now + .5))
        assert engine.current_mvps is None and engine.mvps == 2
        assert engine.result_status == "waiting_self"
    assert events(engine.ingest(state("freezetime", number=1, mvps=3), 8)) == ["mvp"]
    assert engine.result_mvp_baseline == 2 and engine.round_mvp_baseline == 3
    assert not events(engine.ingest(state("freezetime", number=1, mvps=3), 9))
    assert not events(engine.tick(9.5))


def test_recovery_without_mvp_restarts_full_confirmation_window():
    engine = waiting_engine()
    for now in range(2, 8):
        engine.ingest(state("over", own=False), now)
    assert not events(engine.ingest(state("freezetime", number=1), 8))
    assert engine.result_status == "confirming"
    assert not events(engine.tick(9.49))
    assert events(engine.tick(9.5)) == ["victory"]
    assert not events(engine.tick(9.6))


def test_split_recovery_mvp_arrives_after_identity():
    engine = waiting_engine()
    assert not events(engine.ingest(state("freezetime", number=1), 2))
    assert not events(engine.tick(3.4))
    assert events(engine.ingest(state("freezetime", number=1, mvps=3), 3.49)) == ["mvp"]
    assert not events(engine.tick(3.5))


def test_losing_stats_again_restarts_window_again():
    engine = waiting_engine()
    engine.ingest(state("over"), 2)
    engine.ingest(state("over", own=False), 3)
    assert not events(engine.tick(3.6))
    engine.ingest(state("over"), 4)
    assert not events(engine.tick(5.49))
    assert events(engine.tick(5.5)) == ["victory"]


@pytest.mark.parametrize("player_valid", [True, False])
def test_allplayers_fallback_and_player_precedence(player_valid):
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    packet = state("over", mvps=3)
    packet["allplayers"] = {"owner-secret-id": {"team": "CT", "match_stats": {"mvps": 8}}}
    if not player_valid:
        packet["player"]["match_stats"] = {}
    assert events(engine.ingest(packet, 1)) == ["mvp"]
    assert engine.current_mvps == (3 if player_valid else 8)
    assert engine.own_stats_source == ("player" if player_valid else "allplayers")


def test_observing_teammate_with_own_allplayers_stats_plays_immediately():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    packet = state("over", own=False, mvps=99)
    packet["allplayers"] = {"owner-secret-id": {"team": "CT", "match_stats": {"mvps": 3}}}
    assert events(engine.ingest(packet, 1)) == ["mvp"]
    assert engine.current_mvps == 3 and engine.own_stats_source == "allplayers"


def test_late_winner_field_does_not_reopen_already_confirmed_mvp():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    assert events(engine.ingest(state("over", mvps=3, winner=None), 1)) == ["mvp"]
    assert not events(engine.ingest(state("over", mvps=3), 2))
    assert engine.pending_result is None and engine.result_status == "mvp"


def test_continuously_available_stats_keep_normal_and_late_upgrade_behavior():
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    assert not events(engine.ingest(state("over"), 1))
    assert events(engine.tick(2.5)) == ["victory"]
    assert events(engine.ingest(state("over", mvps=3), 3)) == ["mvp"]


@pytest.mark.parametrize("value", [None, -1, True, "3", 3.5])
def test_invalid_current_stats_are_missing_not_zero(value):
    engine = EventEngine(Settings())
    engine.ingest(state(), 0)
    packet = state("over", mvps=value)
    engine.ingest(packet, 1)
    assert engine.current_mvps is None and engine.mvps == 2
    assert engine.own_stats_source == "missing"
    assert not events(engine.tick(3))


def test_missing_original_baseline_cannot_be_filled_from_end_stats():
    engine = EventEngine(Settings())
    engine.ingest(state(mvps=None), 0)
    engine.ingest(state("over", mvps=3), 1)
    assert engine.result_status == "waiting_baseline"
    assert not events(engine.tick(3))
    engine.ingest(state("freezetime", number=1, mvps=3), 4)
    assert engine.result_mvp_baseline is None
    assert not events(engine.tick(5))


def test_unresolved_round_is_skipped_at_live_even_if_music_may_continue():
    engine = waiting_engine()
    engine.s.tracks["victory"].stop_on_live = False
    engine.ingest(state("freezetime", number=1, own=False), 2)
    assert not events(engine.ingest(state("live", number=1, mvps=3), 3))
    assert engine.pending_result is None and engine.result_round is None
    assert engine.result_status == "skipped"
    assert not events(engine.ingest(state("live", number=1, mvps=3), 4))


@pytest.mark.parametrize("cancel", ["map", "identity", "stale", "warmup", "manual"])
def test_cancelled_wait_never_replays_old_result(cancel):
    engine = waiting_engine()
    if cancel == "stale":
        engine.tick(6.1)
    elif cancel == "manual":
        engine.cancel_results("手动停止")
    else:
        packet = state("over", mvps=3)
        if cancel == "map": packet["map"]["name"] = "de_other"
        if cancel == "identity": packet["provider"]["steamid"] = "another-owner"
        if cancel == "warmup": packet["map"]["phase"] = "warmup"
        assert not events(engine.ingest(packet, 2))
    assert engine.pending_result is None and engine.result_round is None
    assert not events(engine.ingest(state("over", mvps=3), 7 if cancel == "stale" else 3))


def test_halftime_music_cancels_previous_pending_result_and_late_mvp():
    engine = EventEngine(Settings())
    engine.ingest(state(number=11), 0)
    engine.ingest(state("over", number=11, own=False), 1)
    assert events(engine.ingest(state("freezetime", number=12, team="T", mvps=3), 2)) == ["side"]
    assert engine.pending_result is None and engine.result_round is None
    assert not events(engine.ingest(state("freezetime", number=12, team="T", mvps=3), 3))


def test_mvp_recovered_while_buying_is_not_counted_again_next_round():
    engine = waiting_engine()
    engine.ingest(state("freezetime", number=1, own=False), 2)
    assert events(engine.ingest(state("freezetime", number=1, mvps=3), 3)) == ["mvp"]
    engine.ingest(state("live", number=1, own=False), 4)
    assert engine.round_mvp_baseline == 3
    assert not events(engine.ingest(state("over", number=1, mvps=3), 5))
    assert events(engine.tick(6.5)) == ["victory"]


def test_final_round_can_wait_for_own_stats_in_gameover():
    engine = waiting_engine()
    packet = state("over", mvps=3)
    packet["map"]["phase"] = "gameover"
    assert events(engine.ingest(packet, 2)) == ["mvp"]


def test_trace_is_persistent_private_deduplicated_and_rotates(tmp_path):
    path = tmp_path / "gsi-results.jsonl"
    trace = ResultLog(path)
    engine = waiting_engine()
    trace.record(engine, received=1, processed=1.02, provider_timestamp=1234)
    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["queue_delay_ms"] == 20
    assert first["own_stats_source"] == "missing" and first["remembered_mvps"] == 2
    trace.record(engine, received=2, processed=2.01, provider_timestamp=1235)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert trace.handler.maxBytes == 2 * 1024 * 1024 and trace.handler.backupCount == 3
    trace.handler.maxBytes = 2000
    for n in range(12):
        actions = engine.ingest(state("freezetime", number=1, mvps=3), 2 + n / 10)
        trace.record(engine, actions, cause=f"test-{n}")
    trace.close()
    assert path.exists() and path.with_name(path.name + ".3").exists()
    assert not path.with_name(path.name + ".4").exists()
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    assert "owner-secret-id" not in text and "private-name" not in text
    assert "teammate-secret-id" not in text and '"auth"' not in text
    for row in path.read_text(encoding="utf-8").splitlines():
        assert json.loads(row)["time"].endswith("+00:00")


def test_trace_creation_failure_warns_once_without_interrupting(tmp_path):
    blocker = tmp_path / "file"
    blocker.touch()
    warnings = []
    trace = ResultLog(blocker / "gsi-results.jsonl", warn=warnings.append)
    trace.record(waiting_engine())
    trace.record(waiting_engine())
    trace.close()
    assert len(warnings) == 1 and trace.failed


def test_trace_emit_failure_warns_once_without_interrupting(tmp_path, monkeypatch):
    warnings = []
    trace = ResultLog(tmp_path / "gsi-results.jsonl", warn=warnings.append)
    engine = waiting_engine()
    trace.record(engine)
    monkeypatch.setattr(trace.handler, "doRollover", lambda: (_ for _ in ()).throw(PermissionError("locked")))
    trace.handler.maxBytes = 1
    trace.record(engine, cause="forced-change")
    trace.record(engine, cause="another-change")
    assert len(warnings) == 1 and trace.failed


def test_trace_closed_stream_failure_is_nonfatal(tmp_path):
    warnings = []
    trace = ResultLog(tmp_path / "gsi-results.jsonl", warn=warnings.append)
    engine = waiting_engine()
    trace.record(engine)
    trace.handler.stream.close()
    trace.record(engine, cause="closed-stream")
    trace.close()
    assert trace.failed and len(warnings) == 1


def test_controller_stop_music_cancels_pending_and_late_mvp(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2MUSIC_DATA_DIR", str(tmp_path))
    controller = Controller(Settings())
    controller.events = waiting_engine()
    controller.stop_music()
    assert controller.events.pending_result is None
    assert not events(controller.events.ingest(state("over", mvps=3), 2))
    controller.close()
    rows = [json.loads(line) for line in (tmp_path / "gsi-results.jsonl").read_text(encoding="utf-8").splitlines()]
    manual = next(row for row in rows if row["cause"] == "manual_stop")
    assert manual["status"] == "skipped"


def test_controller_tick_persists_decision_before_audio_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2MUSIC_DATA_DIR", str(tmp_path))
    controller = Controller(Settings())
    controller.events = waiting_engine()
    controller.running = True
    controller.server = SimpleNamespace(messages=queue.Queue())
    controller.server.messages.put((2, state("freezetime", number=1, mvps=3)))
    controller.audio.healthy = lambda: True
    controller.ptt = SimpleNamespace(update=lambda *_args: None)
    dispatched = []
    controller._actions = lambda actions: dispatched.extend(actions)
    monkeypatch.setattr("cs2music.controller.time.monotonic", lambda: 2.02)
    controller.tick()
    assert events(dispatched) == ["mvp"] and controller.running
    rows = [json.loads(line) for line in (tmp_path / "gsi-results.jsonl").read_text(encoding="utf-8").splitlines()]
    row = next(row for row in rows if row["actions"])
    assert row["queue_delay_ms"] == 20 and row["result_mvp_baseline"] == 2
    assert row["current_mvps"] == 3 and row["actions"][0]["reason"]
    controller.server = controller.ptt = None
    controller.close()
