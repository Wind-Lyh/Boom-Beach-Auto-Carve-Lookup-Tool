from __future__ import annotations

import os
from pathlib import Path
from time import monotonic
from typing import Callable

import cv2
import datetime

from . import config, settings
from .environment_check import check_environment
from .ocr_text import classify_result, get_engine, ocr_lines
from .qnet import click_qnet_switch
from .recorder import StatueRecorder
from .touch import TouchTap


def _ensure_adb_path() -> None:
    """把 MuMu 自带的 adb 目录加入 PATH，供 subprocess 调用 adb。"""
    adb_dir = str(config.MUMU_ADB_DIR)
    current = os.environ.get("PATH", "")
    if adb_dir not in current:
        os.environ["PATH"] = adb_dir + os.pathsep + current


_ensure_adb_path()

from utils.adb_control import AdbController  # noqa: E402
from utils.csv_patch import GameCsvPatcher  # noqa: E402
from utils.image_match import MatchResult, find_template  # noqa: E402


LogFn = Callable[[str], None]
StopFn = Callable[[], bool]


class FlowStoppedError(RuntimeError):
    """用户手动停止时抛出。"""


def _noop_log(_msg: str) -> None:
    pass


def _never_stop() -> bool:
    return False


class StatueAutoFlow:
    """雕像探索自动化主流程（独立于声呐功能）。

    外层 4 轮分别对应 绿/蓝/红/紫 四种雕像的探索按钮
    (exploration-start1~4)；每轮内部循环 x 次，流程为：
    主岛 -> 雕塑入口 -> 研究 -> startN -> 加速 -> 确认 -> 结果页 OCR
    -> 写记录 -> 继续（回到研究页）。每轮结束强制停止游戏并恢复网络。
    """

    def __init__(
        self,
        inner_count: int,
        serial: str = config.ADB_SERIAL,
        threshold: float = config.DEFAULT_MATCH_THRESHOLD,
        log: LogFn | None = None,
        should_stop: StopFn | None = None,
        max_outer: int | None = None,
        stop_after_research: bool = False,
        start_outer: int = 0,
        outers: list[int] | None = None,
        status_cb: Callable[[str], None] | None = None,
        patch_csv: bool = True,
    ):
        if inner_count <= 0:
            raise ValueError("内循环次数必须大于 0")
        self.inner_count = int(inner_count)
        self.max_outer = config.OUTER_COUNT if max_outer is None else int(max_outer)
        if not 1 <= self.max_outer <= config.OUTER_COUNT:
            raise ValueError(f"轮数必须在 1..{config.OUTER_COUNT}")
        self.start_outer = int(start_outer)
        if not 0 <= self.start_outer < self.max_outer:
            raise ValueError(f"起始轮 {self.start_outer + 1} 超出范围")
        self.outers = None
        if outers is not None:
            self.outers = [int(o) for o in outers]
            for o in self.outers:
                if not 0 <= o < config.OUTER_COUNT:
                    raise ValueError(f"轮次必须在 1..{config.OUTER_COUNT}")
        self.serial = serial
        self.threshold = threshold
        self.log = log or _noop_log
        self.status_cb = status_cb or _noop_log
        self.should_stop = should_stop or _never_stop
        self.stop_after_research = bool(stop_after_research)
        # 跑前替换配置表、跑完自动还原；失败直接中止本次运行
        self.patch_csv = bool(patch_csv)
        self.csv_restore_error: str | None = None
        self._patcher: GameCsvPatcher | None = None
        # 弱网开关不再识别，按固定坐标盲点，所以自己记住切换后的状态
        # （默认认为运行前是关闭状态，环境检测会要求工具已就位）
        self._weak_net_on = False
        # 神龛等点位不再识别，取 GUI 里设置好的伪固定坐标（coords.json）
        self.coords = settings.load_coords()
        self.adb = AdbController(serial=serial)
        self.tapper = TouchTap(self.adb)
        self.recorder: StatueRecorder | None = None
        self.results: list[dict] = []
        self._last_gate_log = 0.0
        self._research_reached = False
        self._debug_files: list[Path] = []
        self._stopped = False

    # ---------- 基础操作 ----------

    def _check_stop(self) -> None:
        if self.should_stop():
            raise FlowStoppedError("用户手动停止")

    def _tap(self, x: int, y: int) -> None:
        """执行一次屏幕点击。

        1280x720 下 adb input tap 坐标与截图一致，直接使用；
        其它分辨率（如 1920x1080）下改用 sendevent 旋转映射。
        """
        if config.USE_INPUT_TAP:
            self.adb.click(x, y)
        else:
            self.tapper.tap(x, y)

    def _tpl(self, name: str) -> Path:
        return config.TEMPLATES[name]

    def _tpl_start(self, outer: int) -> Path:
        return config.TEMPLATES["exploration_start"][outer]

    def _wait(
        self,
        tpl_path: Path,
        timeout: float = 30.0,
        desc: str = "",
        gate_tpl: Path | None = None,
        gate_threshold: float | None = None,
        target_threshold: float | None = None,
        roi: tuple[int, int, int, int] | None = None,
        verify_text: str | None = None,
    ) -> MatchResult | None:
        """轮询等待模板出现，返回匹配结果或 None（超时）。

        若提供 gate_tpl，则同一张截图必须同时命中关卡模板，避免
        在其他界面误匹配到相似按钮。
        """
        start = monotonic()
        while monotonic() - start < timeout:
            self._check_stop()
            img = self.adb.read_screenshot()
            effective_target = (
                self.threshold if target_threshold is None else target_threshold
            )
            match = self._find_in_roi(img, tpl_path, effective_target, roi)
            if match is not None and verify_text:
                if not self._text_present_around(
                    img, match, verify_text
                ):
                    match = None
            if match is not None and gate_tpl is not None:
                effective = (
                    self.threshold if gate_threshold is None else gate_threshold
                )
                if find_template(img, gate_tpl, threshold=effective) is None:
                    # 目标已出现但关卡未通过：低频输出得分帮助诊断
                    probe = find_template(img, gate_tpl, threshold=0.5)
                    score = round(probe.score, 3) if probe else 0.0
                    if monotonic() - self._last_gate_log >= 10:
                        self.log(
                            f"{desc or tpl_path.stem} 已出现，但关卡 "
                            f"{gate_tpl.stem} 未通过（得分 {score}）"
                        )
                        self._last_gate_log = monotonic()
            if match is not None and (
                gate_tpl is None
                or find_template(
                    img,
                    gate_tpl,
                    threshold=(
                        self.threshold if gate_threshold is None else gate_threshold
                    ),
                )
                is not None
            ):
                return match
            self.adb.delay(0.5)
        self.log(f"等待超时: {desc or tpl_path.name} ({timeout:.0f}s)")
        self._save_debug_image(self.adb.read_screenshot(), f"timeout_{desc or tpl_path.stem}")
        return None

    def _text_present_around(
        self, img, match: MatchResult, expected: str
    ) -> bool:
        """在匹配区域附近 OCR，验证是否包含预期文字（用于区分相似按钮）。"""
        try:
            w = match.bottom_right[0] - match.top_left[0]
            h = match.bottom_right[1] - match.top_left[1]
            x1 = max(0, match.center[0] - int(w * 1.5))
            y1 = max(0, match.center[1] - int(h * 0.8))
            x2 = min(img.shape[1], match.center[0] + int(w * 1.5))
            y2 = min(img.shape[0], match.center[1] + int(h * 0.8))
            crop = img[y1:y2, x1:x2]
            lines = ocr_lines(crop)
            joined = "".join(lines)
            self.log(
                f"文字验证({expected}): OCR={joined or '(无)'}"
            )
            return expected in joined
        except Exception as exc:
            self.log(f"文字验证失败，按未通过处理: {exc}")
            return False

    def _ocr_find_text(
        self, img, keywords: list[str]
    ) -> tuple[int, int] | None:
        """整屏 OCR，返回第一个包含任一关键词的文字框中心坐标。"""
        result, _ = get_engine()(img)
        if not result:
            return None
        for box, text, _score in result:
            text_str = str(text)
            if any(keyword in text_str for keyword in keywords):
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                return int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
        return None

    def _wait_for_text(
        self,
        keywords: list[str],
        timeout: float = 30.0,
        desc: str = "",
    ) -> tuple[int, int] | None:
        """轮询整屏 OCR，直到出现包含关键词的文字，返回其中心坐标。"""
        start = monotonic()
        while monotonic() - start < timeout:
            self._check_stop()
            img = self.adb.read_screenshot()
            pos = self._ocr_find_text(img, keywords)
            if pos is not None:
                return pos
            self.adb.delay(0.5)
        self.log(f"等待文字超时: {desc or keywords} ({timeout:.0f}s)")
        self._save_debug_image(
            self.adb.read_screenshot(), f"timeout_text_{desc or 'ocr'}"
        )
        return None

    def _wait_main_island(
        self, timeout: float = config.MAIN_ISLAND_TIMEOUT
    ) -> tuple[int, int]:
        """登录后确认进入主岛：先等 5 秒，未确认则每 2 秒重试直到超时。

        返回"军备值"文字中心坐标；超时未确认则保存调试图并中止本轮。
        """
        self.log(
            f"登录后等待 {config.MAIN_ISLAND_INITIAL_WAIT:.0f}s，"
            f"未出现主岛特征文字则每 {config.MAIN_ISLAND_RETRY_GAP:.0f}s 重试"
        )
        self.adb.delay(config.MAIN_ISLAND_INITIAL_WAIT)
        start = monotonic()
        while True:
            self._check_stop()
            img = self.adb.read_screenshot()
            main_pos = self._ocr_find_text(img, ["军备值"])
            if main_pos is not None:
                return main_pos
            if monotonic() - start >= timeout:
                self._save_debug_image(img, "main_island_check_fail")
                raise RuntimeError("登录后未确认进入主岛，中止本轮")
            self.log("主岛特征文字未出现，稍后重试")
            self.adb.delay(config.MAIN_ISLAND_RETRY_GAP)

    @staticmethod
    def _find_in_roi(
        img,
        tpl_path: Path,
        threshold: float,
        roi: tuple[int, int, int, int] | None,
    ) -> MatchResult | None:
        """在指定 ROI 内查找模板；无 ROI 时全图查找。"""
        if roi is None:
            return find_template(img, tpl_path, threshold=threshold)
        x0, y0, w, h = roi
        x0 = max(0, int(x0))
        y0 = max(0, int(y0))
        x1 = min(img.shape[1], x0 + int(w))
        y1 = min(img.shape[0], y0 + int(h))
        if x1 <= x0 or y1 <= y0:
            return None
        crop = img[y0:y1, x0:x1]
        m = find_template(crop, tpl_path, threshold=threshold)
        if m is None:
            return None
        return MatchResult(
            template_path=m.template_path,
            top_left=(m.top_left[0] + x0, m.top_left[1] + y0),
            bottom_right=(m.bottom_right[0] + x0, m.bottom_right[1] + y0),
            center=(m.center[0] + x0, m.center[1] + y0),
            score=m.score,
        )

    def _click_wait(
        self,
        tpl_path: Path,
        timeout: float = 30.0,
        desc: str = "",
        gate_tpl: Path | None = None,
        gate_threshold: float | None = None,
        target_threshold: float | None = None,
        post_delay: float = 1.0,
        verify_text: str | None = None,
    ) -> None:
        match = self._wait(
            tpl_path,
            timeout,
            desc,
            gate_tpl,
            gate_threshold,
            target_threshold,
            verify_text=verify_text,
        )
        if match is None:
            raise RuntimeError(f"未找到目标界面: {desc or tpl_path.name}")
        self.log(f"点击: {desc or tpl_path.name} @ {match.center}")
        self.adb.delay(0.5)
        self._tap(*match.center)
        if post_delay > 0:
            self.adb.delay(post_delay)

    def _save_debug_image(self, img, tag: str) -> Path | None:
        """保存调试图，方便后续校准模板。"""
        try:
            debug_dir = config.RECORDS_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = debug_dir / f"{stamp}_{tag}.png"
            ok, buf = cv2.imencode(".png", img)
            if not ok:
                return None
            buf.tofile(str(path))
            self._debug_files.append(path)
            self.log(f"调试图已保存: {path}")
            return path
        except Exception as exc:
            self.log(f"保存调试图失败: {exc}")
            return None

    def _cleanup_debug_files(self) -> None:
        """成功结束时删除本次运行产生的调试图，失败/停止时保留供排查。"""
        if not self._debug_files:
            return
        removed = 0
        for path in self._debug_files:
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except Exception as exc:
                self.log(f"删除调试图失败 {path}: {exc}")
        self._debug_files.clear()
        if removed:
            self.log(f"已清理本次运行的 {removed} 张调试图")
        # 顺带清理早于保留天数的历史调试图，避免堆积
        try:
            debug_dir = config.RECORDS_DIR / "debug"
            if debug_dir.exists():
                cutoff = datetime.datetime.now() - datetime.timedelta(
                    days=config.DEBUG_KEEP_DAYS
                )
                stale = [
                    p
                    for p in debug_dir.glob("*.png")
                    if p.stat().st_mtime < cutoff.timestamp()
                ]
                for path in stale:
                    try:
                        path.unlink()
                    except Exception:
                        pass
                if stale:
                    self.log(
                        f"已清理 {len(stale)} 张超过 "
                        f"{config.DEBUG_KEEP_DAYS} 天的历史调试图"
                    )
        except Exception as exc:
            self.log(f"清理历史调试图失败: {exc}")

    # ---------- 结果文字识别 ----------

    def _read_result_text(self) -> tuple[str | None, str]:
        """定位结果页文字区域并 OCR，返回 (属性名, 后缀)。"""
        for attempt in range(1, 4):
            self._check_stop()
            img = self.adb.read_screenshot()
            roi = self._locate_text_roi(img)
            if roi is None:
                self.log(f"第 {attempt} 次未能定位文字区域，重试")
                self.adb.delay(1.0)
                continue

            lines = ocr_lines(roi)
            self.log("OCR 结果: " + (" | ".join(lines) if lines else "(无)"))
            name, suffix = classify_result(lines)
            if name is not None:
                return name, suffix
            self.log(f"第 {attempt} 次未匹配到属性名，重试")
            self.adb.delay(1.0)
        return None, ""

    def _locate_text_roi(self, img) -> cv2.Mat | None:
        h, w = img.shape[:2]

        # 主定位：结果面板框是固定不变的，面板内的文字内容才会变化
        panel = find_template(img, self._tpl("result_panel"), threshold=self.threshold)
        if panel is not None:
            x1, y1 = panel.top_left
            x2, y2 = panel.bottom_right
            pad_x = int((x2 - x1) * 0.08)
            pad_y = int((y2 - y1) * 0.15)
            x1 = max(0, x1 - pad_x)
            y1 = max(0, y1 - pad_y)
            x2 = min(w, x2 + pad_x)
            y2 = min(h, y2 + pad_y)
            return img[y1:y2, x1:x2]

        # 兜底：文字锚点（内容变化时不可靠，仅作参考）
        anchor = find_template(img, self._tpl("text_anchor"), threshold=self.threshold)
        if anchor is not None:
            aw = anchor.bottom_right[0] - anchor.top_left[0]
            ah = anchor.bottom_right[1] - anchor.top_left[1]
            crop_w = int(aw * config.TEXT_CROP_SCALE_W)
            crop_h = int(ah * config.TEXT_CROP_SCALE_H)
            x1 = max(0, anchor.center[0] - crop_w // 2)
            y1 = max(0, anchor.center[1] - crop_h // 2)
            x2 = min(w, x1 + crop_w)
            y2 = min(h, y1 + crop_h)
            return img[y1:y2, x1:x2]

        return None

    # ---------- 主流程 ----------

    def run(self, record_path: str | Path) -> list[dict]:
        # 启动前环境自检：设备/root/游戏/分辨率，任一失败立即中止
        problems = check_environment(self.serial, log=self.log)
        if problems:
            for problem in problems:
                self.log(f"环境检查失败: {problem}")
            raise RuntimeError("环境检查未通过: " + "；".join(problems))

        # 环境没问题后先替换配置表；替换失败直接中止，不进入探索流程
        if self.patch_csv:
            self._replace_game_csv()

        self._stopped = False
        try:
            self.recorder = StatueRecorder(record_path)
            outer_indices = (
                self.outers
                if self.outers is not None
                else range(self.start_outer, self.max_outer)
            )
            for outer in outer_indices:
                self._check_stop()
                self._run_outer(outer)
        except FlowStoppedError as exc:
            self._stopped = True
            self.log(str(exc))
            self._cleanup_network()
        except BaseException:
            self._cleanup_network()
            raise
        finally:
            # 正常结束、手动停止、异常三种情况都要还原配置表并清掉临时备份
            self._restore_game_csv()
        if not self._stopped:
            self._cleanup_debug_files()
        self.log(f"全部完成，共记录 {len(self.recorder.rows())} 行 -> {record_path}")
        return self.results

    def _replace_game_csv(self) -> None:
        """跑前把两张配置表替换进游戏目录；失败直接中止本次运行。"""
        self.log("开始替换配置表（artifacts.csv / achievements.csv）")
        patcher = GameCsvPatcher(adb=self.adb)
        try:
            backup = patcher.replace()
        except Exception as exc:
            self.log(f"配置表替换失败: {exc}")
            # 替换是分步执行的，失败时可能已经改过游戏目录，先尝试回滚
            try:
                patcher.restore(cleanup=True)
                self.log("已回滚到替换前的配置表")
            except Exception as rollback_exc:
                self.log(
                    "配置表回滚失败，请手动执行 "
                    f"python -m utils.csv_patch restore --cleanup : {rollback_exc}"
                )
            raise RuntimeError(f"配置表替换失败，已终止: {exc}") from exc
        self._patcher = patcher
        self.log(f"配置表替换完成，备份: {backup}")

    def _restore_game_csv(self) -> None:
        """收尾时用备份还原游戏目录，并删除设备/本机的临时备份。"""
        patcher = self._patcher
        if patcher is None:
            return
        self._patcher = None
        self.csv_restore_error = None
        self.log("正在还原配置表...")
        try:
            patcher.restore(cleanup=True)
        except Exception as exc:
            self.csv_restore_error = str(exc)
            self.log(
                "配置表还原失败，请手动执行 "
                f"python -m utils.csv_patch restore --cleanup 检查: {exc}"
            )
            return
        self.log("配置表已还原，临时备份已清理")

    def _cleanup_network(self) -> None:
        try:
            self.adb.close_app(config.GAME_PACKAGE_NAME)
        except Exception as exc:
            self.log(f"关闭游戏失败: {exc}")
        try:
            self._set_weak_network(False)
        except Exception as exc:
            self.log(f"关闭弱网(QNET)失败: {exc}")
        try:
            self.adb.disable_weak_network(config.GAME_PACKAGE_NAME)
            self.log("已清理残留 iptables 规则")
        except Exception as exc:
            self.log(f"清理残留 iptables 规则失败: {exc}")

    def _set_weak_network(self, want_on: bool) -> None:
        """切换弱网：按固定坐标点开关，用内部记录的状态避免多点一次点反。

        运行期不识别工具位置（环境检测已确认工具停在屏幕左下角），
        所以只有目标状态与记录不一致时才真的点一下；多余调用不会把开关点反。
        """
        label = "开启" if want_on else "关闭"
        if self._weak_net_on == want_on:
            self.log(f"弱网已是{label}状态，无需点击")
            return
        click_qnet_switch(self.adb, self.log)
        self._weak_net_on = want_on
        self.adb.delay(config.QNET_CLICK_VERIFY_GAP)
        self.log(f"弱网已{label} (QNET)")

    # ---------- 神龛(shrine)流程 ----------

    def _run_shrine_sequence(self, round_index: int) -> None:
        """开弱网之后、进雕塑入口之前的一系列固定操作。

        round_index 是整体轮次（1~4），用来按轮次点选对应卡位。
        神龛与神龛-start 都不识别，直接用 GUI 里设置好的坐标
        （coords.json 的 shrine / shrine_start），两者后面还会各再点一次。
        """
        self.log(f"---- 神龛流程开始（第 {round_index} 轮）----")
        delay = config.SHRINE_STEP_DELAY

        for point in config.SHRINE_OPEN_POINTS:
            self._check_stop()
            self.log(f"点击: 神龛流程-固定点位 {point}")
            self._tap(*point)
            self.adb.delay(delay)

        self.log(
            f"滑动: {config.SHRINE_SWIPE_START} -> {config.SHRINE_SWIPE_END} "
            f"x{config.SHRINE_SWIPE_TIMES}"
        )
        for _ in range(config.SHRINE_SWIPE_TIMES):
            self._check_stop()
            self._swipe(config.SHRINE_SWIPE_START, config.SHRINE_SWIPE_END)
            self.adb.delay(config.SHRINE_SWIPE_GAP)
        self.adb.delay(delay)

        self.log(f"点击: 神龛流程-固定点位 {config.SHRINE_TAP_AFTER_SWIPE}")
        self._tap(*config.SHRINE_TAP_AFTER_SWIPE)
        self.adb.delay(config.SHRINE_TAP_AFTER_SWIPE_DELAY)
        self.log(f"点击: 神龛流程-固定点位 {config.SHRINE_TAP_MENU}")
        self._tap(*config.SHRINE_TAP_MENU)
        self.adb.delay(config.SHRINE_TAP_DELAY)

        # 神龛与 start 都不再识别，直接用设置好的两个坐标
        shrine_pos = tuple(self.coords["shrine"])
        shrine_start_pos = tuple(self.coords["shrine_start"])
        self.log(f"点击: 神龛（设定坐标） {shrine_pos}")
        self._tap(*shrine_pos)
        self.adb.delay(config.SHRINE_TAP_DELAY)
        self.log(f"点击: 神龛-start（设定坐标） {shrine_start_pos}")
        self._tap(*shrine_start_pos)
        self.adb.delay(config.SHRINE_TAP_DELAY)

        card_point = (
            config.SHRINE_CARD_X_BASE + config.SHRINE_CARD_X_STEP * round_index,
            config.SHRINE_CARD_Y,
        )
        self.log(f"点击: 神龛流程-第 {round_index} 轮卡位 {card_point}")
        self._tap(*card_point)
        self.adb.delay(config.SHRINE_TAP_DELAY)
        self.log(f"点击: 神龛流程-固定点位 {config.SHRINE_TAP_CENTER}")
        self._tap(*config.SHRINE_TAP_CENTER)
        self.adb.delay(config.SHRINE_TAP_DELAY)

        # 第二次点神龛已按要求删除，这里只再点一次神龛-start
        self.log(f"点击: 神龛-start（设定坐标，第二次） {shrine_start_pos}")
        self._tap(*shrine_start_pos)
        self.adb.delay(delay)

        self.log(f"点击: 神龛流程-固定点位 {config.SHRINE_TAP_LAST}")
        self._tap(*config.SHRINE_TAP_LAST)
        self.adb.delay(config.SHRINE_TAP_LAST_DELAY)
        self.log(f"点击: 神龛流程-固定点位 {config.SHRINE_TAP_AFTER_LAST}")
        self._tap(*config.SHRINE_TAP_AFTER_LAST)
        self.adb.delay(delay)
        self.log("---- 神龛流程结束 ----")

    def _swipe(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        """从 start 拖到 end；时长留空时用库默认手势时长。"""
        duration = config.SHRINE_SWIPE_DURATION_MS
        if duration is None:
            self.adb.drag(start[0], start[1], end[0], end[1])
        else:
            self.adb.drag(start[0], start[1], end[0], end[1], duration)

    def _run_outer(self, outer: int) -> None:
        column = config.COLUMNS[outer]
        if self.outers is not None:
            position = self.outers.index(outer) + 1
            header = (
                f"第 {position}/{len(self.outers)} 轮：{column}"
                f"（第 {outer + 1} 张卡）"
            )
        else:
            header = f"第 {outer + 1}/{self.max_outer} 轮：{column}"
        self.log(f"========== {header} ==========")
        self.status_cb(header)

        # 干净启动：先关游戏，确认弱网已关闭，再启动
        self.adb.close_app(config.GAME_PACKAGE_NAME)
        self._set_weak_network(False)
        self.adb.delay(1.5).open_app(config.GAME_PACKAGE_NAME)

        # 启动后等待 3~5 秒，再处理登录按钮
        self.adb.delay(4.0)
        if config.USE_FIXED_NAVIGATION:
            login_pos = self._wait_for_text(
                ["登陆岛屿", "登陆"], timeout=25, desc="登录按钮"
            )
            if login_pos is not None:
                self.log(f"OCR 识别到登录按钮 @ {login_pos}")
                self._tap(*login_pos)
            else:
                self.log(
                    f"OCR 未识别到登录按钮，使用固定坐标 {config.LOGIN_FIXED_POINT}"
                )
                self._tap(*config.LOGIN_FIXED_POINT)
        else:
            self.log("检查登录按钮")
            login = self._wait(self._tpl("login_button"), timeout=10, desc="登录按钮")
            if login is not None:
                self.adb.delay(0.5)
                self._tap(*login.center)
                self.log(f"已点击登录按钮(模板) @ {login.center}")
            else:
                self.adb.delay(0.5)
                self._tap(*config.LOGIN_FIXED_POINT)
                self.log(f"模板未匹配到登录按钮，使用固定坐标 {config.LOGIN_FIXED_POINT}")

        # 连拍几张过渡截图，方便诊断登录后的界面变化（默认关闭）
        if config.SAVE_LOGIN_BURST:
            self.adb.delay(1.0)
            self._save_debug_image(self.adb.read_screenshot(), "after_login_1s")
            self.adb.delay(2.0)
            self._save_debug_image(self.adb.read_screenshot(), "after_login_3s")
            self.adb.delay(3.0)
            self._save_debug_image(self.adb.read_screenshot(), "after_login_6s")

        if config.USE_FIXED_NAVIGATION:
            # 确认已进入主岛再开弱网、再导航，避免登录未成功时盲点
            self.log("固定点位模式：等待确认进入主岛")
            main_pos = self._wait_main_island()
            self.log(f"已确认进入主岛（特征文字 @ {main_pos}）")
            entry = None
        else:
            self.log("等待主岛雕塑入口")

            # 只有确认处于主岛才允许点击雕塑入口，避免在登录界面误匹配
            entry = self._wait(
                self._tpl("sculpture_entry"),
                timeout=90,
                desc="雕塑入口",
                gate_tpl=self._tpl("main_island"),
                gate_threshold=0.85,
            )
            if entry is None:
                raise RuntimeError("未找到雕塑入口，无法开始本轮")

        # 全程弱网（QNET）
        self._set_weak_network(True)

        # 开弱网之后、进雕塑入口之前：神龛(shrine)流程（整段是固定坐标，仅固定导航模式跑）
        if config.SHRINE_FLOW_ENABLED and config.USE_FIXED_NAVIGATION:
            self._run_shrine_sequence(outer + 1)

        if config.USE_FIXED_NAVIGATION:
            sculpture_point = tuple(self.coords["sculpture"])
            sculpture_start_point = tuple(self.coords["sculpture_start"])
            self.adb.delay(0.5)
            self.log(f"点击: 雕塑(设定坐标) @ {sculpture_point}")
            self._tap(*sculpture_point)
            self.adb.delay(0.5)
            self.log(f"点击: 雕塑-start(设定坐标) @ {sculpture_start_point}")
            self._tap(*sculpture_start_point)
            self.adb.delay(0.5)
            self.log(f"点击: 研究按钮(固定) @ {config.RESEARCH_BUTTON_POINT}")
            self._tap(*config.RESEARCH_BUTTON_POINT)
            self.adb.delay(0.5)
        else:
            self.log(f"点击: 雕塑入口 @ {entry.center}")
            self._tap(*entry.center)
            self._save_debug_image(self.adb.read_screenshot(), "after_entry_tap")

            # 点雕塑建筑后会出现 start 图标，点击后才进入雕塑主页面
            start_icon = self._wait(
                self._tpl("sculpture_start"),
                timeout=15,
                desc="雕塑-start",
                # 真 start 图标出现在雕塑建筑右下方；限制范围避免主岛误匹配
                roi=(
                    entry.center[0] - 150,
                    entry.center[1] - 30,
                    400,
                    260,
                ),
            )
            if start_icon is None:
                raise RuntimeError("未找到雕塑-start 图标，无法进入雕塑页")
            self.adb.delay(0.5)
            self.log(f"点击: 雕塑-start @ {start_icon.center}")
            self._tap(*start_icon.center)
            self._save_debug_image(self.adb.read_screenshot(), "after_start_tap")

        for i in range(1, self.inner_count + 1):
            self._check_stop()
            self._run_inner(outer, i)
            if self.stop_after_research and self._research_reached:
                break

        if self.stop_after_research and self._research_reached:
            self._set_weak_network(False)
            self.log("测试完成：已停留在研究界面，游戏保持开启，弱网已关闭")
            return

        # 退出游戏并恢复网络（先关游戏回到桌面，再关弱网）
        self.adb.close_app(config.GAME_PACKAGE_NAME)
        self._set_weak_network(False)
        self.log(f"第 {outer + 1} 轮结束：游戏已退出，弱网已关闭")

    def _run_inner(self, outer: int, index: int) -> None:
        column = config.COLUMNS[outer]
        self.status_cb(f"第 {outer + 1} 轮 内循环 {index}/{self.inner_count}")
        self.log(f"---- 内循环 {index}/{self.inner_count}（{column}）----")

        if config.USE_FIXED_NAVIGATION:
            if self.stop_after_research:
                self._research_reached = True
                self.log("已进入研究界面（测试模式：在此停止）")
                return
            self._run_inner_fixed(outer, index)
            return

        self._click_wait(
            self._tpl("research_button"),
            desc="研究按钮",
            gate_tpl=self._tpl("sculpture_main"),
            gate_threshold=0.78,
            target_threshold=0.97,
            verify_text="研究",
        )
        if self.stop_after_research:
            reached = self._wait(
                self._tpl_start(outer),
                timeout=15,
                desc=f"探索按钮 start{outer + 1}",
            )
            self._research_reached = reached is not None
            if self._research_reached:
                self.log("已进入研究界面（测试模式：在此停止）")
            else:
                self.log("已点击研究按钮，但未确认进入研究界面")
            return

        self._click_wait(self._tpl_start(outer), desc=f"探索按钮 start{outer + 1}")
        self._click_wait(self._tpl("accelerate_button"), desc="加速按钮")
        self._click_wait(self._tpl("acceleration"), desc="加速页面按钮")
        self._click_wait(self._tpl("accelerate_check"), desc="确认加速")

        panel = self._wait(self._tpl("result_panel"), timeout=40, desc="结果面板")
        if panel is None:
            raise RuntimeError("未进入结果页面")

        name, suffix = self._read_result_text()
        text = f"{name}{suffix}" if name else "(error)"
        if not name:
            self.log("警告：属性名识别失败，已写入(error)")
        self.recorder.update(index, outer, text)
        self.log(f"第 {index} 次结果: {text}")
        self.results.append(
            {"outer": outer, "inner": index, "column": column, "result": text}
        )

        self._click_wait(self._tpl("continue_button"), desc="继续按钮")

    def _run_inner_fixed(self, outer: int, index: int) -> None:
        """固定坐标版本的内循环：开始研究 -> 加速 -> 钻石 -> 确认 -> 领取 -> OCR -> 继续。"""
        column = config.COLUMNS[outer]
        card = config.CARD_START_POINTS[outer]

        # 开始研究/加速/领取/继续 都位于当前卡片的按钮区（随卡片 x 变化）
        self.log(f"点击: 开始研究(固定) @ {card}")
        self._tap(*card)
        self.adb.delay(config.DELAY_START_TO_ACCEL)

        self.log(f"点击: 加速 @ {card}")
        self._tap(*card)
        self.adb.delay(config.DELAY_ACCEL_TO_CHOICE)

        # 加速选择/确认加速 是全局弹窗位置
        self.log(f"点击: 加速选择-钻石 @ {config.ACCEL_CHOICE_DIAMOND_POINT}")
        self._tap(*config.ACCEL_CHOICE_DIAMOND_POINT)
        self.adb.delay(config.DELAY_CHOICE_TO_CONFIRM)

        self.log(f"点击: 确认加速 @ {config.ACCEL_CONFIRM_POINT}")
        self._tap(*config.ACCEL_CONFIRM_POINT)
        self.adb.delay(config.DELAY_CONFIRM_TO_CLAIM)

        self.log(f"点击: 领取 @ {card}")
        self._tap(*card)
        self.adb.delay(config.DELAY_CLAIM_TO_SKIP)

        # 领取后再点一次当前卡片的 start 按钮位置，强制跳过结果准备动画
        self.log(f"点击: 跳过准备动画(start按钮位置) @ {card}")
        self._tap(*card)
        self.adb.delay(config.DELAY_SKIP_TO_OCR)

        # 结果 OCR：保存识别用截图，裁剪卡片区域识别属性名 + 品质提升/研究代币
        result_img = self.adb.read_screenshot()
        self._save_debug_image(result_img, "ocr_result")
        name, suffix = self._read_result_text_cropped(
            first_img=result_img, outer=outer
        )
        text = f"{name}{suffix}" if name else "(error)"
        if not name:
            self.log("警告：属性名识别失败，已写入(error)")
        self.recorder.update(index, outer, text)
        self.log(f"第 {index} 次结果: {text}")
        self.results.append(
            {"outer": outer, "inner": index, "column": column, "result": text}
        )

        self.log(f"点击: 继续 @ {card}")
        self._tap(*card)
        self.adb.delay(config.DELAY_CONTINUE_TO_NEXT)

    def _read_result_text_cropped(
        self, first_img=None, outer: int = 0
    ) -> tuple[str | None, str]:
        """优先裁剪当前卡片区域 OCR（更快），失败时回退整屏 OCR。"""
        card = config.CARD_START_POINTS[outer]
        crop = (
            card[0] - config.CARD_CROP_HALF_W,
            config.CARD_CROP_Y0,
            config.CARD_CROP_HALF_W * 2,
            config.CARD_CROP_Y1 - config.CARD_CROP_Y0,
        )
        img = first_img
        for attempt in range(1, config.RESULT_OCR_ATTEMPTS + 1):
            self._check_stop()
            if img is None:
                img = self.adb.read_screenshot()

            x0, y0, w, h = crop
            x0 = max(0, x0)
            y0 = max(0, y0)
            x1 = min(img.shape[1], x0 + w)
            y1 = min(img.shape[0], y0 + h)
            cropped = img[y0:y1, x0:x1]
            lines = ocr_lines(cropped)
            self.log(
                f"结果OCR-裁剪(第{attempt}次): "
                + (" | ".join(lines) if lines else "(无)")
            )
            name, suffix = classify_result(lines)
            if name is not None:
                return name, suffix

            # 裁剪未识别到，回退整屏一次
            full_lines = ocr_lines(img)
            name, suffix = classify_result(full_lines)
            if name is not None:
                self.log("整屏回退识别成功")
                return name, suffix

            img = None
            self.adb.delay(config.RESULT_OCR_RETRY_GAP)
        return None, ""
