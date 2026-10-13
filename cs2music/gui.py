from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QKeySequenceEdit,
)

from .audio import devices
from .config import EVENT_LABELS, Settings, data_dir, resource_path
from .controller import Controller
from .hotkeys import parse_hotkey
from .steam import find_game_dirs, install_cfg


class Window(QMainWindow):
    def __init__(self, settings: Settings, load_warning: str = ""):
        super().__init__()
        self.s = settings
        self.controller = Controller(settings)
        self.setWindowTitle("CS2 对局音乐 · TS 麦克风播放器")
        self.resize(1060, 820)
        self.setMinimumSize(880, 660)
        self._device_widgets = []
        self._locked_widgets = []
        self._last_logs = ""
        self.rows = {}
        self.track_controls = {}
        self.setFont(QFont("Microsoft YaHei UI", 10))
        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel("CS2 对局音乐")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("耳机独立监听 · 麦克风先降噪再混音 · TS 自动按键通话"))
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        tabs.addTab(self._music_tab(), "事件音乐")
        tabs.addTab(self._audio_tab(), "设备与音量")
        tabs.addTab(self._setup_tab(), "首次配置与 GSI")
        tabs.addTab(self._status_tab(), "状态与日志")
        controls = QHBoxLayout()
        self.start_button = QPushButton("启动监听")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.toggle)
        controls.addWidget(self.start_button)
        stop = QPushButton("停止音乐")
        stop.clicked.connect(self.controller.stop_music)
        controls.addWidget(stop)
        self.mute_button = QCheckBox("真实麦克风静音")
        self.mute_button.toggled.connect(lambda value: setattr(self.controller.audio, "muted", value))
        controls.addWidget(self.mute_button)
        controls.addStretch()
        save = QPushButton("保存设置")
        save.clicked.connect(lambda: self.safe(self.save))
        controls.addWidget(save)
        layout.addLayout(controls)
        self.summary = QLabel("未启动 · TS 热键未实机确认")
        layout.addWidget(self.summary)
        self.setCentralWidget(root)
        self.setStyleSheet("""
            QWidget { background: #171c26; color: #e5eaf4; }
            QLabel#title { font-size: 25px; font-weight: 700; padding: 5px 0; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
                background: #242c3a; border: 1px solid #43516a; border-radius: 5px; padding: 5px;
            }
            QPushButton { background: #2c394d; border: 1px solid #526786;
                border-radius: 5px; padding: 7px 10px; }
            QPushButton:hover { background: #3b5070; }
            QPushButton#primary { background: #246dae; }
            QPushButton:disabled, QComboBox:disabled { color: #8290a4; }
            QGroupBox { border: 1px solid #43516a; border-radius: 5px; margin-top: 12px; padding-top: 15px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; }
            QTabBar::tab { background: #242c3a; padding: 10px 15px; }
            QTabBar::tab:selected { background: #345477; }
        """)
        self.refresh_devices()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(20)
        if load_warning:
            self.controller.log(load_warning)
        if not settings.ptt_verified:
            tabs.setCurrentIndex(2)

    def safe(self, function):
        try:
            return function()
        except Exception as exc:
            QMessageBox.warning(self, "需要处理", str(exc))

    def _music_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        tip = QLabel("每类事件随机选曲，并独立设置音量和购买阶段结束后是否停止。事件音量同时作用于耳机与朋友端，再叠加各路总音量。\n取消停止后可继续到曲终或被新事件替换；炸弹取消、地图切换和 GSI 失联仍会停止。试听仅耳机，双路测试传给朋友。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        for event, label in EVENT_LABELS.items():
            box = QGroupBox(label)
            box_layout = QVBoxLayout(box)
            line = QHBoxLayout()
            box_layout.addLayout(line)
            enabled = QCheckBox("启用")
            enabled.setChecked(self.s.tracks[event].enabled)
            path = QLineEdit(self.s.tracks[event].path)
            path.setPlaceholderText("选择包含 WAV / MP3 / FLAC / OGG 的文件夹；兼容旧版单曲")
            browse = QPushButton("选择文件夹")
            browse.clicked.connect(lambda _checked=False, e=event: self.browse(e))
            listen = QPushButton("耳机试听")
            listen.clicked.connect(lambda _checked=False, e=event: self.safe(lambda: self.preview(e, False)))
            send = QPushButton("双路测试")
            send.clicked.connect(lambda _checked=False, e=event: self.safe(lambda: self.preview(e, True)))
            for item in (enabled, path, browse, listen, send):
                line.addWidget(item)
            self.rows[event] = (enabled, path)
            enabled.toggled.connect(lambda value, e=event: setattr(self.s.tracks[e], "enabled", value))
            path.textChanged.connect(lambda value, e=event: setattr(self.s.tracks[e], "path", value))
            options = QHBoxLayout()
            options.addWidget(QLabel("事件音量"))
            volume = QDoubleSpinBox()
            volume.setRange(0, 200)
            volume.setDecimals(0)
            volume.setSuffix(" %")
            volume.setValue(self.s.tracks[event].gain * 100)
            volume.setMinimumWidth(100)
            volume.valueChanged.connect(lambda value, e=event: setattr(self.s.tracks[e], "gain", value / 100))
            options.addWidget(volume)
            stop = QCheckBox("正式交战开始时停止播放")
            stop.setChecked(self.s.tracks[event].stop_on_live)
            stop.setToolTip("购买阶段结束、进入正式交战时停止；取消后播放至曲终或新事件替换。炸弹取消和状态失联仍会停止。")
            stop.toggled.connect(lambda value, e=event: setattr(self.s.tracks[e], "stop_on_live", value))
            options.addWidget(stop)
            options.addStretch()
            box_layout.addLayout(options)
            self.track_controls[event] = (volume, stop)
            layout.addWidget(box)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def _audio_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        self.mic, self.head, self.cable = QComboBox(), QComboBox(), QComboBox()
        for label, combo in (("真实麦克风", self.mic), ("耳机输出", self.head), ("虚拟输出（CABLE Input）", self.cable)):
            combo.setMinimumWidth(520)
            form.addRow(label, combo)
            self._device_widgets.append(combo)
            self._locked_widgets.append(combo)
        refresh = QPushButton("刷新音频设备")
        refresh.clicked.connect(self.refresh_devices)
        self._locked_widgets.append(refresh)
        form.addRow("", refresh)
        self.denoise = QCheckBox("启用 RNNoise（只处理真实麦克风）")
        self.denoise.setChecked(self.s.denoise)
        self._locked_widgets.append(self.denoise)
        form.addRow("降噪", self.denoise)
        self.spins = {}
        for label, name, low, high, suffix, scale in (
            ("耳机音乐音量", "headphone_gain", 0, 200, " %", 100),
            ("朋友端音乐音量", "music_gain", 0, 200, " %", 100),
            ("真实麦克风音量", "mic_gain", 0, 400, " %", 100),
            ("语音电平门限", "voice_threshold_db", -80, -5, " dB", 1),
            ("语音检测置信度", "vad_threshold", 0, 100, " %", 100),
            ("说话时音乐衰减", "duck_db", -36, 0, " dB", 1),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setDecimals(1)
            spin.setSuffix(suffix)
            spin.setValue(getattr(self.s, name) * scale)
            spin.valueChanged.connect(lambda value, n=name, k=scale: setattr(self.s, n, value / k))
            self.spins[name] = spin
            form.addRow(label, spin)
        self.ptt_key = QKeySequenceEdit(QKeySequence(self.s.ptt_key.replace("Win+", "Meta+")))
        self.ptt_key.setMaximumSequenceLength(1)
        self.ptt_key.setFinishingKeyCombinations([])
        self.ptt_key.setClearButtonEnabled(True)
        self.ptt_key.keySequenceChanged.connect(self.key_changed)
        self._locked_widgets.append(self.ptt_key)
        form.addRow("TS 按键通话键", self.ptt_key)
        form.addRow("", QLabel("点击输入框后直接按键，例如 K、Space、Ctrl+Alt+K；在 TS 中绑定同一组合。"))
        layout.addLayout(form)
        tip = QLabel("麦克风按语音门控；无说话时不会把原始环境声直接混入。说话期间仍可能存在残余噪声。\n设备变更请先停止监听。耳机与虚拟输出必须不同；请关闭 Windows 的“侦听此设备”。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch()
        return widget

    def _setup_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        instructions = QLabel(
            "① 安装 VB-CABLE 后重启电脑。在“设备与音量”中选择物理麦克风、耳机和 CABLE Input。\n"
            "② TS 输入选择 CABLE Output，改为按键通话；绑定下面相同的热键。\n"
            "③ 关闭 TS 输入的降噪／语音检测／会改变音乐音量的处理，真实麦克风由本程序降噪。\n"
            "④ 启动监听，执行热键测试。5 秒内切换到 TS 或 CS2；观察 TS 是否持续发送 6 秒。\n"
            "⑤ 两个前台场景都通过后勾选确认，再用双路测试检查朋友端的弱音、间奏和说话。\n"
            "⑥ 安装 GSI 配置并重启 CS2；首次收到状态只建立基线，不重播历史事件。")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        links = QHBoxLayout()
        cable = QPushButton("VB-CABLE 官方下载")
        cable.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://vb-audio.com/Cable/")))
        links.addWidget(cable)
        test = QPushButton("TS 热键测试（5 秒倒计时）")
        test.clicked.connect(lambda: self.safe(self.controller.test_ptt))
        links.addWidget(test)
        layout.addLayout(links)
        self.verified = QCheckBox("我已确认：TS 与 CS2 前台时热键均生效（用户实测，程序无法自动确认）")
        self.verified.setChecked(self.s.ptt_verified)
        self.verified.toggled.connect(lambda value: setattr(self.s, "ptt_verified", value))
        layout.addWidget(self.verified)
        box = QGroupBox("CS2 GSI 配置")
        form = QFormLayout(box)
        self.game_path = QLineEdit(self.s.game_dir)
        row = QHBoxLayout()
        row.addWidget(self.game_path)
        select = QPushButton("选择目录")
        select.clicked.connect(self.choose_game)
        row.addWidget(select)
        find = QPushButton("查找 Steam")
        find.clicked.connect(self.find_game)
        row.addWidget(find)
        form.addRow("游戏目录", row)
        install = QPushButton("安装专用 GSI 配置")
        install.clicked.connect(lambda: self.safe(self.install))
        form.addRow("", install)
        self.rule_spins = {}
        for label, name, low, high, integer in (
            ("炸弹总时长（秒）", "bomb_seconds", 15, 120, False),
            ("下包状态延迟补偿（秒，正值提早）", "bomb_compensation", -10, 10, False),
            ("正常半场回合数", "half_rounds", 1, 30, True),
            ("加时半场回合数", "overtime_half_rounds", 1, 15, True),
        ):
            spin = QSpinBox() if integer else QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setValue(getattr(self.s, name))
            spin.valueChanged.connect(lambda value, n=name: setattr(self.s, n, value))
            form.addRow(label, spin)
            self.rule_spins[name] = spin
        layout.addWidget(box)
        note = QLabel("精确炸弹倒计时在实战可能缺失，估算会受 GSI 延迟影响；中途进入已下包回合时不猜测下包时刻。\n默认半场 12 回合，加时半场 3 回合；其他赛制请调整。热键兼容与朋友端音质需要实机验证。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return widget

    def _status_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        layout.addWidget(self.logs)
        logs_path = QLabel(f"设置与日志位置：{data_dir()}")
        logs_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        logs_path.setWordWrap(True)
        layout.addWidget(logs_path)
        return widget

    def refresh_devices(self):
        try:
            available = devices()
        except Exception as exc:
            available = []
            self.controller.log(f"音频设备枚举失败：{exc}")
        for combo, saved, capture in ((self.mic, self.s.mic_device, True),
                                      (self.head, self.s.headphone_device, False),
                                      (self.cable, self.s.cable_device, False)):
            selected = combo.currentText() or saved
            combo.clear()
            combo.addItem("请选择设备", "")
            for device in available:
                if device.inputs if capture else device.outputs:
                    combo.addItem(device.label, device.label)
            index = combo.findText(selected)
            combo.setCurrentIndex(max(0, index))

    def key_changed(self, _value):
        self.s.ptt_verified = False
        self.verified.setChecked(False)

    def collect(self):
        self.s.mic_device = self.mic.currentData() or ""
        self.s.headphone_device = self.head.currentData() or ""
        self.s.cable_device = self.cable.currentData() or ""
        self.s.denoise = self.denoise.isChecked()
        self.s.ptt_key = parse_hotkey(self.ptt_key.keySequence().toString(QKeySequence.PortableText))[0]
        self.s.game_dir = self.game_path.text()

    def save(self):
        self.collect()
        self.s.save()
        self.controller.log("设置已保存")

    def toggle(self):
        if self.controller.running:
            self.controller.stop()
            self.controller.log("监听已停止")
        else:
            self.safe(self._start)
        self.sync_controls()

    def _start(self):
        self.save()
        self.controller.start()

    def sync_controls(self):
        running = self.controller.running
        self.start_button.setText("停止运行" if running else "启动监听")
        for widget in self._locked_widgets:
            widget.setEnabled(not running)

    def browse(self, event):
        current = Path(self.rows[event][1].text())
        initial = str(current if current.is_dir() else current.parent)
        path = QFileDialog.getExistingDirectory(self, "选择音乐文件夹（随机播放其中一首）", initial)
        if path:
            self.rows[event][1].setText(path)

    def preview(self, event, transmit):
        self.collect()
        self.controller.preview(event, transmit)

    def choose_game(self):
        path = QFileDialog.getExistingDirectory(self, "选择 CS2 根目录或 cfg 目录", self.game_path.text())
        if path:
            self.game_path.setText(path)

    def find_game(self):
        found = find_game_dirs()
        if found:
            self.game_path.setText(str(found[0]))
            self.controller.log(f"找到 CS2：{found[0]}")
        else:
            QMessageBox.information(self, "查找结果", "未找到 CS2，请手动选择 Steam 的游戏安装目录。")

    def install(self):
        destination = install_cfg(Path(self.game_path.text()))
        self.save()
        self.controller.log(f"GSI 配置已安装：{destination}；请重启 CS2")
        QMessageBox.information(self, "安装完成", f"{destination}\n请重启 CS2。")

    def tick(self):
        self.controller.tick()
        self.sync_controls()
        engine, audio, ptt = self.controller.events, self.controller.audio, self.controller.ptt
        now = time.monotonic()
        remaining = max(0.0, engine.bomb_deadline - now) if engine.bomb_deadline else None
        test = ""
        if self.controller.test_end > now:
            test = (f"热键测试倒计时 {max(0, self.controller.test_start - now):.1f}s" if now < self.controller.test_start
                    else f"热键测试保持中 {self.controller.test_end - now:.1f}s")
        status = "运行中" if self.controller.running else "未启动"
        gsi = "已连接" if engine.connected and self.controller.running else "等待 CS2"
        verified = "用户已确认" if self.s.ptt_verified else "未实机确认，自动发送暂停"
        self.summary.setText(f"{status} · GSI {gsi} · TS 热键 {verified}" + (f" · {test}" if test else ""))
        stats = audio.stats()
        self.details.setText(
            f"本人 SteamID：{engine.identity or '等待数据'}  队伍：{engine.own_team or '未知'}\n"
            f"地图：{engine.map_name or '未知'}  回合阶段：{engine.round_phase or '未知'}\n"
            f"音乐：{EVENT_LABELS.get(audio.event, '无')}  语音：{'说话中' if audio.speaking else '静音门控'}  电平：{audio.level_db:.1f} dB\n"
            f"炸弹计时来源：{engine.bomb_source}  剩余：{f'{remaining:.1f}s' if remaining is not None else '无'}\n"
            f"通话键：{self.s.ptt_key}  状态：{'保持' if ptt and ptt.held else '释放'}\n"
            f"缓冲欠载：{stats['underflows']}  溢出：{stats['overflows']}  设备标志：{stats['device_flags']}\n"
            f"错误：{self.controller.fault or '无'}")
        text = "\n".join(self.controller.logs)
        if text != self._last_logs:
            self.logs.setPlainText(text)
            self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())
            self._last_logs = text

    def closeEvent(self, event):
        self.timer.stop()
        self.controller.close()
        try:
            self.save()
            log_path = data_dir() / "last-session.log"
            log_path.write_text("\n".join(self.controller.logs), encoding="utf-8")
        except (OSError, ValueError, TypeError):
            pass
        event.accept()


def run():
    app = QApplication.instance() or QApplication([])
    warning = ""
    try:
        settings = Settings.load()
    except (OSError, ValueError, TypeError) as exc:
        settings = Settings()
        warning = f"配置无法读取，使用默认设置（原文件未在启动时覆盖）：{exc}"
        path = data_dir() / "settings.json"
        if path.exists():
            try:
                backup = path.with_name(f"settings-invalid-{time.time_ns()}.json")
                backup.write_bytes(path.read_bytes())
                warning += f"；已备份：{backup}"
            except OSError:
                warning += "；备份失败，请先保存原配置再修改设置"
    window = Window(settings, warning)
    window.show()
    return app.exec()
