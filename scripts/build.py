from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
import os
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    sys.path.insert(0, str(ROOT))
    from cs2music import __version__
    # PyInstaller replaces its prior output; validate these computed paths first.
    for output in (ROOT / "dist/CS2Music", ROOT / "build/CS2Music", ROOT / "runtime/build-check"):
        if not output.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError(f"Build output escapes workspace: {output}")
    if not (ROOT / "assets/rnnoise/rnnoise.dll").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts/vendor_rnnoise.py")], check=True)
    if "--package-only" not in sys.argv:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "CS2Music.spec")],
                       cwd=ROOT, check=True)
    destination = ROOT / "dist/CS2Music"
    # Reject a distribution with missing native libraries or broken Qt startup.
    check_env = os.environ.copy()
    check_env["CS2MUSIC_DATA_DIR"] = str(ROOT / "runtime/build-check")
    for flag in ("--self-check", "--gui-check"):
        subprocess.run([str(destination / "CS2Music.exe"), flag], env=check_env,
                       cwd=ROOT, timeout=20, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    for name in ("README.md", "THIRD_PARTY_NOTICES.md", "requirements-lock.txt", "VALIDATION.md"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, destination / name)
    for source in (ROOT / "assets/rnnoise").iterdir():
        if source.suffix.lower() in (".txt", ".json"):
            target = destination / "licenses/rnnoise" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Preserve installed distributions' license files next to the replaceable shared libraries.
    used = {line.split("==")[0].lower().replace("-", "_") for line in
            (ROOT / "requirements-lock.txt").read_text().splitlines() if "==" in line}
    for distribution in importlib.metadata.distributions():
        if distribution.metadata["Name"].lower().replace("-", "_") not in used:
            continue
        for file in distribution.files or []:
            if any("license" in part.lower() or "copying" in part.lower() for part in file.parts):
                source = distribution.locate_file(file)
                if source.is_file():
                    target = destination / "licenses" / distribution.metadata["Name"] / str(file)
                    if not target.resolve().is_relative_to((destination / "licenses").resolve()):
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, destination / "licenses/PYTHON-LICENSE.txt")
    shutil.copy2(ROOT / "assets/gamestate_integration_music.cfg", destination)
    destination.joinpath("run-self-check.cmd").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\nstart /wait "" CS2Music.exe --self-check\r\n'
        'echo Report: %LOCALAPPDATA%\\CS2Music\\self-check.json\r\npause\r\n', encoding="ascii")
    source = destination / "source"
    source.mkdir(parents=True, exist_ok=True)
    for name in ("main.py", "CS2Music.spec", "requirements.txt", "requirements-dev.txt",
                 "requirements-lock.txt", "setup.ps1", "build.ps1", "start.cmd", ".gitignore",
                 "README.md", "THIRD_PARTY_NOTICES.md", "VALIDATION.md"):
        shutil.copy2(ROOT / name, source / name)
    for name in ("cs2music", "scripts", "tests"):
        shutil.copytree(ROOT / name, source / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (source / "assets/rnnoise").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "assets/gamestate_integration_music.cfg", source / "assets")
    for file in (ROOT / "assets/rnnoise").iterdir():
        if file.suffix.lower() in (".txt", ".json"):
            shutil.copy2(file, source / "assets/rnnoise" / file.name)
    archive = shutil.make_archive(str(ROOT / f"dist/CS2Music-{__version__}-Windows-x64"), "zip", ROOT / "dist", "CS2Music")
    with Path(archive).open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    Path(archive).with_suffix(".sha256").write_text(f"{digest}  {Path(archive).name}\n", encoding="ascii")
    print(f"Built {archive}")


if __name__ == "__main__":
    main()
