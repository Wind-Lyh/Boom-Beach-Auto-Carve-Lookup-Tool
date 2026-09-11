from __future__ import annotations

import re
from typing import Optional

from utils.adb_control import AdbController


class TouchTap:
    """基于 sendevent 的单指点击注入。

    腾讯国服游戏会忽略 `adb shell input tap` 这类合成输入，
    但 sendevent 直接写 /dev/input，等价于硬件触摸事件。
    """

    def __init__(self, adb: AdbController, rotation: str = "ccw"):
        self.adb = adb
        # 横屏显示(1920x1080) -> 触摸设备原生竖屏(1080x1920) 的旋转方向
        # 实测（用户点击 1390,459 -> 设备 622,1392）为逆时针 90 度
        self.rotation = rotation  # "cw" / "ccw" / "none"
        self._device: Optional[tuple[str, int, int, int, int]] = None
        self._screen_size: Optional[tuple[int, int]] = None

    def _get_device(self) -> tuple[str, int, int, int, int]:
        if self._device is not None:
            return self._device
        result = self.adb._run(["shell", "getevent", "-pl"])
        info = self._parse_touch_device(result.stdout)
        if info is None:
            raise RuntimeError("未找到支持多点触控的输入设备")
        self._device = info
        return info

    @staticmethod
    def _parse_touch_device(output: str) -> Optional[tuple[str, int, int, int, int]]:
        for block in re.split(r"(?=add device \d+:\s)", output):
            if not all(
                name in block
                for name in (
                    "ABS_MT_SLOT",
                    "ABS_MT_TRACKING_ID",
                    "ABS_MT_POSITION_X",
                    "ABS_MT_POSITION_Y",
                )
            ):
                continue
            device_match = re.search(r"add device \d+:\s+(\S+)", block)
            x_range = re.search(
                r"ABS_MT_POSITION_X\s*:.*?min\s+(-?\d+),\s+max\s+(-?\d+)",
                block,
            )
            y_range = re.search(
                r"ABS_MT_POSITION_Y\s*:.*?min\s+(-?\d+),\s+max\s+(-?\d+)",
                block,
            )
            if device_match and x_range and y_range:
                return (
                    device_match.group(1),
                    int(x_range.group(1)),
                    int(x_range.group(2)),
                    int(y_range.group(1)),
                    int(y_range.group(2)),
                )
        return None

    def _get_screen_size(self) -> tuple[int, int]:
        if self._screen_size is None:
            self._screen_size = self.adb.get_screenshot_size()
        return self._screen_size

    @staticmethod
    def _scale(value: int, screen: int, touch_min: int, touch_max: int) -> int:
        if screen <= 1:
            return touch_min
        ratio = int(value) / (screen - 1)
        return int(round(touch_min + ratio * (touch_max - touch_min)))

    def tap(self, x: int, y: int, duration: float = 0.08) -> None:
        """在显示坐标 (x, y) 处注入一次真实触摸点击（sendevent）。"""
        path, min_x, max_x, min_y, max_y = self._get_device()
        width, height = self._get_screen_size()
        tx, ty = self._to_device_coords(
            x, y, width, height, min_x, max_x, min_y, max_y
        )
        hold = max(float(duration), 0.03)
        script = (
            f"sendevent {path} 3 47 0; "
            f"sendevent {path} 3 57 100; "
            f"sendevent {path} 3 53 {tx}; "
            f"sendevent {path} 3 54 {ty}; "
            f"sendevent {path} 1 325 1; "
            f"sendevent {path} 0 0 0; "
            f"sleep {hold:.3f}; "
            f"sendevent {path} 1 325 0; "
            f"sendevent {path} 3 57 -1; "
            f"sendevent {path} 0 0 0"
        )
        self.adb._run_privileged_script(script)

    def _to_device_coords(
        self,
        x: int,
        y: int,
        screen_w: int,
        screen_h: int,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
    ) -> tuple[int, int]:
        """把显示坐标转换到触摸设备原生坐标（处理横竖屏旋转）。"""
        if self.rotation == "cw":
            # 显示(0,0) 对应设备左下角；x轴映射到设备y轴
            dx = y
            dy = (screen_w - 1) - x
            return (
                self._scale(dx, screen_h, min_x, max_x),
                self._scale(dy, screen_w, min_y, max_y),
            )
        if self.rotation == "ccw":
            dx = (screen_h - 1) - y
            dy = x
            return (
                self._scale(dx, screen_h, min_x, max_x),
                self._scale(dy, screen_w, min_y, max_y),
            )
        return (
            self._scale(x, screen_w, min_x, max_x),
            self._scale(y, screen_h, min_y, max_y),
        )
