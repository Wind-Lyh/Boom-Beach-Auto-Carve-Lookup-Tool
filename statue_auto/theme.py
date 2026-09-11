"""界面主题：浅色极简风格。

配色、字体、控件样式都集中在这里，想调外观基本只改这个文件。
用法：启动时 ``theme.apply(app)``；白色功能区块再单独挂一层淡阴影
（Qt 的样式表不支持 box-shadow，只能用 QGraphicsDropShadowEffect）。
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget

# ---- 配色（一套主色 + 灰阶，只有危险操作带红）----
PAGE_BG = "#f4f5f7"          # 页面底色：柔和浅灰
CARD_BG = "#ffffff"          # 功能区块：白
CARD_BORDER = "#edeef1"      # 区块描边（几乎看不出）
TEXT = "#333333"             # 正文：深灰
TEXT_MUTED = "#8a8f98"       # 次要提示：浅灰
TEXT_HEADING = "#4a4f57"     # 分组标题：中性深灰（可读，但不抢眼）
TEXT_LOG = "#3f444b"         # 日志正文
LINE = "#dcdfe4"             # 输入控件描边
HOVER_BG = "#f2f3f5"         # 普通按钮悬浮底色
PRESSED_BG = "#e9ebee"

PRIMARY = "#3b7df6"          # 主色：柔和蓝
PRIMARY_HOVER = "#2f6bd8"
PRIMARY_PRESSED = "#2a60c4"
PRIMARY_DISABLED = "#c8d7f2"

# 唯一的强警示色：终止操作
DANGER = "#d64541"
DANGER_HOVER = "#c23a36"
DANGER_PRESSED = "#ad332f"

DISABLED_TEXT = "#b9bec6"
DISABLED_BG = "#f7f8f9"
DISABLED_BORDER = "#e8eaed"

# ---- 字体（无衬线，字号统一）、尺寸 ----
FONT_STACK = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
MONO_STACK = '"Consolas", "Cascadia Mono", monospace'
FONT_SIZE = 13

BUTTON_MIN_WIDTH = 108
BUTTON_MIN_HEIGHT = 34
RADIUS = 6
CARD_RADIUS = 8


STYLE = f"""
QWidget {{
    font-family: {FONT_STACK};
    font-size: {FONT_SIZE}px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {PAGE_BG}; }}

/* 功能区块：白底、极淡描边，阴影由 apply_card_shadow() 挂 */
QGroupBox {{
    background: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: {CARD_RADIUS}px;
    margin-top: 4px;
    padding: 20px 18px 18px 18px;
}}
QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    padding: 0 0 10px 0;
    color: {TEXT_HEADING};
}}

QLabel {{ background: transparent; border: none; }}
QLabel#hintLabel {{ color: {TEXT_MUTED}; }}
QLabel#captionLabel {{ color: {TEXT_MUTED}; font-size: 12px; }}

/* 状态文字：默认次要灰，运行/完成/失败各自带色 */
QLabel#statusLabel {{ color: {TEXT_MUTED}; padding: 2px 2px; }}
QLabel#statusLabel[state="running"] {{ color: {PRIMARY}; }}
QLabel#statusLabel[state="done"] {{ color: {TEXT}; }}
QLabel#statusLabel[state="error"] {{ color: {DANGER}; }}

/* 普通功能按钮：白底 + 浅灰边框，悬浮只变底色 */
QPushButton {{
    min-width: {BUTTON_MIN_WIDTH}px;
    min-height: {BUTTON_MIN_HEIGHT}px;
    padding: 4px 16px;
    border: 1px solid {LINE};
    border-radius: {RADIUS}px;
    background: {CARD_BG};
    color: {TEXT};
}}
QPushButton:hover {{ background: {HOVER_BG}; }}
QPushButton:pressed {{ background: {PRESSED_BG}; }}
QPushButton:disabled {{
    color: {DISABLED_TEXT};
    border-color: {DISABLED_BORDER};
    background: {DISABLED_BG};
}}
QPushButton:disabled:hover {{
    background: {DISABLED_BG};
    border-color: {DISABLED_BORDER};
}}

