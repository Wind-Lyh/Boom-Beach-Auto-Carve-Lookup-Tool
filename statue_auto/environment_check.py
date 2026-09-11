from __future__ import annotations

import os
import re
import subprocess
from typing import Callable

from . import config

# 固定坐标模式依赖的分辨率
EXPECTED_RESOLUTION = (1280, 720)


def _ensure_adb_path() -> None:
    """把 MuMu 自带的 adb 目录加入 PATH，供 subprocess 调用 adb。"""
    adb_dir = str(config.MUMU_ADB_DIR)
    current = os.environ.get("PATH", "")
    if adb_dir not in current:
        os.environ["PATH"] = adb_dir + os.pathsep + current


def _adb(serial: str, args: list[str]) -> tuple[int, str]:
    """执行 adb 命令（不抛异常），返回 (returncode, 合并输出)。"""
    command = ["adb", "-s", serial] + args
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=20
        )
    except subprocess.TimeoutExpired:
        return 1, "adb 命令超时"
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def _adb_global(args: list[str]) -> tuple[int, str]:
    """执行不带设备参数的 adb 命令（如 adb connect）。"""
    result = subprocess.run(
        ["adb"] + args, capture_output=True, text=True, timeout=20
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def _parse_wm_size(output: str) -> tuple[int, int] | None:
    match = re.search(r"(\d+)x(\d+)", output)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _qnet_in_corner(frame: tuple[int, int, int, int], size: tuple[int, int]) -> bool:
    """悬浮窗是否停在屏幕左下角区域内。"""
    _x0, y0, x1, _y1 = frame
    width, height = max(size), min(size)
    return (
        x1 <= width * config.QNET_CORNER_X_RATIO
        and y0 >= height * config.QNET_CORNER_Y_RATIO
    )

def check_environment(
    serial: str = config.ADB_SERIAL,
    log: Callable[[str], None] = print,
) -> list[str]:
    """启动前环境自检：设备在线 / root / 游戏已安装 / 分辨率，并清理残留弱网规则。

    返回问题列表，为空表示全部通过。设备不在线时提前返回，后续检查无意义。
    """
    _ensure_adb_path()
    problems: list[str] = []
    log(f"环境自检: 设备 {serial}")

    code, out = _adb(serial, ["shell", "echo", "ok"])
    if (code != 0 or out != "ok") and ":" in serial:
        # 设备可能只是掉线未注册：host:port 形式先尝试重连一次
        _adb_global(["connect", serial])
        code, out = _adb(serial, ["shell", "echo", "ok"])
    if code != 0 or out != "ok":
        problems.append(
            f"设备不可用（{out or '无输出'}），请确认模拟器已启动且 ADB 地址正确"
        )
        return problems
    log("设备在线: 正常")

    code, out = _adb(serial, ["shell", "id", "-u"])
    root_ok = code == 0 and out == "0"
    if root_ok:
        log("root 权限: 正常")
    else:
        log("提示: 无 root 权限（QNET 流程不受影响，仅 GUI 手动开关与弱网自检需要）")

    code, out = _adb(serial, ["shell", "pm", "path", config.GAME_PACKAGE_NAME])
    if code != 0 or "package:" not in out:
        problems.append(f"游戏未安装（{config.GAME_PACKAGE_NAME}）")
    else:
        log(f"游戏已安装: {config.GAME_PACKAGE_NAME}")

    code, out = _adb(serial, ["shell", "wm", "size"])
    size = _parse_wm_size(out)
    if size is None:
        problems.append(f"无法读取屏幕分辨率（{out or '无输出'}）")
    elif sorted(size) != sorted(EXPECTED_RESOLUTION):
        problems.append(
            f"分辨率 {size[0]}x{size[1]}，固定坐标要求 "
            f"{EXPECTED_RESOLUTION[0]}x{EXPECTED_RESOLUTION[1]}（横竖屏均可）"
        )
    else:
        log(f"分辨率: {size[0]}x{size[1]}")

    if not problems and root_ok:
        # 清理上次可能残留的弱网规则（尽力而为，失败不影响主流程）
        try:
            from utils.adb_control import AdbController  # noqa: PLC0415

            adb = AdbController(serial=serial)
            adb.disable_weak_network(config.GAME_PACKAGE_NAME)
            log("已清理残留弱网规则")
        except Exception as exc:
            problems.append(f"清理残留弱网规则失败: {exc}")

        # 收拾上次异常退出残留的配置表备份：还在替换状态就先还原，再删备份
        try:
            from utils.adb_control import AdbController  # noqa: PLC0415
            from utils.csv_patch import GameCsvPatcher  # noqa: PLC0415

            adb = AdbController(serial=serial)
            note = GameCsvPatcher(adb=adb).cleanup_stale()
            log(note or "配置表备份: 无残留")
        except Exception as exc:
            problems.append(f"清理残留配置表备份失败: {exc}")
    elif not problems:
        log("跳过残留弱网规则与配置表备份清理（无 root 权限）")

    if not problems:
        # QNET 弱网工具：用 adb 查悬浮窗（不靠图像识别），再按固定坐标读开关灯色
        try:
            from utils.adb_control import AdbController  # noqa: PLC0415
            from .qnet import (  # noqa: PLC0415
                find_qnet_window,
                read_qnet_state_at,
            )

            adb = AdbController(serial=serial)
            window = find_qnet_window(adb)
            if (
                window is None
                or not window.shown
                or not _qnet_in_corner(window.frame, size or EXPECTED_RESOLUTION)
            ):
                problems.append("请将弱网工具开启并置于屏幕左下角")
            else:
                state = read_qnet_state_at(adb.read_screenshot())
                log(
                    f"弱网工具已检测到（悬浮窗 {window.frame}），当前状态: {state}"
                )
                # 运行期是盲点开关，所以开局必须处于关闭状态，这里只提示不代点
                if state == "on":
                    problems.append(
                        "弱网工具当前是开启状态，请先手动关闭弱网再运行"
                    )
                elif state == "unknown":
                    log("提示: 无法从颜色判断开关状态，请自行确认弱网是关闭的")
        except Exception as exc:
            problems.append(f"弱网工具检测失败: {exc}")

    if problems:
        return problems
    log("环境自检通过")
    return []
