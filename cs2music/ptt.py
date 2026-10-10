"""Windows global PTT with an independent crash/heartbeat watchdog."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import data_dir


KEYS = {f"F{i}": 0x6F + i for i in range(1, 25)}


def send_key(vk: int, down: bool):
    if os.name != "nt":
        raise OSError("自动按键通话仅支持 Windows")
    from ctypes import wintypes

    class Keyboard(ctypes.Structure):
        _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

    class Mouse(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

    class Hardware(ctypes.Structure):
        _fields_ = [("msg", wintypes.DWORD), ("low", wintypes.WORD), ("high", wintypes.WORD)]

    class Payload(ctypes.Union):
        _fields_ = [("ki", Keyboard), ("mi", Mouse), ("hi", Hardware)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", Payload)]

    packet = Input(1, Payload(ki=Keyboard(vk, 0, 0 if down else 2, 0, 0)))
    api = ctypes.WinDLL("user32", use_last_error=True).SendInput
    api.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    api.restype = wintypes.UINT
    if api(1, ctypes.byref(packet), ctypes.sizeof(Input)) != 1:
        raise OSError("SendInput 失败；请检查 TS 与程序的权限级别")


def watchdog(parent_pid: int, state_file: Path, release=send_key):
    """The child observes a shared journal and only releases keys owned by the parent."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, parent_pid)
    last = {}
    try:
        while True:
            try:
                last = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            dead = not handle or kernel.WaitForSingleObject(handle, 0) != 258
            stale = last and time.time() - last.get("heartbeat", 0) > 3
            closing = last.get("closed", False)
            if dead or stale or closing:
                # A final read closes the race where the parent journals a key-down
                # and dies between our earlier read and the process-handle check.
                try:
                    last = json.loads(state_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                if last.get("held"):
                    try:
                        release(int(last["vk"]), False)
                    except OSError:
                        pass
                # A stale parent is disarmed; parent detects the missing watchdog and stops.
                break
            time.sleep(0.1)
    finally:
        if handle:
            kernel.CloseHandle(handle)
        state_file.unlink(missing_ok=True)


class PTTController:
    def __init__(self, key: str, send=send_key, watch: bool = True):
        if key not in KEYS:
            raise ValueError("通话键必须是 F1–F24")
        self.vk = KEYS[key]
        self.send = send
        self.held = False
        self.last_activity = 0.0
        self.process: subprocess.Popen | None = None
        self.file: Path | None = None
        self.last_journal = 0.0
        self.closed = False
        if watch:
            directory = data_dir()
            directory.mkdir(parents=True, exist_ok=True)
            self.file = directory / f"ptt-{os.getpid()}-{time.time_ns()}.json"
            self._journal()
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--watchdog", str(os.getpid()), str(self.file)]
            else:
                command = [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"),
                           "--watchdog", str(os.getpid()), str(self.file)]
            self.process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW,
                                            cwd=Path(__file__).resolve().parent.parent)

    def _journal(self, held: bool | None = None):
        if self.file:
            payload = {"vk": self.vk, "held": self.held if held is None else held,
                       "closed": self.closed, "heartbeat": time.time()}
            temp = self.file.with_suffix(".tmp")
            temp.write_text(json.dumps(payload), encoding="utf-8")
            for attempt in range(4):
                try:
                    temp.replace(self.file)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    # Windows may briefly deny renaming while the reader holds its handle.
                    time.sleep(0.002)
            self.last_journal = time.monotonic()

    def update(self, active: bool, now: float):
        if self.closed:
            return
        if self.process and self.process.poll() is not None:
            self.release()
            raise OSError("通话键看护进程已退出，音频发送已停止。请重新启动监听。")
        if active:
            self.last_activity = now
            if not self.held:
                # Write intent BEFORE pressing, so a crash between operations is safe.
                self._journal(held=True)
                self.send(self.vk, True)
                self.held = True
        elif self.held and now - self.last_activity >= 0.3:
            self.release()
        if now - self.last_journal >= 0.25:
            self._journal()

    def release(self):
        if self.held:
            self.send(self.vk, False)
            self.held = False
            self._journal()

    def close(self):
        if self.closed:
            return
        try:
            self.release()
        finally:
            self.closed = True
            self._journal()
            if self.process:
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # Leave the independent watchdog alive to release the journalled key.
                    pass
