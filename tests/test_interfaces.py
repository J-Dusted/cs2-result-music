import http.client
import json
import time
import subprocess
import sys
from pathlib import Path

import pytest

from cs2music.config import Settings
from cs2music.gsi import GSIServer
from cs2music.ptt import PTTController
from cs2music.steam import install_cfg


def test_http_real_post_validation_and_queue():
    server = GSIServer(port=0)
    server.start()
    port = server.server.server_port
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request("POST", "/gsi", json.dumps({"provider": {"steamid": "self"}}))
        assert connection.getresponse().status == 204
        assert server.messages.get(timeout=1)[1]["provider"]["steamid"] == "self"
        connection.request("POST", "/wrong", "{}")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        connection.request("POST", "/gsi", '{"player":{"match_stats":[]}}')
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        connection.request("POST", "/gsi", "[]")
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        connection.close()
    finally:
        server.close()


def test_settings_atomic_roundtrip_validation_and_unknown_version(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.tracks["mvp"].path = "中文/music.mp3"
    settings.save(path)
    assert Settings.load(path).tracks["mvp"].path == "中文/music.mp3"
    assert not path.with_suffix(".tmp").exists()
    settings.music_gain = float("nan")
    with pytest.raises(ValueError):
        settings.save(path)
    assert Settings.load(path).music_gain == .35
    path.write_text('{"version":9}', encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.load(path)


def test_ptt_silence_gaps_delay_journal_intent_and_cleanup(tmp_path):
    calls = []
    controller = PTTController("F13", send=lambda vk, down: calls.append((vk, down)), watch=False)
    controller.file = tmp_path / "journal.json"
    controller.update(True, 100)
    assert controller.held and len(calls) == 1
    assert json.loads(controller.file.read_text())["held"]
    controller.update(True, 101)
    controller.update(False, 101.2)
    assert controller.held
    controller.update(False, 101.31)
    assert not controller.held and len(calls) == 2
    controller.update(True, 102)
    controller.close()
    assert calls[-1][1] is False
    assert json.loads(controller.file.read_text())["closed"]


def test_ptt_intent_exists_before_press(tmp_path):
    path = tmp_path / "journal.json"
    def press(_vk, down):
        if down:
            assert json.loads(path.read_text())["held"] is True
    controller = PTTController("F13", send=press, watch=False)
    controller.file = path
    controller.update(True, 1)
    controller.close()


def test_gsi_install_is_idempotent_and_never_overwrites(tmp_path):
    directory = tmp_path / "game/csgo/cfg"
    directory.mkdir(parents=True)
    path = install_cfg(tmp_path)
    assert install_cfg(tmp_path) == path
    path.write_text("existing user configuration", encoding="utf-8")
    with pytest.raises(FileExistsError):
        install_cfg(tmp_path)
    assert path.read_text() == "existing user configuration"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_independent_watchdog_releases_after_parent_crash(tmp_path):
    state, output = tmp_path / "watch.json", tmp_path / "released.json"
    parent = subprocess.Popen([sys.executable, str(Path(__file__).with_name("watchdog_fixture.py")),
                               "parent", str(state), str(output)], stdout=subprocess.PIPE,
                              text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert parent.stdout.readline().strip() == "ready"
        parent.terminate()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 5
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert json.loads(output.read_text()) == {"vk": 124, "down": False}
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SendInput ABI")
def test_sendinput_abi_without_touching_keyboard(monkeypatch):
    from cs2music.ptt import send_key
    calls = []
    class Function:
        result = 1
        def __call__(self, count, pointer, size):
            packet = pointer._obj
            calls.append((count, size, packet.type, packet.u.ki.vk, packet.u.ki.flags))
            return self.result
    function = Function()
    class User32:
        SendInput = function
    monkeypatch.setattr("cs2music.ptt.ctypes.WinDLL", lambda *_args, **_kwargs: User32())
    send_key(124, True)
    send_key(124, False)
    assert calls == [(1, 40, 1, 124, 0), (1, 40, 1, 124, 2)]
    function.result = 0
    with pytest.raises(OSError):
        send_key(124, True)
