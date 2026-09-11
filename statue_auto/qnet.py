from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import cv2

from . import config
from utils.image_match import MatchResult, find_template


class QnetToolError(RuntimeError):
    """QNET 弱网工具相关错误。"""


@dataclass(frozen=True)
class QnetWindow:
    """弱网工具悬浮窗的位置信息。"""

    frame: tuple[int, int, int, int]  # 窗口在屏幕上的 (x0, y0, x1, y1)
    shown: bool                       # 是否已经绘制出来（可见）


def find_qnet_window(adb) -> QnetWindow | None:
    """用 adb 查窗口信息定位弱网工具的悬浮窗，不做任何图像识别。

    返回 None 表示悬浮窗不存在（工具没开、或悬浮窗被关掉了）。
    """
    result = adb._run(["shell", "dumpsys", "window", "windows"], check=False)
    output = result.stdout or ""
    for block in output.split("  Window #")[1:]:
        if config.QNET_PACKAGE not in block:
            continue
        # 悬浮窗（SYSTEM_ALERT_WINDOW）才是要点的那个，主界面 Activity 窗口不算
        if "appop=SYSTEM_ALERT_WINDOW" not in block:
            continue
        match = re.search(r"frame=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", block)
        if match is None:
            continue
        x0, y0, x1, y1 = (int(group) for group in match.groups())
        shown = "mHasSurface=true" in block and "shown=true" in block
        return QnetWindow((x0, y0, x1, y1), shown)
    return None


def read_qnet_state_at(img) -> str:
    """按固定坐标读开关灯色，返回 'on' / 'off' / 'unknown'（不做模板匹配）。"""
    x, y = config.QNET_LED_POINT
    half = config.QNET_LED_HALF
    height, width = img.shape[:2]
    x0, x1 = max(0, x - half), min(width, x + half)
    y0, y1 = max(0, y - half), min(height, y + half)
    if x1 <= x0 or y1 <= y0:
        return "unknown"
    return classify_state(img[y0:y1, x0:x1])


def find_qnet_tool(img) -> MatchResult | None:
    """在截图中定位 QNET 工具的左侧 RICK 标识。"""
    return find_template(
        img, config.QNET_TEMPLATE, threshold=config.QNET_TEMPLATE_THRESHOLD
    )


def classify_state(roi) -> str:
    """按 HSV 色相判断开关状态：红色=开启(on)，绿色=关闭(off)。"""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(int)
    sat = hsv[:, :, 1].astype(int)
    mask = sat > 60
    if int(mask.sum()) < 5:
        return "unknown"
    hues = hue[mask]
    red_frac = float(((hues < 12) | (hues > 168)).mean())
    green_frac = float(((hues >= 35) & (hues <= 85)).mean())
    if red_frac > 0.3 and red_frac > green_frac:
        return "on"
    if green_frac > 0.3 and green_frac > red_frac:
        return "off"
    return "unknown"


def read_qnet_state(img, match: MatchResult) -> str:
    """读取状态灯颜色，返回 'on' / 'off' / 'unknown'。"""
    x0 = match.center[0] + config.QNET_LED_DX0
    x1 = match.center[0] + config.QNET_LED_DX1
    y0 = match.center[1] + config.QNET_LED_DY0
    y1 = match.center[1] + config.QNET_LED_DY1
    h, w = img.shape[:2]
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(w, x1)
    y1 = min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return "unknown"
    roi = img[y0:y1, x0:x1]
    return classify_state(roi)


def click_qnet_switch(adb, log: Callable[[str], None]) -> None:
    """按固定坐标点一下弱网工具开关。

    运行期不再识别工具位置（环境检测已确认它停在屏幕左下角），
    所以这里直接点配置里的固定坐标，点一次就是切换一次。
    """
    x, y = config.QNET_FIXED_SWITCH_POINT
    log(f"点击: 弱网工具开关(固定坐标) @ ({x}, {y})")
    adb.click(x, y)


def ensure_qnet_state(
    adb, want_on: bool, log: Callable[[str], None]
) -> None:
    """确保 QNET 弱网处于目标状态：读色 -> 必要时点击 -> 0.2s 后验证。

    主流程已改为按固定坐标盲点（见 click_qnet_switch），保留这个带识别的版本
    供调试/手工排查使用。
    """
    want_label = "开启" if want_on else "关闭"

    img = adb.read_screenshot()
    match = find_qnet_tool(img)
    if match is None:
        raise QnetToolError("弱网工具未找到，请先手动启动")
    state = read_qnet_state(img, match)
    if state == "unknown":
        raise QnetToolError("无法判断弱网工具当前开关状态")
    if (state == "on") == want_on:
        log(f"弱网工具已是{want_label}状态，无需操作")
        return

    x = match.center[0] + config.QNET_CLICK_OFFSET_X
    y = match.center[1]
    log(f"点击弱网工具开关 @ ({x}, {y})")
    adb.click(x, y)
    adb.delay(config.QNET_CLICK_VERIFY_GAP)

    img2 = adb.read_screenshot()
    match2 = find_qnet_tool(img2)
    if match2 is None:
        raise QnetToolError("点击后未找到弱网工具，无法验证状态")
    state2 = read_qnet_state(img2, match2)
    if state2 == "unknown":
        raise QnetToolError("点击后无法确认弱网工具状态")
    if (state2 == "on") != want_on:
        raise QnetToolError(
            f"弱网切换失败：目标{want_label}，"
            f"当前{'开启' if state2 == 'on' else '关闭'}"
        )
    log(f"弱网工具已{want_label}并验证成功")
