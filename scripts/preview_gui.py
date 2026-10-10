"""Render the actual Qt widgets offscreen without starting any audio or hotkeys."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["CS2MUSIC_DATA_DIR"] = str(ROOT / "runtime/gui-preview")
from PySide6.QtWidgets import QApplication, QTabWidget
from PySide6.QtGui import QFontDatabase
from cs2music.config import Settings
from cs2music.gui import Window

app = QApplication([])
# The offscreen plugin does not enumerate Windows system fonts.
QFontDatabase.addApplicationFont(str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"))
window = Window(Settings())
window.show()
tabs = window.findChild(QTabWidget)
destination = ROOT / "runtime/preview"
destination.mkdir(parents=True, exist_ok=True)
for index in range(tabs.count()):
    tabs.setCurrentIndex(index)
    app.processEvents()
    window.grab().save(str(destination / f"tab-{index}.png"))
window.close()
print(destination)
