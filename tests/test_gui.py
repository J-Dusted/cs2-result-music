import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QKeySequence
from cs2music.config import Settings
from cs2music.gui import Window


def test_gui_controls_and_ptt_verification_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2MUSIC_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    settings = Settings(ptt_verified=True)
    window = Window(settings)
    assert len(window.rows) == 6
    window.ptt_key.setKeySequence(QKeySequence("Ctrl+Alt+K"))
    assert not settings.ptt_verified and not window.verified.isChecked()
    window.collect()
    assert settings.ptt_key == "Ctrl+Alt+K"
    monkeypatch.setattr("cs2music.gui.QFileDialog.getExistingDirectory", lambda *_args: str(tmp_path))
    window.browse("victory")
    assert settings.tracks["victory"].path == str(tmp_path)
    window.spins["music_gain"].setValue(70)
    assert settings.music_gain == .7
    window.close()


def test_gui_cleared_key_does_not_overwrite_valid_saved_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2MUSIC_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.save()
    window = Window(settings)
    window.ptt_key.clear()
    window.close()
    assert Settings.load().ptt_key == "F13"
