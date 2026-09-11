from __future__ import annotations

import datetime
import sys

from PyQt6.QtCore import QSize, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils.adb_control import AdbController

from . import config
from . import settings
from . import theme
from .environment_check import check_environment
from .flow import FlowStoppedError, StatueAutoFlow
from .network_check import judge_weak_network, run_weak_network_check
from .recorder import unique_record_path

# 卡片模式：标签 -> 外层轮次列表（None 表示全部四张）
CARD_MODES: list[tuple[str, list[int] | None]] = [
    ("全部（绿/蓝/红/紫）", None),
    ("仅绿雕", [0]),
    ("仅蓝雕", [1]),
    ("仅红雕", [2]),
    ("仅紫雕", [3]),
]


class FlowThread(QThread):
    """后台运行探索流程，日志/状态/结果通过信号回传主界面。"""

    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(
        self,
        inner_count: int,
        serial: str,
        record_path: str,
        stop_flag: list[bool],
        outers: list[int] | None = None,
        stop_after_research: bool = False,
        patch_csv: bool = True,
    ):
        super().__init__()
        self.inner_count = inner_count
        self.serial = serial
        self.record_path = record_path
        self.stop_flag = stop_flag
        self.outers = outers
        self.stop_after_research = stop_after_research
        self.patch_csv = patch_csv

    def run(self) -> None:
        try:
            flow = StatueAutoFlow(
                inner_count=self.inner_count,
                serial=self.serial,
                log=self.log_signal.emit,
                should_stop=lambda: self.stop_flag[0],
                outers=self.outers,
                stop_after_research=self.stop_after_research,
                status_cb=self.status_signal.emit,
                patch_csv=self.patch_csv,
            )
            flow.run(self.record_path)
            message = f"流程全部完成，共记录 {len(flow.results)} 次"
            if flow.csv_restore_error:
                message += (
                    f"（注意：配置表还原失败，请手动执行 "
                    f"python -m utils.csv_patch restore --cleanup 检查："
                    f"{flow.csv_restore_error}）"
                )
            self.finished_signal.emit(True, message)
        except FlowStoppedError as exc:
            self.finished_signal.emit(False, f"已手动停止：{exc}（游戏已退出、网络已恢复）")
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class NetworkCheckThread(QThread):
    """后台弱网自检（观察 12 秒），避免阻塞界面。"""

    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial

    def run(self) -> None:
        try:
            summary = run_weak_network_check(
                serial=self.serial, log=self.log_signal.emit
            )
            self.finished_signal.emit(True, judge_weak_network(summary))
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class EnvironmentCheckThread(QThread):
    """后台环境自检（设备/root/游戏/分辨率），避免阻塞界面。"""

    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial

    def run(self) -> None:
        problems = check_environment(
            serial=self.serial, log=self.log_signal.emit
        )
        if problems:
            self.finished_signal.emit(False, "；".join(problems))
        else:
            self.finished_signal.emit(True, "环境正常，可以运行")


class WeakNetToggleThread(QThread):
    """后台开关弱网，只切换规则不做其他操作。"""

    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, serial: str, enabled: bool):
        super().__init__()
        self.serial = serial
        self.enabled = enabled

    def run(self) -> None:
        action = "开启" if self.enabled else "关闭"
        try:
            adb = AdbController(serial=self.serial)
            if self.enabled:
                adb.enable_weak_network(config.GAME_PACKAGE_NAME)
            else:
                adb.disable_weak_network(config.GAME_PACKAGE_NAME)
            msg = f"弱网已{action} (DROP)" if self.enabled else f"弱网已{action}"
            self.finished_signal.emit(True, msg)
        except Exception as exc:
            self.finished_signal.emit(False, f"弱网{action}失败: {exc}")


class ScreenshotThread(QThread):
    """后台截一张设备画面，给坐标设置界面取点用。"""

    shot_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial

    def run(self) -> None:
        try:
            self.shot_signal.emit(AdbController(serial=self.serial).read_screenshot())
        except Exception as exc:
            self.error_signal.emit(str(exc))


