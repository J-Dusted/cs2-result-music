# One-folder distribution keeps Qt, libsndfile, PortAudio and RNNoise replaceable.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / 'assets/gamestate_integration_music.cfg'), 'assets')]
datas += [(str(root / 'assets/rnnoise'), 'assets/rnnoise')]
datas += copy_metadata('sounddevice') + copy_metadata('soundfile')
binaries = collect_dynamic_libs('_sounddevice_data') + collect_dynamic_libs('_soundfile_data')
a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=['_cffi_backend'],
             excludes=['matplotlib', 'pandas', 'PySide6.QtWebEngineCore', 'PySide6.QtQml',
                       'PySide6.QtQuick', 'PySide6.QtNetwork', 'PySide6.QtPdf', 'pytest'],
             noarchive=False)
# Qt 6.12 uses Windows' unversioned ICU shim. A PATH-provided Poppler ICU DLL
# has versioned exports and breaks Qt with WinError 127. Use the OS shim.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CS2Music',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CS2Music')
