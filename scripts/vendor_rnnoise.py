"""Vendor only the Windows DLL, which includes RNNoise's default trained model.

Usage: python scripts/vendor_rnnoise.py [path/to/pyrnnoise-0.3.5-win_amd64.whl]
The wrapper's Python dependencies (PyAV/matplotlib) are deliberately not imported.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "pyrnnoise-0.3.5-py3-none-win_amd64.whl"
SHA256 = "0bba4b8db29cb8d7aa9ac287dad798c05d7c658d9eccbe66a21d5aaaf2b3854a"


def main():
    wheel = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build/rnnoise" / NAME
    if not wheel.exists():
        with urllib.request.urlopen("https://pypi.org/pypi/pyrnnoise/0.3.5/json", timeout=30) as response:
            meta = json.load(response)
        item = next(item for item in meta["urls"] if item["filename"] == NAME)
        if item["digests"]["sha256"] != SHA256:
            raise ValueError("Upstream wheel checksum changed")
        wheel.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(item["url"], wheel)
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != SHA256:
        raise ValueError("RNNoise wheel SHA256 mismatch")
    destination = ROOT / "assets/rnnoise"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        destination.joinpath("rnnoise.dll").write_bytes(
            archive.read("pyrnnoise-0.3.5.data/purelib/pyrnnoise/rnnoise.dll"))
        destination.joinpath("PYRNNOISE-LICENSE.txt").write_bytes(
            archive.read("pyrnnoise-0.3.5.dist-info/licenses/LICENSE"))
    destination.joinpath("ORIGIN.json").write_text(json.dumps({
        "package": "pyrnnoise", "version": "0.3.5", "wheel_sha256": SHA256,
        "source": "https://github.com/pengzhendong/pyrnnoise",
        "rnnoise_source": "https://github.com/xiph/rnnoise",
        "model": "default model compiled into DLL; rnnoise_create(NULL)",
    }, indent=2), encoding="utf-8")
    print(f"Vendored RNNoise DLL and embedded model: {destination}")


if __name__ == "__main__":
    main()
