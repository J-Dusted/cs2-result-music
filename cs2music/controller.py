from __future__ import annotations

import queue
import time
from collections import deque

from .audio import AudioEngine
from .config import EVENT_LABELS, Settings
from .events import EventEngine
from .gsi import GSIServer
from .ptt import PTTController


class Controller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.audio = AudioEngine(settings)
        self.events = EventEngine(settings)
        self.server: GSIServer | None = None
        self.ptt: PTTController | None = None
        self.logs: deque[str] = deque(maxlen=200)
        self.test_start = self.test_end = 0.0
        self.running = False
        self.fault = ""

    def log(self, message: str):
        self.logs.append(f"{time.strftime('%H:%M:%S')}  {message}")

    def start(self):
        if self.running:
            return
        self.settings.validate()
        try:
            self.audio.start()
            self.ptt = PTTController(self.settings.ptt_key)
            self.server = GSIServer()
            self.server.start()
            self.events = EventEngine(self.settings)
            self.running = True
            self.fault = ""
            self.log("监听已启动：127.0.0.1:8765/gsi")
            if not self.settings.ptt_verified:
                self.log("TS 热键尚未确认；自动发送暂停。请完成热键测试。")
        except Exception:
            self.stop()
            raise

    def tick(self):
        if not self.running:
            return
        now = time.monotonic()
        try:
            if not self.audio.healthy():
                raise RuntimeError(self.audio.error or "音频设备断开或流已停止；请重新选择设备后启动")
            while True:
                try:
                    received, message = self.server.messages.get_nowait()
                except queue.Empty:
                    break
                # Do not replay queued states after a paused UI or debugger.
                if now - received <= self.events.STALE_SECONDS:
                    self._actions(self.events.ingest(message, received))
            self._actions(self.events.tick(now))
            testing = self.test_start <= now < self.test_end
            active = testing or (self.settings.ptt_verified and self.audio.transmitting)
            self.ptt.update(active, now)
            if self.audio.error:
                self.log(self.audio.error)
                self.audio.error = ""
        except Exception as exc:
            self.fault = str(exc)
            self.log(f"运行已停止：{exc}")
            self.stop()

    def _actions(self, actions):
        for action in actions:
            if action.kind == "stop":
                self.audio.stop_music()
                self.log(action.reason)
            else:
                track = self.settings.tracks[action.event]
                # A new phase cancels any old track even when no new file is assigned.
                self.audio.stop_music()
                if track.enabled and track.path:
                    try:
                        self.audio.play(track.path, action.event, transmit=True)
                        self.log(f"触发：{EVENT_LABELS[action.event]}")
                    except Exception as exc:
                        self.log(str(exc))
                else:
                    self.log(f"触发：{EVENT_LABELS[action.event]}（未启用或未设置文件）")

    def preview(self, event: str, transmit: bool):
        if not self.running:
            raise RuntimeError("请先选择设备并启动监听")
        if transmit and not self.settings.ptt_verified:
            raise RuntimeError("请先完成 TS 热键测试并确认朋友端收到声音")
        self.audio.play(self.settings.tracks[event].path, event, transmit)
        self.log(f"{'双路测试' if transmit else '耳机试听'}：{EVENT_LABELS[event]}")

    def test_ptt(self):
        if not self.running:
            raise RuntimeError("请先启动监听")
        self.test_start = time.monotonic() + 5
        self.test_end = self.test_start + 6
        self.log("热键测试：5 秒后保持通话键 6 秒。请切到 TS 或 CS2 观察发送状态。")

    def stop_music(self):
        self.audio.stop_music()
        self.events.active_event = ""
        self.test_start = self.test_end = 0
        self.log("音乐已停止")

    def stop(self):
        self.running = False
        self.test_start = self.test_end = 0
        # Stop audio first so no samples continue after PTT is released.
        self.audio.stop()
        if self.ptt:
            try:
                self.ptt.close()
            except OSError as exc:
                self.log(f"释放通话键失败：{exc}（独立看护将重试）")
            self.ptt = None
        if self.server:
            self.server.close()
            self.server = None

    def close(self):
        self.stop()
        self.audio.close()
