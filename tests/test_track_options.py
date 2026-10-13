import json
from types import SimpleNamespace

import numpy as np
import pytest

from cs2music.audio import AudioEngine, FRAME
from cs2music.config import EVENT_LABELS, Settings
from cs2music.controller import Controller
from cs2music.events import Action, EventEngine


@pytest.mark.parametrize("version", [1, 2, 3])
def test_settings_defaults_migration_and_independent_options(tmp_path, version):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": version, "music_gain": .25,
                                "tracks": {"start": {"path": "中文/音乐"}}}), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.version == 3 and settings.music_gain == .25
    assert settings.tracks["start"].path == "中文/音乐"
    assert all(t.gain == 1 and t.stop_on_live for t in settings.tracks.values())
    settings.tracks["start"].gain = .45
    settings.tracks["start"].stop_on_live = False
    settings.save(path)
    loaded = Settings.load(path)
    assert loaded.tracks["start"].gain == .45 and not loaded.tracks["start"].stop_on_live
    assert loaded.tracks["victory"].gain == 1 and loaded.tracks["victory"].stop_on_live


@pytest.mark.parametrize("field,value", [("gain", float("nan")), ("gain", -1), ("gain", 2.01),
                                          ("gain", True), ("stop_on_live", "false")])
def test_invalid_track_options(field, value):
    settings = Settings()
    setattr(settings.tracks["mvp"], field, value)
    with pytest.raises(ValueError):
        settings.validate()


@pytest.mark.parametrize("event", EVENT_LABELS)
@pytest.mark.parametrize("stop", [True, False])
def test_live_policy_applies_to_each_selected_event_and_pending_decode(tmp_path, event, stop):
    path = tmp_path / "song.wav"
    path.touch()
    settings = Settings(ptt_verified=True)
    settings.tracks[event].path = str(path)
    settings.tracks[event].stop_on_live = stop
    controller = Controller(settings)
    controller.audio.close()
    calls = []
    # play returns while decode is still pending; event need not be published by audio yet.
    controller.audio = SimpleNamespace(stop_music=lambda: calls.append("stop"),
                                       play=lambda *_args: calls.append("play"))
    controller._actions([Action("play", event)])
    calls.clear()
    controller._actions([Action("stop", reason="正式交战开始", when_live=True)])
    assert calls == (["stop"] if stop else [])
    assert controller.playing_event == ("" if stop else event)
    # Forced cancellation still wins, irrespective of the live policy.
    controller._actions([Action("stop", reason="GSI 超时")])
    assert calls[-1] == "stop" and controller.playing_event == ""


def test_preview_uses_its_own_stop_policy_and_new_event_replaces_it(tmp_path):
    path = tmp_path / "song.wav"
    path.touch()
    settings = Settings(ptt_verified=True)
    for event in ("start", "mvp"):
        settings.tracks[event].path = str(path)
    settings.tracks["start"].stop_on_live = False
    controller = Controller(settings)
    controller.audio.close()
    calls = []
    controller.audio = SimpleNamespace(stop_music=lambda: calls.append("stop"),
                                       play=lambda *_args: calls.append("play"))
    controller.running = True
    controller.preview("start", False)
    controller._actions([Action("stop", when_live=True)])
    assert calls == ["play"]
    controller._actions([Action("play", "mvp")])
    assert calls[-2:] == ["stop", "play"] and controller.playing_event == "mvp"
    controller._actions([Action("stop", when_live=True)])
    assert calls[-1] == "stop" and controller.playing_event == ""


def test_event_state_resets_at_live_even_when_previous_mvp_can_continue():
    def state(phase, number, mvps=0, winner=None):
        return {"provider": {"steamid": "self"},
                "map": {"name": "de_test", "phase": "live", "round": number,
                        "team_ct": {"score": number}, "team_t": {"score": 0}},
                "player": {"steamid": "self", "team": "CT", "match_stats": {"mvps": mvps}},
                "round": {"phase": phase, "win_team": winner}}
    settings = Settings()
    settings.tracks["mvp"].stop_on_live = False
    engine = EventEngine(settings)
    engine.ingest(state("live", 0), 0)
    assert any(a.event == "mvp" for a in engine.ingest(state("over", 0, 1, "CT"), 1))
    engine.ingest(state("freezetime", 1, 1), 2)
    actions = engine.ingest(state("live", 1, 1), 3)
    assert any(a.kind == "stop" and a.when_live for a in actions)
    engine.ingest(state("over", 1, 1, "CT"), 4)
    assert any(a.event == "victory" for a in engine.tick(5.5))
    assert any(not a.when_live for a in engine.tick(10) if a.kind == "stop")


def test_event_gain_scales_both_routes_live_edit_and_outgoing_tail():
    settings = Settings()
    settings.tracks["victory"].gain = .5
    engine = AudioEngine(settings)
    try:
        engine.event = "victory"
        engine._event_gain = .5
        engine._pcm = np.full((FRAME * 12, 2), .4, np.float32)
        engine.send_music = True
        head, shared = engine._render_music()
        np.testing.assert_allclose(head, .2)
        np.testing.assert_allclose(shared, .2)
        settings.tracks["victory"].gain = .25
        head, shared = engine._render_music()
        assert head[0, 0] == pytest.approx(.2) and head[-1, 0] == pytest.approx(.1)
        np.testing.assert_allclose(head, shared)
        engine.stop_music()
        settings.tracks["mvp"].gain = 2
        engine.event = "mvp"
        engine._event_gain = 2
        engine._pcm = np.full((FRAME * 2, 2), .1, np.float32)
        engine.send_music = False
        head, shared = engine._render_music()
        # Old transmitted tail retains .25; new headphone-only music has its own gain.
        assert shared[0, 0] == pytest.approx(.1)
        assert head[0, 0] == pytest.approx(.3)
        engine.stop_music()
    finally:
        engine.close()


def test_zero_event_gain_and_natural_end():
    settings = Settings()
    settings.tracks["side"].gain = 0
    engine = AudioEngine(settings)
    try:
        engine.event = "side"
        engine._event_gain = 0
        engine._pcm = np.ones((100, 2), np.float32)
        engine.send_music = True
        head, shared = engine._render_music()
        assert not head.any() and not shared.any()
        assert not engine.music_active and not engine.transmitting
    finally:
        engine.close()
