from __future__ import annotations

import json
import platform
import os
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import RNNoise, devices
from .config import data_dir, resource_path


def self_check() -> int:
    report = {"platform": platform.platform(), "errors": [], "real_ts_verified": False}
    try:
        noise = RNNoise()
        output, probability = noise.process(np.zeros(480, np.float32))
        report["rnnoise"] = {"finite": bool(np.isfinite(output).all()), "probability": probability,
                             "model": "embedded default model"}
        noise.close()
        if not report["rnnoise"]["finite"]:
            raise RuntimeError("RNNoise returned non-finite samples")
    except Exception as exc:
        report["errors"].append(f"RNNoise: {exc}")
    try:
        report["devices"] = [d.__dict__ for d in devices()]
        report["virtual_cable_available"] = any("CABLE INPUT" in d["label"].upper() for d in report["devices"])
    except Exception as exc:
        report["errors"].append(f"audio enumeration: {exc}")
    report["formats"] = {k: v for k, v in sf.available_formats().items() if k in ("WAV", "FLAC", "OGG", "MP3")}
    report["gsi_template"] = resource_path("assets/gamestate_integration_music.cfg").is_file()
    if not report["gsi_template"]:
        report["errors"].append("GSI template missing")
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "self-check.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


def gui_check() -> int:
    """Bundle smoke test with actual Qt widgets; no audio or key transmission."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QTabWidget
    from PySide6.QtGui import QFontDatabase
    from .gui import Window
    from .config import Settings
    app = QApplication([])
    QFontDatabase.addApplicationFont(str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"))
    window = Window(Settings())
    window.show()
    tabs = window.findChild(QTabWidget)
    directory = data_dir() / "gui-check"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(tabs.count()):
        tabs.setCurrentIndex(index)
        app.processEvents()
        if not window.grab().save(str(directory / f"tab-{index}.png")):
            raise RuntimeError("Qt render failed")
    window.close()
    return 0
