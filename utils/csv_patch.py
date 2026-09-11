"""海岛奇兵配置表替换工具（专用）。

只做一件事：把固定的两张表 ``artifacts.csv`` / ``achievements.csv``
从模拟器共享目录搬进游戏数据目录，替换前自动备份，并支持一键还原。

替换流程（顺序不能颠倒）：

1. 确认共享目录里两张表都在
2. 强制停止游戏，避免文件被占用、或被启动时的校验覆盖
3. 备份整个 ``update`` 目录：设备留一份，本机临时目录也留一份
4. 覆盖 ``update/csv/`` 下的目标文件
5. ``chmod -R 777`` 再把属主改回游戏账号，保证游戏进程读得到
6. 逐个比对 SHA-1，确认文件确实写进去了

典型用法::

    from csv_patch import GameCsvPatcher

    patcher = GameCsvPatcher()
    patcher.replace()        # 替换
    ...                      # 跑自动化流程
    patcher.restore()        # 还原
    patcher.cleanup()        # 删掉临时备份

命令行同样可用（在 StatueExplorer 目录下执行）::

    python -m utils.csv_patch replace
    python -m utils.csv_patch restore --cleanup
    python -m utils.csv_patch cleanup
    python -m utils.csv_patch status
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from config import ADB_SERIAL
from utils.adb_control import AdbCommandError, AdbController
from utils.logger import get_logger


logger = get_logger(__name__)


# ---- 固定配置（本工具是专用工具，不打算做成通用搬运工）----

# 游戏包名
GAME_PACKAGE_NAME = "com.tencent.tmgp.supercell.boombeach"

# 模拟器里存放待替换配置表的共享目录
SOURCE_CSV_DIR = "/storage/emulated/0/1/csv"

# 需要替换的两张表
CSV_FILENAMES = ("artifacts.csv", "achievements.csv")

# 游戏数据目录：/data/user/0/<包名>/update
UPDATE_DIR_TEMPLATE = "/data/user/0/{package}/update"

# update 目录下存放配置表的子目录
CSV_SUBDIR = "csv"

# update 目录挑出来的权限，方便游戏进程读取
TARGET_PERMISSION = "777"

# 设备侧临时备份的存放位置与命名前缀
DEVICE_BACKUP_ROOT = "/data/local/tmp"
BACKUP_NAME_PREFIX = "bb_csv_backup_"


def _q(text: str) -> str:
    """把路径等参数包成安全的 shell 单引号形式。"""
    return shlex.quote(text)


def _remove_local_dir(path: Path) -> bool:
    """删除本机目录，返回是否删干净。

    Windows 上偶尔会因目录权限删不干净，这里补救一次再判断。
    """
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        try:
            path.chmod(0o777)
        except OSError:
            pass
        shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


class GameCsvPatcher:
    """把两张固定配置表替换进游戏目录，并提供还原。"""

    def __init__(
        self,
        adb: AdbController | None = None,
        package: str = GAME_PACKAGE_NAME,
        source_dir: str = SOURCE_CSV_DIR,
    ):
        self.adb = adb or AdbController(serial=ADB_SERIAL)
        self.package = package
        self.source_dir = source_dir.rstrip("/")
        self.update_dir = UPDATE_DIR_TEMPLATE.format(package=package)
        self.csv_dir = f"{self.update_dir}/{CSV_SUBDIR}"
        self._device_backup_dir: str | None = None
        self._local_backup_dir: Path | None = None

    # ---- 对外接口 ----

    def replace(self, force: bool = False) -> Path:
        """把两张表替换进游戏目录，返回本机备份目录。

        force 为 False 时，若设备上已存在历史备份会直接报错，
        避免在半成品状态上再备份一次、把干净的原始备份覆盖掉。
        """
        self._assert_source_files()
        self._assert_no_stale_backup(force)

        self.adb.ensure_root_shell()
        self.adb.close_app(self.package)

        backup = self._create_backup()
        self._push_target_files()
        self._fix_permissions()
        self._verify_target_files()

        logger.info("配置表替换完成，备份位置: %s", backup)
        return backup

    def restore(self, cleanup: bool = False) -> None:
        """用备份把 update 目录整体还原。"""
        backup_update = self._resolve_backup_update()
        if backup_update is None:
            raise FileNotFoundError("找不到可用备份，无法还原")

        self.adb.ensure_root_shell()
        self.adb.close_app(self.package)

        before = self._remote_file_map(backup_update)
        self._sh(f"rm -rf {_q(self.update_dir)}")
        self._sh(f"cp -a {_q(backup_update)} {_q(self.update_dir)}")
        after = self._remote_file_map(self.update_dir)

        if before != after:
            logger.error("还原后校验不一致: backup=%s current=%s", before, after)
            raise RuntimeError("还原后校验不通过，请手动检查游戏目录")

        logger.info("还原完成，已恢复 %s 个文件", len(after))
        if cleanup:
            self.cleanup()

    def cleanup(self) -> None:
        """删除设备与本机的临时备份。

        除了本次实例自己建的备份，还会顺手清掉历史遗留的备份目录，
        所以在新进程里直接调 cleanup() 也能收拾干净。
        """
        device_dirs = set(self._find_device_backups())
        if self._device_backup_dir:
            device_dirs.add(self._device_backup_dir)
        for path in sorted(device_dirs):
            self._sh(f"rm -rf {_q(path)}", check=False)
            logger.info("已删除设备备份: %s", path)
        self._device_backup_dir = None

        local_dirs = set(Path(tempfile.gettempdir()).glob(f"{BACKUP_NAME_PREFIX}*"))
        if self._local_backup_dir:
            local_dirs.add(self._local_backup_dir)
        for path in sorted(local_dirs):
            if _remove_local_dir(path):
                logger.info("已删除本机备份: %s", path)
            else:
                logger.warning("本机备份删除失败，请手动清理: %s", path)
        self._local_backup_dir = None

    def is_applied(self) -> bool:
        """两张表是否已经和共享目录里的版本一致。"""
        for name in CSV_FILENAMES:
            target = f"{self.csv_dir}/{name}"
            source = self._source_path(name)
            if not self._remote_exists(target):
                return False
            if self._remote_sha1(target) != self._remote_sha1(source):
                return False
        return True

    def has_backup(self) -> bool:
        """设备或本机是否还留着可用备份。"""
        if self._device_backup_dir and self._remote_exists(self._device_backup_dir):
            return True
        if self._local_backup_update() is not None:
            return True
        return bool(self._find_device_backups())

    def cleanup_stale(self) -> str:
        """收拾上次异常退出残留的备份，返回一句日志说明；没有残留则返回空串。

        分两种情况：游戏目录还停在替换状态（说明上次没跑到还原）就先还原再删备份；
        已经是原始状态则只删备份。直接删备份会把唯一的原件弄丢，所以必须先判断。
        """
        if not self.has_backup():
            return ""
        if self.is_applied():
            self.restore(cleanup=True)
            return "发现上次残留的备份且游戏目录仍是替换状态，已还原并清理备份"
        self.cleanup()
        return "发现上次残留的备份（游戏目录已是原始状态），已清理备份"

    # ---- 内部实现 ----

    def _source_path(self, name: str) -> str:
        return f"{self.source_dir}/{name}"

    def _sh(self, script: str, *, check: bool = True):
        """以 root 身份执行一段 shell 脚本。

        复用 AdbController 里现成的特权执行通道，顺带拿到 root 检查、
        参数转义和连接自恢复。
        """
        return self.adb._run_privileged_script(script, check=check)

    def _assert_source_files(self) -> None:
        missing = [name for name in CSV_FILENAMES if not self._remote_exists(self._source_path(name))]
        if missing:
            raise FileNotFoundError(f"共享目录里缺少配置表: {', '.join(missing)}（目录 {self.source_dir}）")

    def _assert_no_stale_backup(self, force: bool) -> None:
        stale = self._find_device_backups()
        if stale and not force:
            raise RuntimeError(
                f"设备上已有备份 {stale}，说明之前替换过还没还原。"
                "请先 restore()/cleanup()，或显式传 force=True"
            )

    def _find_device_backups(self) -> list[str]:
        result = self._sh(
            f"ls -d {DEVICE_BACKUP_ROOT}/{BACKUP_NAME_PREFIX}* 2>/dev/null",
            check=False,
        )
        return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())

    def _create_backup(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._device_backup_dir = f"{DEVICE_BACKUP_ROOT}/{BACKUP_NAME_PREFIX}{stamp}"
        self._sh(
            f"mkdir -p {_q(self._device_backup_dir)} && "
            f"cp -a {_q(self.update_dir)} {_q(self._device_backup_dir)}/"
        )

        # 本机备份放在系统临时目录，用完由 cleanup() 删除。
        # 这里不用 tempfile.mkdtemp：它在 Windows 上建出的目录权限受限，
        # adb pull 无法在里面创建子目录。
        self._local_backup_dir = Path(tempfile.gettempdir()) / f"{BACKUP_NAME_PREFIX}{stamp}"
        self._local_backup_dir.mkdir(parents=True, exist_ok=True)

        device_update = f"{self._device_backup_dir}/{Path(self.update_dir).name}"
        try:
            self.adb._run(["pull", device_update, str(self._local_backup_dir)])
        except AdbCommandError as exc:
            # 本机备份只是额外保险，失败不中断替换；还原仍可用设备上的备份。
            logger.warning("本机备份失败，仅保留设备备份: %s", exc)
        logger.info("已备份游戏目录: device=%s local=%s", self._device_backup_dir, self._local_backup_dir)
        return self._local_backup_dir

    def _push_target_files(self) -> None:
        for name in CSV_FILENAMES:
            self._sh(f"cp -f {_q(self._source_path(name))} {_q(self.csv_dir)}/{_q(name)}")
            logger.info("已覆盖配置表: %s/%s", self.csv_dir, name)

    def _fix_permissions(self) -> None:
        uid, gid = self._game_uid_gid()
        self._sh(f"chmod -R {TARGET_PERMISSION} {_q(self.update_dir)}")
        self._sh(f"chown -R {uid}:{gid} {_q(self.update_dir)}")
        logger.info("已调整权限: mode=%s owner=%s:%s", TARGET_PERMISSION, uid, gid)

    def _game_uid_gid(self) -> tuple[int, int]:
        """从 update 目录的属主读出游戏账号的 uid/gid。"""
        output = self._sh(f"stat -c '%u %g' {_q(self.update_dir)}").stdout.split()
        if len(output) != 2:
            raise RuntimeError(f"无法解析 update 目录属主: {output!r}")
        return int(output[0]), int(output[1])

    def _verify_target_files(self) -> None:
        for name in CSV_FILENAMES:
            target = f"{self.csv_dir}/{name}"
            source = self._source_path(name)
            target_sha = self._remote_sha1(target)
            if target_sha != self._remote_sha1(source):
                raise RuntimeError(f"替换后校验失败: {name} 目标 SHA-1={target_sha}")
        logger.info("替换后校验通过: %s", ", ".join(CSV_FILENAMES))

    def _resolve_backup_update(self) -> str | None:
        """返回设备上可用的备份 update 目录，必要时把本机备份推回设备。"""
        if self._device_backup_dir:
            candidate = f"{self._device_backup_dir}/{Path(self.update_dir).name}"
            if self._remote_exists(candidate):
                return candidate

        backups = self._find_device_backups()
        if backups:
            self._device_backup_dir = backups[-1]
            return f"{backups[-1]}/{Path(self.update_dir).name}"

        local_update = self._local_backup_update()
        if local_update is not None and self._local_backup_dir is not None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._device_backup_dir = f"{DEVICE_BACKUP_ROOT}/{BACKUP_NAME_PREFIX}{stamp}"
            self._sh(f"mkdir -p {_q(self._device_backup_dir)}")
            self.adb._run(["push", str(local_update), self._device_backup_dir])
            return f"{self._device_backup_dir}/{Path(self.update_dir).name}"

        return None

    def _local_backup_update(self) -> Path | None:
        """返回本机备份里的 update 目录，不存在则返回 None。"""
        if self._local_backup_dir is None:
            return None
        candidate = self._local_backup_dir / Path(self.update_dir).name
        return candidate if candidate.is_dir() else None

    def _remote_exists(self, path: str) -> bool:
        return self._sh(f"test -e {_q(path)}", check=False).returncode == 0

    def _remote_sha1(self, path: str) -> str:
        result = self._sh(f"sha1sum {_q(path)}")
        digest = result.stdout.split()
        if not digest:
            raise RuntimeError(f"无法读取 SHA-1: {path}")
        return digest[0]

    def _remote_file_map(self, root: str) -> dict[str, str]:
        """列出目录下所有文件的相对路径与 SHA-1，用于还原前后比对。"""
        result = self._sh(
            f"cd {_q(root)} && find . -type f -exec sha1sum {{}} +",
            check=False,
        )
        mapping: dict[str, str] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            digest, _, relative = line.partition("  ")
            if not relative:
                continue
            mapping[relative.strip().removeprefix("./")] = digest
        return mapping


def replace_game_csvs(force: bool = False) -> Path:
    """一步到位：替换两张配置表，返回本机备份目录。"""
    return GameCsvPatcher().replace(force=force)


def restore_game_csvs(cleanup: bool = False) -> None:
    """一步到位：用最近的备份还原游戏目录。"""
    GameCsvPatcher().restore(cleanup=cleanup)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="海岛奇兵配置表替换（专用）")
    parser.add_argument(
        "action",
        choices=("replace", "restore", "cleanup", "status"),
        help="replace=替换，restore=还原，cleanup=删备份，status=查看状态",
    )
    parser.add_argument("--force", action="store_true", help="replace 时忽略历史备份直接执行")
    parser.add_argument("--cleanup", action="store_true", help="restore 成功后顺手删掉备份")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    patcher = GameCsvPatcher()

    if args.action == "replace":
        patcher.replace(force=args.force)
        return 0

    if args.action == "restore":
        patcher.restore(cleanup=args.cleanup)
        return 0

    if args.action == "cleanup":
        patcher.cleanup()
        return 0

    logger.info("两张表已替换: %s", patcher.is_applied())
    logger.info("存在可用备份: %s", patcher.has_backup())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
