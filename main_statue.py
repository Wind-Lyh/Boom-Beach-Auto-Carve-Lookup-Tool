"""雕像探索自动化 - 独立命令行入口（不影响声呐功能）。"""

import argparse
import datetime
from pathlib import Path

from statue_auto.config import ADB_SERIAL, RECORDS_DIR
from statue_auto.environment_check import check_environment
from statue_auto.flow import StatueAutoFlow
from statue_auto.network_check import judge_weak_network, run_weak_network_check
from statue_auto.recorder import unique_record_path


def main() -> int:
    parser = argparse.ArgumentParser(description="雕像探索自动化")
    parser.add_argument("--x", type=int, help="内循环次数")
    parser.add_argument(
        "--rounds",
        type=int,
        default=4,
        help="外层轮数（1~4，默认 4；调试时可用 1 只测绿雕）",
    )
    parser.add_argument(
        "--outer",
        type=int,
        default=0,
        help="只跑指定外层轮次（1~4，如 --outer 2 只测寒冰）",
    )
    parser.add_argument(
        "--stop-after-research",
        action="store_true",
        help="测试模式：点进研究界面后即停止，游戏保持开启",
    )
    parser.add_argument("--adb", default=ADB_SERIAL, help="ADB 设备地址")
    parser.add_argument("--file", default="", help="记录文件名（默认按日期）")
    parser.add_argument(
        "--check-network",
        action="store_true",
        help="只做弱网自检（开 DROP -> 观察 -> 关闭），不运行探索流程",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="只做环境自检（设备/root/游戏/分辨率），不运行探索流程",
    )
    parser.add_argument(
        "--no-patch-csv",
        action="store_true",
        help="跳过配置表替换（默认跑前自动替换 artifacts/achievements，跑完自动还原）",
    )
    args = parser.parse_args()

    if args.check_network:
        summary = run_weak_network_check(serial=args.adb)
        print(judge_weak_network(summary))
        return 0

    if args.check_env:
        problems = check_environment(serial=args.adb)
        if problems:
            for problem in problems:
                print(f"失败: {problem}")
            return 1
        print("环境正常，可以运行")
        return 0

    if args.x is None:
        parser.error("--x 内循环次数必填（或使用 --check-network 自检弱网）")

    # 文件名末尾追加当前时间，避免重复运行覆盖已有记录
    stem = (
        Path(args.file).stem
        if args.file
        else datetime.datetime.now().strftime("%Y%m%d")
    )
    record_path = unique_record_path(RECORDS_DIR, stem)

    if args.outer:
        if not 1 <= args.outer <= 4:
            parser.error("--outer 必须在 1..4")
        args.rounds = args.outer
        start_outer = args.outer - 1
    else:
        start_outer = 0

    flow = StatueAutoFlow(
        inner_count=args.x,
        serial=args.adb,
        log=print,
        max_outer=args.rounds,
        stop_after_research=args.stop_after_research,
        start_outer=start_outer,
        patch_csv=not args.no_patch_csv,
    )
    results = flow.run(record_path)
    print(f"记录文件: {record_path}")
    print(f"结果概览: {len(results)} 轮")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
