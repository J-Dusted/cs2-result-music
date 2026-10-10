import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from cs2music.config import Settings
from cs2music.gui import Window


def test_gui_controls_and_ptt_verification_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2MUSIC_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    settings = Settings(ptt_verified=True)
    window = Window(settings)
    assert len(window.rows) == 6
    window.ptt_key.setCurrentText("F14")
    assert not settings.ptt_verified and not window.verified.isChecked()
    window.spins["music_gain"].setValue(70)
    assert settings.music_gain == .7
    window.close()
