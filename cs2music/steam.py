from __future__ import annotations

import os
import re
from pathlib import Path

from .config import resource_path


def find_game_dirs() -> list[Path]:
    roots = [Path(os.getenv("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam"]
    if os.name == "nt":
        import winreg
        for hive, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
            except OSError:
                pass
    libraries = list(roots)
    for root in roots:
        manifest = root / "steamapps/libraryfolders.vdf"
        if manifest.exists():
            text = manifest.read_text(encoding="utf-8", errors="replace")
            for value in re.findall(r'"path"\s+"([^"\n]+)"', text):
                libraries.append(Path(value.replace("\\\\", "\\")))
    found = []
    for library in libraries:
        game = library / "steamapps/common/Counter-Strike Global Offensive"
        if (game / "game/csgo/cfg").is_dir() and game not in found:
            found.append(game)
    return found


def cfg_dir(path: Path) -> Path:
    for candidate in (path / "game/csgo/cfg", path / "csgo/cfg", path):
        if candidate.is_dir() and candidate.name.lower() == "cfg":
            return candidate
    raise ValueError("请选择 CS2 游戏根目录，或 game/csgo/cfg 文件夹")


def install_cfg(path: Path) -> Path:
    destination = cfg_dir(path) / "gamestate_integration_music.cfg"
    template = resource_path("assets/gamestate_integration_music.cfg").read_bytes()
    if destination.exists():
        if destination.read_bytes() == template:
            return destination
        raise FileExistsError("已有同名 GSI 配置且内容不同。请先备份或改名，再安装。")
    # Exclusive creation avoids overwriting a file that appeared since the check.
    with destination.open("xb") as file:
        file.write(template)
    return destination
