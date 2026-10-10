from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .hotkeys import parse_hotkey

EVENT_LABELS = {
    "victory": "回合胜利", "defeat": "回合失败", "mvp": "本人 MVP",
    "bomb": "炸弹最后十秒", "start": "热身后开局", "side": "换边后开局",
}


def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / name


def data_dir() -> Path:
    if os.getenv("CS2MUSIC_DATA_DIR"):
        return Path(os.environ["CS2MUSIC_DATA_DIR"])
    if getattr(sys, "frozen", False):
        return Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "CS2Music"
    return Path(__file__).resolve().parent.parent / "runtime"


@dataclass
class Track:
    enabled: bool = True
    path: str = ""


@dataclass
class Settings:
    version: int = 2
    tracks: dict[str, Track] = field(default_factory=lambda: {k: Track() for k in EVENT_LABELS})
    mic_device: str = ""
    headphone_device: str = ""
    cable_device: str = ""
    headphone_gain: float = 0.35
    music_gain: float = 0.35
    mic_gain: float = 1.0
    denoise: bool = True
    voice_threshold_db: float = -42.0
    vad_threshold: float = 0.55
    duck_db: float = -12.0
    ptt_key: str = "F13"
    ptt_verified: bool = False
    bomb_seconds: float = 40.0
    bomb_compensation: float = 0.0
    half_rounds: int = 12
    overtime_half_rounds: int = 3
    game_dir: str = ""

    def validate(self) -> None:
        if self.version != 2:
            raise ValueError("配置文件版本不受支持")
        self.ptt_key = parse_hotkey(self.ptt_key)[0]
        for track in self.tracks.values():
            if not isinstance(track, Track) or not isinstance(track.path, str) or not isinstance(track.enabled, bool):
                raise ValueError("音乐文件设置无效")
        for name, low, high in (
            ("headphone_gain", 0, 2), ("music_gain", 0, 2), ("mic_gain", 0, 4),
            ("voice_threshold_db", -80, -5), ("vad_threshold", 0, 1),
            ("duck_db", -36, 0), ("bomb_seconds", 15, 120), ("bomb_compensation", -10, 10),
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"无效设置：{name}")
        if not 1 <= self.half_rounds <= 30 or not 1 <= self.overtime_half_rounds <= 15:
            raise ValueError("半场回合数超出范围")

    def save(self, path: Path | None = None) -> None:
        self.validate()
        path = path or data_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or data_dir() / "settings.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version", 1) == 1:
            raw["version"] = 2
        tracks = raw.pop("tracks", {})
        s = cls(**raw)
        s.tracks = {k: Track(**tracks.get(k, {})) for k in EVENT_LABELS}
        s.validate()
        return s
