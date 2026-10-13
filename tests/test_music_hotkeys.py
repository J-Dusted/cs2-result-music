import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs2music.config import Settings
from cs2music.controller import Controller
from cs2music.hotkeys import parse_hotkey
from cs2music.music import choose_track
from cs2music.ptt import PTTController


def test_folder_selection_filters_and_observes_changes(tmp_path):
    (tmp_path / "中文.MP3").touch()
    (tmp_path / "quiet.flac").touch()
    (tmp_path / "readme.txt").touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/ignored.wav").touch()
    first = choose_track(str(tmp_path))
    second = choose_track(str(tmp_path), first)
    assert {Path(first).name, Path(second).name} == {"中文.MP3", "quiet.flac"}
    Path(first).unlink()
    assert choose_track(str(tmp_path), second) == second
    Path(second).unlink()
    with pytest.raises(ValueError, match="没有 WAV"):
        choose_track(str(tmp_path))


def test_random_picker_receives_all_candidates(monkeypatch, tmp_path):
    for name in ("a.wav", "b.ogg", "c.mp3"):
        (tmp_path / name).touch()
    options = []
    def pick(candidates):
        options.extend(candidates)
        return candidates[-1]
    monkeypatch.setattr("cs2music.music.random.choice", pick)
    assert Path(choose_track(str(tmp_path))).name == "c.mp3"
    assert len(options) == 3


def test_legacy_file_and_missing_folder(tmp_path):
    path = tmp_path / "one.wav"
    path.touch()
    assert choose_track(str(path)) == str(path.resolve())
    assert choose_track(str(tmp_path), str(path.resolve())) == str(path.resolve())
    with pytest.raises(ValueError):
        choose_track("")
    with pytest.raises(FileNotFoundError):
        choose_track(str(tmp_path / "missing"))


def test_events_and_previews_use_random_pick_and_route(tmp_path):
    for name in ("a.wav", "b.wav"):
        (tmp_path / name).touch()
    settings = Settings(ptt_verified=True)
    settings.tracks["victory"].path = str(tmp_path)
    controller = Controller(settings)
    controller.audio.close()  # No real audio streams are started.
    played = []
    controller.audio = SimpleNamespace(stop_music=lambda: None,
                                       play=lambda *args: played.append(args))
    controller.running = True
    controller._actions([SimpleNamespace(kind="play", event="victory")])
    controller.preview("victory", False)
    controller.preview("victory", True)
    assert [args[2] for args in played] == [True, False, True]
    assert played[0][0] != played[1][0] and played[1][0] != played[2][0]
    assert all(Path(args[0]).parent == tmp_path for args in played)


@pytest.mark.parametrize("text,normal,codes", [
    ("k", "K", (75,)), ("5", "5", (53,)), ("Space", "Space", (32,)),
    ("alt+control+k", "Ctrl+Alt+K", (17, 18, 75)),
    ("Meta+Shift+Left", "Shift+Win+Left", (16, 91, 37)),
    ("Ctrl++", "Ctrl+Shift+=", (17, 16, 187)),
    ("Ctrl+,", "Ctrl+,", (17, 188)), ("F24", "F24", (135,)),
    ("Ctrl+Shift+!", "Ctrl+Shift+1", (17, 16, 49)),
])
def test_hotkey_mapping(text, normal, codes):
    assert parse_hotkey(text) == (normal, codes)


@pytest.mark.parametrize("text", ["", "Ctrl", "Ctrl+Ctrl+K", "K+L", "F25", "Ctrl+K, Ctrl+L", "鼠标侧键"])
def test_reject_invalid_or_multiple_chords(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_settings_migrate_legacy_and_roundtrip_combination(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 1, "ptt_key": "F13", "ptt_verified": True,
                                "tracks": {"victory": {"path": "old/song.mp3"}}}))
    settings = Settings.load(path)
    assert settings.version == 3 and settings.ptt_verified
    assert settings.tracks["victory"].path == "old/song.mp3"
    settings.ptt_key = "Alt+Ctrl+K"
    settings.tracks["mvp"].path = "中文/音乐目录"
    settings.save(path)
    loaded = Settings.load(path)
    assert loaded.ptt_key == "Ctrl+Alt+K"
    assert loaded.tracks["mvp"].path == "中文/音乐目录"


def test_combo_journal_delay_reverse_release(tmp_path):
    calls = []
    path = tmp_path / "ptt.json"
    def send(vk, down):
        if down:
            assert json.loads(path.read_text())["keys"] == [17, 18, 75]
        calls.append((vk, down))
    controller = PTTController("Ctrl+Alt+K", send=send, watch=False)
    controller.file = path
    controller.update(True, 1)
    controller.update(True, 2)
    controller.update(False, 2.2)
    assert calls == [(17, True), (18, True), (75, True)]
    controller.update(False, 2.31)
    assert calls[-3:] == [(75, False), (18, False), (17, False)]
    assert json.loads(path.read_text())["keys"] == []
    controller.close()


def test_partial_press_failure_releases_modifiers(tmp_path):
    calls = []
    def send(vk, down):
        calls.append((vk, down))
        if vk == 75 and down:
            raise OSError("failed")
    controller = PTTController("Ctrl+Alt+K", send=send, watch=False)
    controller.file = tmp_path / "ptt.json"
    with pytest.raises(OSError):
        controller.update(True, 1)
    assert not controller.held
    assert calls[-3:] == [(75, False), (18, False), (17, False)]
    controller.close()


def test_release_failure_keeps_failed_key_for_watchdog(tmp_path):
    calls = []
    def send(vk, down):
        calls.append((vk, down))
        if vk == 75 and not down:
            raise OSError("failed release")
    controller = PTTController("Ctrl+Alt+K", send=send, watch=False)
    controller.file = tmp_path / "ptt.json"
    controller.update(True, 1)
    with pytest.raises(OSError):
        controller.close()
    journal = json.loads(controller.file.read_text())
    assert journal["held"] and journal["closed"] and journal["keys"] == [75]
    assert calls[-2:] == [(18, False), (17, False)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_watchdog_releases_complete_combo_after_crash(tmp_path):
    state, output = tmp_path / "watch.json", tmp_path / "released.json"
    fixture = Path(__file__).with_name("watchdog_fixture.py")
    parent = subprocess.Popen([sys.executable, str(fixture), "parent", str(state), str(output), "Ctrl+Alt+K"],
                              stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert parent.stdout.readline().strip() == "ready"
        parent.terminate()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 5
        while state.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert json.loads(output.read_text()) == [{"vk": 75, "down": False},
                                                  {"vk": 18, "down": False}, {"vk": 17, "down": False}]
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=3)
