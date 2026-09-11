from __future__ import annotations

import datetime
import re
import time
from typing import Callable

from . import config
from .flow import _ensure_adb_path

_ensure_adb_path()

from utils.adb_control import AdbController  # noqa: E402


LogFn = Callable[[str], None]


def _rule_summary(diag: str) -> str:
    v4 = re.search(r"ipv4_rule=(\d+)", diag)
    v6 = re.search(r"ipv6_rule=(\S+)", diag)
    return (
        f"ipv4={v4.group(1) if v4 else '?'} "
        f"ipv6={v6.group(1) if v6 else '?'}"
    )


def _parse_drop_counters(diag: str, chain: str) -> tuple[int, int] | None:
    """从诊断文本中解析指定链 DROP 规则的 (pkts, bytes) 计数。"""
    section = re.search(rf"\[iptables {chain}\].*?(?=\[|\Z)", diag, re.S)
    if section is None:
        return None
    for line in section.group(0).splitlines():
        m = re.match(r"^\s*(\d+)\s+(\d+)\s+DROP\s", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def run_weak_network_check(
    serial: str = config.ADB_SERIAL,
    duration: float = 12.0,
    log: LogFn = print,
) -> dict:
    """实测弱网是否有效：开启 DROP -> 观察计数器 -> 截图 -> 关闭并确认清理。

    只切换网络规则，不点击、不启动/关闭游戏；结束时必定恢复网络。
    返回汇总字典。
    """
    adb = AdbController(serial=serial)
    adb.ensure_root_shell()
    pkg = config.GAME_PACKAGE_NAME
    summary: dict = {
        "serial": serial,
        "uid": None,
        "rule_after_enable": "",
        "drop_delta_pkts": 0,
        "drop_delta_bytes": 0,
        "screenshot": None,
        "restored": False,
    }
    try:
        uid = adb._get_package_uid(pkg)
        summary["uid"] = uid
        log(f"游戏 UID: {uid}")

        before = adb.get_weak_network_diagnostics(pkg)
        log("开启前规则: " + _rule_summary(before))
        before_counters = _parse_drop_counters(before, "BBMA_WEAKNET")
        log(
            "开启前 DROP 计数: "
            + (f"{before_counters}" if before_counters else "(无规则)")
        )

        adb.enable_weak_network(pkg)
        enabled_diag = adb.get_weak_network_diagnostics(pkg)
        summary["rule_after_enable"] = _rule_summary(enabled_diag)
        log("开启后规则: " + summary["rule_after_enable"])

        log(f"观察 {duration:.0f} 秒（期间游戏若发网络请求应被 DROP 拦截）...")
        time.sleep(duration)

        debug_dir = config.RECORDS_DIR / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        shot = debug_dir / (
            "weak_check_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
        )
        adb.take_screenshot(shot)
        summary["screenshot"] = str(shot)
        log(f"弱网期间截图已保存: {shot}")

        after = adb.get_weak_network_diagnostics(pkg)
        after_counters = _parse_drop_counters(after, "BBMA_WEAKNET")
        log("开启后 DROP 计数: " + (f"{after_counters}" if after_counters else "(无)"))
        if after_counters:
            # 开启前无规则时基线按 0 计
            base = before_counters or (0, 0)
            summary["drop_delta_pkts"] = after_counters[0] - base[0]
            summary["drop_delta_bytes"] = after_counters[1] - base[1]
        log(
            f"拦截增量: {summary['drop_delta_pkts']} 包 / "
            f"{summary['drop_delta_bytes']} 字节"
        )
        return summary
    finally:
        try:
            adb.disable_weak_network(pkg)
            summary["restored"] = not adb.is_weak_network_enabled(pkg)
            log(
                "网络已恢复: "
                + ("是" if summary["restored"] else "否（请检查！）")
            )
        except Exception as exc:
            summary["restored"] = False
            log(f"恢复网络失败: {exc}")


def judge_weak_network(summary: dict) -> str:
    """根据检查结果给出结论。"""
    problems = []
    if "ipv4=1" not in summary.get("rule_after_enable", ""):
        problems.append("IPv4 规则未生效")
    if summary.get("drop_delta_pkts", 0) <= 0:
        problems.append("观察期内未拦截到流量（游戏可能空闲或无请求）")
    if not summary.get("restored", False):
        problems.append("网络未恢复")
    if not problems:
        return "弱网有效：规则生效且拦截到游戏流量，网络已恢复，可以继续。"
    return "弱网待确认：" + "；".join(problems)
