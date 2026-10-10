# Third-party components

This distribution dynamically loads the following components. Their original
license texts are included under `licenses/` in the Windows distribution.
Libraries are shipped separately in `_internal/` and can be replaced with
ABI-compatible builds. No third-party music or virtual audio driver is bundled.

| Component | Origin | License |
| --- | --- | --- |
| PySide6 / Qt Essentials | https://www.qt.io/qt-for-python | LGPL-3.0 / Qt module-specific licenses |
| NumPy | https://github.com/numpy/numpy | BSD-3-Clause |
| SciPy and its numerical libraries | https://github.com/scipy/scipy | BSD and bundled library licenses |
| sounddevice / PortAudio | https://github.com/spatialaudio/python-sounddevice | MIT |
| soundfile / libsndfile | https://github.com/bastibe/python-soundfile | BSD / LGPL-2.1 |
| RNNoise | https://github.com/xiph/rnnoise | BSD-3-Clause |
| RNNoise Windows binary from pyrnnoise 0.3.5 | https://github.com/pengzhendong/pyrnnoise | Apache-2.0 wrapper, RNNoise BSD-3-Clause |
| Python / CFFI | https://www.python.org / https://cffi.readthedocs.io | PSF / MIT |
| PyInstaller runtime | https://pyinstaller.org | GPL with bootloader distribution exception |

The RNNoise binary is extracted unchanged from a pinned Windows wheel; its
default model is compiled into the DLL. See `assets/rnnoise/ORIGIN.json` for the
wheel hash and provenance. Source and rebuilding instructions are available at
the linked upstream projects. The application source and build spec accompany
the executable and do not restrict replacing or debugging these shared libraries.
