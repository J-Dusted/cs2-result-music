"""Pick one current file per event, outside the audio callback."""
from __future__ import annotations

import random
from pathlib import Path

EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}


def choose_track(location: str, previous: str = "") -> str:
    if not location.strip():
        raise ValueError("请先选择音乐文件夹")
    path = Path(location).expanduser()
    if path.is_dir():
        candidates = sorted((item.resolve() for item in path.iterdir()
                             if item.is_file() and item.suffix.lower() in EXTENSIONS), key=str)
    elif path.is_file() and path.suffix.lower() in EXTENSIONS:
        candidates = [path.resolve()]  # Preserve single-file settings from version 1.
    else:
        raise FileNotFoundError(f"音乐文件夹或文件不存在／格式不支持：{location}")
    if not candidates:
        raise ValueError(f"文件夹中没有 WAV / MP3 / FLAC / OGG 音乐（只读取当前层）：{location}")
    if len(candidates) > 1:
        candidates = [item for item in candidates if str(item) != previous]
    return str(random.choice(candidates))