/* 核心执行按钮：柔和蓝底白字，悬浮略微加深 */
QPushButton#primaryButton {{
    background: {PRIMARY};
    border: 1px solid {PRIMARY};
    color: #ffffff;
}}
QPushButton#primaryButton:hover {{
    background: {PRIMARY_HOVER};
    border-color: {PRIMARY_HOVER};
}}
QPushButton#primaryButton:pressed {{
    background: {PRIMARY_PRESSED};
    border-color: {PRIMARY_PRESSED};
}}
QPushButton#primaryButton:disabled {{
    background: {PRIMARY_DISABLED};
    border-color: {PRIMARY_DISABLED};
    color: #ffffff;
}}

/* 迷你次要按钮：宽高同步缩小，圆角/描边沿用普通按钮 */
QPushButton#miniButton {{
    min-width: 54px;
    min-height: 26px;
    padding: 2px 10px;
}}

/* 终止操作：唯一的强警示按钮，实心红底白字 */
QPushButton#dangerButton {{
    background: {DANGER};
    border: 1px solid {DANGER};
    color: #ffffff;
}}
QPushButton#dangerButton:hover {{
    background: {DANGER_HOVER};
    border-color: {DANGER_HOVER};
}}
QPushButton#dangerButton:pressed {{
    background: {DANGER_PRESSED};
    border-color: {DANGER_PRESSED};
}}
QPushButton#dangerButton:disabled {{
    background: {DISABLED_BG};
    border-color: {DISABLED_BORDER};
    color: {DISABLED_TEXT};
}}

/* 输入类控件 */
QLineEdit, QSpinBox, QComboBox {{
    background: {CARD_BG};
    border: 1px solid {LINE};
    border-radius: {RADIUS}px;
    padding: 4px 8px;
    min-height: 24px;
    selection-background-color: {PRIMARY};
    selection-color: #ffffff;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {PRIMARY}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    color: {DISABLED_TEXT};
    background: {DISABLED_BG};
    border-color: {DISABLED_BORDER};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {CARD_BG};
    border: 1px solid {LINE};
    selection-background-color: #eef3fe;
    selection-color: {TEXT};
    outline: none;
}}

QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {LINE};
    border-radius: 4px;
    background: {CARD_BG};
}}
QCheckBox::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
}}
QCheckBox::indicator:disabled {{
    background: {DISABLED_BG};
    border-color: {DISABLED_BORDER};
}}

/* 日志区：浅底等宽字，外面那层白卡片负责分组 */
QPlainTextEdit {{
    background: #fbfbfc;
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS}px;
    padding: 8px;
    color: {TEXT_LOG};
    font-family: {MONO_STACK};
}}

/* 取点截图区 */
QLabel#pickerArea {{
    background: {HOVER_BG};
    border: 1px dashed {LINE};
    border-radius: {RADIUS}px;
    color: {TEXT_MUTED};
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: #d5d8dd;
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #c2c6cc; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: #d5d8dd;
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QDialogButtonBox QPushButton {{ min-width: 96px; }}
"""


def apply(app) -> None:
    """把主题应用到整个应用。"""
    app.setStyleSheet(STYLE)


def apply_card_shadow(widget: QWidget) -> None:
    """给白色功能区块挂一层很淡的阴影（样式表做不了，只能用效果）。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(18)
    effect.setOffset(0, 2)
    effect.setColor(QColor(17, 24, 39, 20))
    widget.setGraphicsEffect(effect)


# ---- 按钮图标：内嵌 SVG，不依赖外部文件 ----

_ICON_PLAY = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path fill="{color}" d="M4.2 2.6v10.8L13.4 8z"/></svg>'
)
_ICON_STOP = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<rect x="3.6" y="3.6" width="8.8" height="8.8" rx="1.4" fill="{color}"/></svg>'
)


def _make_icon(svg: str, color: str, size: int) -> QIcon:
    """把内嵌 SVG 渲染成 QIcon（按 2 倍图渲染，高分屏不糊）。"""
    renderer = QSvgRenderer(QByteArray(svg.format(color=color).encode("utf-8")))
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


def play_icon(color: str = "#ffffff", size: int = 14) -> QIcon:
    """播放图标（开始按钮）。"""
    return _make_icon(_ICON_PLAY, color, size)


def stop_icon(color: str = "#ffffff", size: int = 14) -> QIcon:
    """方块停止图标（停止按钮）。"""
    return _make_icon(_ICON_STOP, color, size)