class CoordinatePickerLabel(QLabel):
    """显示截图，鼠标点一下就把点击位置换算成图像坐标发出去。"""

    picked = pyqtSignal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(720, 405)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("pickerArea")
        self.setText("点「截取当前画面」后，在图上点一下即可取坐标")
        self._image: QImage | None = None
        self._pixmap: QPixmap | None = None

    def set_image(self, image: QImage) -> None:
        self._image = image
        self._refresh()

    def _refresh(self) -> None:
        if self._image is None:
            return
        self._pixmap = QPixmap.fromImage(self._image).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(self._pixmap)

    def resizeEvent(self, event) -> None:
        self._refresh()
        super().resizeEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._image is None or self._pixmap is None:
            return
        offset_x = (self.width() - self._pixmap.width()) // 2
        offset_y = (self.height() - self._pixmap.height()) // 2
        x = event.position().x() - offset_x
        y = event.position().y() - offset_y
        if not (0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height()):
            return
        self.picked.emit(
            int(x * self._image.width() / self._pixmap.width()),
            int(y * self._image.height() / self._pixmap.height()),
        )


class CoordinateDialog(QDialog):
    """设置伪固定坐标：神龛 / 神龛-start / 雕塑 / 雕塑-start（可截当前画面点选取点）。"""

    # 输入框顺序，也是点图取点下拉里的顺序
    FIELDS = ("shrine", "shrine_start", "sculpture", "sculpture_start")

    def __init__(self, serial: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置坐标")
        self.resize(1000, 720)
        self._serial = serial
        self._thread: ScreenshotThread | None = None
        self._spins: dict[str, tuple[QSpinBox, QSpinBox]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        coord_group = QGroupBox("坐标（点「保存」后写入 coords.json）")
        theme.apply_card_shadow(coord_group)
        form = QFormLayout(coord_group)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        for key in self.FIELDS:
            spin_x = QSpinBox()
            spin_x.setRange(0, 4096)
            spin_y = QSpinBox()
            spin_y.setRange(0, 4096)
            row = QHBoxLayout()
            row.addWidget(QLabel("x"))
            row.addWidget(spin_x)
            row.addSpacing(12)
            row.addWidget(QLabel("y"))
            row.addWidget(spin_y)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            form.addRow(settings.LABELS[key], holder)
            self._spins[key] = (spin_x, spin_y)
        layout.addWidget(coord_group)

        pick_group = QGroupBox("截图取点（可选）")
        theme.apply_card_shadow(pick_group)
        pick_layout = QVBoxLayout(pick_group)
        pick_layout.setContentsMargins(18, 22, 18, 18)
        pick_layout.setSpacing(12)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(10)
        pick_row.addWidget(QLabel("取点目标:"))
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(150)
        for key in self.FIELDS:
            self.target_combo.addItem(settings.LABELS[key], key)
        pick_row.addWidget(self.target_combo)
        self.capture_btn = QPushButton("截取当前画面")
        self.capture_btn.clicked.connect(self._capture)
        pick_row.addWidget(self.capture_btn)
        self.hint_label = QLabel("（在图上点一下即可设置当前选中的坐标）")
        self.hint_label.setObjectName("hintLabel")
        pick_row.addWidget(self.hint_label)
        pick_row.addStretch(1)
        pick_layout.addLayout(pick_row)

        self.picker = CoordinatePickerLabel()
        self.picker.picked.connect(self._on_picked)
        pick_layout.addWidget(self.picker, stretch=1)
        layout.addWidget(pick_group, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_btn.setText("保存")
        save_btn.setObjectName("primaryButton")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload()

    def reload(self) -> None:
        """把当前坐标（文件里的值）填进输入框。"""
        for key, (spin_x, spin_y) in self._spins.items():
            value = settings.load_coords()[key]
            spin_x.setValue(value[0])
            spin_y.setValue(value[1])

    def _capture(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self.capture_btn.setEnabled(False)
        self.picker.setText("正在截取当前画面...")
        self._thread = ScreenshotThread(self._serial)
        self._thread.shot_signal.connect(self._shot_ready)
        self._thread.error_signal.connect(self._shot_failed)
        self._thread.start()

    def _shot_ready(self, image) -> None:
        self.capture_btn.setEnabled(True)
        height, width = image.shape[:2]
        channels = image.shape[2] if image.ndim == 3 else 1
        if channels == 3:
            qimage = QImage(
                image.data, width, height, channels * width,
                QImage.Format.Format_BGR888,
            ).copy()
        else:
            qimage = QImage(
                image.data, width, height, width, QImage.Format.Format_Grayscale8
            ).copy()
        self.picker.set_image(qimage)

    def _shot_failed(self, message: str) -> None:
        self.capture_btn.setEnabled(True)
        self.picker.setText("截取失败")
        QMessageBox.warning(self, "截取失败", message)

    def _on_picked(self, image_x: int, image_y: int) -> None:
        key = self.target_combo.currentData()
        spin_x, spin_y = self._spins[key]
        spin_x.setValue(image_x)
        spin_y.setValue(image_y)
        self.hint_label.setText(
            f"已取点：{settings.LABELS[key]} = ({image_x}, {image_y})"
        )

    def _save(self) -> None:
        data = {
            key: [spin_x.value(), spin_y.value()]
            for key, (spin_x, spin_y) in self._spins.items()
        }
        try:
            path = settings.save_coords(data)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.saved_path = path
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("雕像探索自动化")
        self.resize(900, 660)
        # 最小高度按"窄窗口换行后仍不挤压"来定；再小就由日志区去缩
        self.setMinimumSize(560, 620)
        self._stop_flag = [False]
        self._thread: FlowThread | None = None
        self._check_thread: NetworkCheckThread | None = None
        self._env_thread: EnvironmentCheckThread | None = None
        self._net_thread: WeakNetToggleThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        params = QGroupBox("运行参数")
        theme.apply_card_shadow(params)
        # 高度不参与压缩：空间不够时让日志区去缩，别挤压参数/选项
        params.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        params_layout = QGridLayout(params)
        params_layout.setHorizontalSpacing(24)
        params_layout.setVerticalSpacing(12)

        self.x_spin = QSpinBox()
        self.x_spin.setRange(1, 9999)
        self.x_spin.setValue(2)
        self.x_spin.setMinimumWidth(90)

        self.serial_edit = QLineEdit(config.ADB_SERIAL)
        self.serial_edit.setMinimumWidth(160)

        self.card_combo = QComboBox()
        self.card_combo.setMinimumWidth(150)
        for label, _outers in CARD_MODES:
            self.card_combo.addItem(label)

        self.file_edit = QLineEdit(datetime.datetime.now().strftime("%Y%m%d"))
        self.file_edit.setMinimumWidth(140)

        # 两列排布，窗口变窄时也不会挤成一团
        params_layout.addWidget(QLabel("循环次数 x:"), 0, 0)
        params_layout.addWidget(self.x_spin, 0, 1)
        params_layout.addWidget(QLabel("ADB 地址:"), 0, 2)
        params_layout.addWidget(self.serial_edit, 0, 3)
        params_layout.addWidget(QLabel("卡片:"), 1, 0)
        params_layout.addWidget(self.card_combo, 1, 1)
        params_layout.addWidget(QLabel("记录文件名(自动加时间):"), 1, 2)
        params_layout.addWidget(self.file_edit, 1, 3)
        params_layout.setColumnStretch(1, 1)
        params_layout.setColumnStretch(3, 1)
        layout.addWidget(params)

        # 运行选项：左侧两行复选框，右侧弱网手动控制（窄窗口时自动换到下方）
        self.options_card = QGroupBox("运行选项")
        theme.apply_card_shadow(self.options_card)
        self.options_card.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self._options_grid = QGridLayout(self.options_card)
        self._options_grid.setContentsMargins(18, 22, 18, 18)
        self._options_grid.setHorizontalSpacing(28)
        self._options_grid.setVerticalSpacing(14)

        self._left_box = QWidget()
        left_layout = QVBoxLayout(self._left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        self.debug_check = QCheckBox("测试模式：进入研究界面即停止（不消耗资源）")
        self.patch_check = QCheckBox(
            "跑前替换配置表（artifacts/achievements，跑完自动还原）"
        )
        self.patch_check.setChecked(True)
        left_layout.addWidget(self.debug_check)
        left_layout.addWidget(self.patch_check)
        left_layout.addStretch(1)

        self._weak_box = QWidget()
        weak_layout = QVBoxLayout(self._weak_box)
        weak_layout.setContentsMargins(0, 0, 0, 0)
        weak_layout.setSpacing(8)
        weak_caption = QLabel("弱网手动控制（仅开关规则，不执行其他操作）")
        weak_caption.setObjectName("captionLabel")
        weak_layout.addWidget(weak_caption)
        weak_row = QHBoxLayout()
        weak_row.setSpacing(6)
        self.net_on_btn = QPushButton("弱网开启")
        self.net_on_btn.setObjectName("miniButton")
        self.net_off_btn = QPushButton("弱网关闭")
        self.net_off_btn.setObjectName("miniButton")
        for button in (self.net_on_btn, self.net_off_btn):
            button.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
        self.net_on_btn.clicked.connect(lambda: self._toggle_weak_network(True))
        self.net_off_btn.clicked.connect(lambda: self._toggle_weak_network(False))
        weak_row.addWidget(self.net_on_btn)
        weak_row.addWidget(self.net_off_btn)
        weak_row.addStretch(1)
        weak_layout.addLayout(weak_row)

        self._options_grid.addWidget(self._left_box, 0, 0)
        self._options_grid.addWidget(
            self._weak_box, 0, 1, Qt.AlignmentFlag.AlignVCenter
        )
        self._options_grid.setColumnStretch(0, 1)
        # None 表示还没判断过，首次布局一定会算一遍
        self._options_stacked: bool | None = None
        layout.addWidget(self.options_card)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.setIcon(theme.play_icon())
        self.start_btn.setIconSize(QSize(14, 14))
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.setIcon(theme.stop_icon())
        self.stop_btn.setIconSize(QSize(14, 14))
        self.env_btn = QPushButton("环境检测")
        self.check_net_btn = QPushButton("弱网自检")
        self.coord_btn = QPushButton("设置坐标")
        self.open_dir_btn = QPushButton("打开记录目录")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.env_btn.clicked.connect(self._start_environment_check)
        self.check_net_btn.clicked.connect(self._start_network_check)
        self.coord_btn.clicked.connect(self._open_coordinate_dialog)
        self.open_dir_btn.clicked.connect(self._open_records_dir)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.env_btn)
        buttons.addWidget(self.check_net_btn)
        buttons.addWidget(self.coord_btn)
        buttons.addWidget(self.open_dir_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setProperty("state", "idle")
        layout.addWidget(self.status_label)

        log_group = QGroupBox("运行日志")
        theme.apply_card_shadow(log_group)
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(18, 22, 18, 18)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        # 空间不够时优先缩日志区，别去挤压上面的参数/选项卡片
        self.log_view.setMinimumHeight(48)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_group, stretch=1)

        self.setCentralWidget(central)

    # ---------- 运行控制 ----------

    def _start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        if self._check_thread is not None and self._check_thread.isRunning():
            self._append_log("弱网自检进行中，请稍后再开始")
            return
        if self._env_thread is not None and self._env_thread.isRunning():
            self._append_log("环境检测进行中，请稍后再开始")
            return
        if self._net_thread is not None and self._net_thread.isRunning():
            self._append_log("弱网开关操作进行中，请稍后再开始")
            return

        self._stop_flag[0] = False
        stem = self.file_edit.text().strip() or datetime.datetime.now().strftime(
            "%Y%m%d"
        )
        record_path = unique_record_path(config.RECORDS_DIR, stem)

        self.log_view.clear()
        self._append_log(f"记录文件: {record_path}")
        self._thread = FlowThread(
            inner_count=self.x_spin.value(),
            serial=self.serial_edit.text().strip() or config.ADB_SERIAL,
            record_path=str(record_path),
            stop_flag=self._stop_flag,
            outers=CARD_MODES[self.card_combo.currentIndex()][1],
            stop_after_research=self.debug_check.isChecked(),
            patch_csv=self.patch_check.isChecked(),
        )
        self._thread.log_signal.connect(self._append_log)
        self._thread.status_signal.connect(self._set_status)
        self._thread.finished_signal.connect(self._finished)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.env_btn.setEnabled(False)
        self.check_net_btn.setEnabled(False)
        self.net_on_btn.setEnabled(False)
        self.net_off_btn.setEnabled(False)
        self.patch_check.setEnabled(False)
        self.coord_btn.setEnabled(False)
        self._set_status("运行中...")
        self._thread.start()

    def _stop(self) -> None:
        self._stop_flag[0] = True
        self.stop_btn.setEnabled(False)
        self._set_status("正在停止（会先关闭游戏并恢复网络）...")

    def _finished(self, ok: bool, message: str) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.env_btn.setEnabled(True)
        self.check_net_btn.setEnabled(True)
        self.net_on_btn.setEnabled(True)
        self.net_off_btn.setEnabled(True)
        self.patch_check.setEnabled(True)
        self.coord_btn.setEnabled(True)
        if "手动停止" in message:
            self._set_status("已停止")
        else:
            self._set_status("完成" if ok else "失败")
        self._append_log(("完成: " if ok else "失败: ") + message)

    # ---------- 弱网自检 ----------

    def _start_network_check(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._append_log("探索流程进行中，请结束后再自检")
            return
        if self._env_thread is not None and self._env_thread.isRunning():
            self._append_log("环境检测进行中，请稍后再自检")
            return
        if self._net_thread is not None and self._net_thread.isRunning():
            self._append_log("弱网开关操作进行中，请稍后再自检")
            return
        self.check_net_btn.setEnabled(False)
        self.env_btn.setEnabled(False)
        self.net_on_btn.setEnabled(False)
        self.net_off_btn.setEnabled(False)
        self._set_status("弱网自检中（约 12 秒）...")
        self._append_log("弱网自检开始：开启 DROP -> 观察 12 秒 -> 恢复")
        self._check_thread = NetworkCheckThread(
            serial=self.serial_edit.text().strip() or config.ADB_SERIAL
        )
        self._check_thread.log_signal.connect(self._append_log)
        self._check_thread.finished_signal.connect(self._check_finished)
        self._check_thread.start()

    def _check_finished(self, ok: bool, message: str) -> None:
        self.check_net_btn.setEnabled(True)
        self.env_btn.setEnabled(True)
        self.net_on_btn.setEnabled(True)
        self.net_off_btn.setEnabled(True)
        self._set_status("完成" if ok else "自检失败")
        self._append_log(("自检: " if ok else "自检失败: ") + message)

    # ---------- 环境检测 ----------

    def _start_environment_check(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._append_log("探索流程进行中，请结束后再检测")
            return
        if self._check_thread is not None and self._check_thread.isRunning():
            self._append_log("弱网自检进行中，请稍后再检测")
            return
        if self._net_thread is not None and self._net_thread.isRunning():
            self._append_log("弱网开关操作进行中，请稍后再检测")
            return
        self.env_btn.setEnabled(False)
        self.check_net_btn.setEnabled(False)
        self.net_on_btn.setEnabled(False)
        self.net_off_btn.setEnabled(False)
        self._set_status("环境检测中...")
        self._env_thread = EnvironmentCheckThread(
            serial=self.serial_edit.text().strip() or config.ADB_SERIAL
        )
        self._env_thread.log_signal.connect(self._append_log)
        self._env_thread.finished_signal.connect(self._env_finished)
        self._env_thread.start()

    def _env_finished(self, ok: bool, message: str) -> None:
        self.env_btn.setEnabled(True)
        self.check_net_btn.setEnabled(True)
        self.net_on_btn.setEnabled(True)
        self.net_off_btn.setEnabled(True)
        if ok:
            self._set_status("环境正常")
            self._append_log(f"环境检测: {message}")
        else:
            self._set_status("环境检测未通过")
            self._append_log(f"环境检测未通过: {message}")
            QMessageBox.warning(self, "环境检测未通过", message)

    # ---------- 弱网手动开关 ----------

    def _toggle_weak_network(self, enabled: bool) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._append_log("探索流程进行中，请结束后再手动开关弱网")
            return
        if self._check_thread is not None and self._check_thread.isRunning():
            self._append_log("弱网自检进行中，请稍后再手动开关")
            return
        if self._env_thread is not None and self._env_thread.isRunning():
            self._append_log("环境检测进行中，请稍后再手动开关")
            return
        if self._net_thread is not None and self._net_thread.isRunning():
            return

        action = "开启" if enabled else "关闭"
        self.net_on_btn.setEnabled(False)
        self.net_off_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.env_btn.setEnabled(False)
        self.check_net_btn.setEnabled(False)
        self._set_status(f"弱网{action}中...")
        self._net_thread = WeakNetToggleThread(
            serial=self.serial_edit.text().strip() or config.ADB_SERIAL,
            enabled=enabled,
        )
        self._net_thread.log_signal.connect(self._append_log)
        self._net_thread.finished_signal.connect(self._net_toggle_finished)
        self._net_thread.start()

    def _net_toggle_finished(self, ok: bool, message: str) -> None:
        self.net_on_btn.setEnabled(True)
        self.net_off_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.env_btn.setEnabled(True)
        self.check_net_btn.setEnabled(True)
        self._set_status("完成" if ok else "失败")
        self._append_log(("成功: " if ok else "失败: ") + message)

    # ---------- 辅助 ----------

    def _open_coordinate_dialog(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._append_log("探索流程进行中，请结束后再设置坐标")
            return
        dialog = CoordinateDialog(
            self.serial_edit.text().strip() or config.ADB_SERIAL, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        coords = settings.load_coords()
        self._append_log(
            f"坐标已保存到 {getattr(dialog, 'saved_path', settings.COORDS_PATH)}"
        )
        for key, label in settings.LABELS.items():
            self._append_log(f"  {label}: {tuple(coords[key])}")

    def _open_records_dir(self) -> None:
        config.RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.RECORDS_DIR)))

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        # 状态色靠动态属性切换，改完要让样式重新生效
        if "失败" in text or "未通过" in text:
            state = "error"
        elif "运行" in text or "中" in text:
            state = "running"
        elif text in {"完成", "环境正常"}:
            state = "done"
        else:
            state = "idle"
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    # ---------- 自适应布局 ----------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_options_layout()

    def showEvent(self, event) -> None:
        # 首次显示时卡片才拿到最终宽度，这里补算一次，避免开局停在错误的排布
        super().showEvent(event)
        self._update_options_layout()

    def _update_options_layout(self) -> None:
        """横向放不下时，把弱网按钮组换到复选框下方。

        需要多宽是按两块内容的 sizeHint 实时算的，不写死像素，
        所以换字体、改 DPI、改文案都不会算错。
        """
        if not hasattr(self, "_options_grid"):
            return
        margins = self._options_grid.contentsMargins()
        available = (
            self.options_card.width() - margins.left() - margins.right()
        )
        needed = (
            self._left_box.sizeHint().width()
            + self._weak_box.sizeHint().width()
            + self._options_grid.horizontalSpacing()
        )
        stacked = needed > available
        if stacked == self._options_stacked:
            return
        self._options_stacked = stacked
        if stacked:
            self._options_grid.addWidget(
                self._weak_box, 1, 0, 1, 2, Qt.AlignmentFlag.AlignLeft
            )
        else:
            self._options_grid.addWidget(
                self._weak_box, 0, 1, Qt.AlignmentFlag.AlignVCenter
            )

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    # ---------- 关闭处理 ----------

    def closeEvent(self, event) -> None:
        running = self._thread is not None and self._thread.isRunning()
        if not running:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "确认退出",
            "流程正在运行。退出前会先停止流程、关闭游戏并恢复网络。确定退出？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._stop_flag[0] = True
        self._set_status("正在停止...")
        self._thread.wait(30000)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    theme.apply(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
