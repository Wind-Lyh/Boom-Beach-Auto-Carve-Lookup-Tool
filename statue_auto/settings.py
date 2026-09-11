"""用户设置的伪固定坐标：读写 coords.json。

神龛、神龛-start、雕塑、雕塑-start 这四个位置都不再做图像识别，
改成 GUI 里手动设置一次、之后按设置好的坐标直接点击。
文件不存在或内容不合法时用 config 里的默认值。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config


# 坐标文件放在项目根目录，方便直接查看/手改
COORDS_PATH = config.BASE_DIR / "coords.json"

# 各项的默认值（都取自 config，GUI 里改了会写回这个文件）
DEFAULTS: dict[str, list[int]] = {
    "shrine": list(config.SHRINE_POINT),
    "shrine_start": list(config.SHRINE_START_POINT),
    "sculpture": list(config.SCULPTURE_ENTRY_POINT),
    "sculpture_start": list(config.SCULPTURE_START_POINT),
}

# 给界面用的中文名
LABELS: dict[str, str] = {
    "shrine": "神龛坐标",
    "shrine_start": "神龛-start 坐标",
    "sculpture": "雕塑坐标",
    "sculpture_start": "雕塑-start 坐标",
}


def load_coords() -> dict[str, list[int]]:
    """读坐标；缺项、文件损坏都退回默认值，旧格式会自动升级。"""
    data = {key: list(value) for key, value in DEFAULTS.items()}
    try:
        raw = json.loads(COORDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if not isinstance(raw, dict):
        return data
    for key in DEFAULTS:
        value = raw.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                data[key] = [int(value[0]), int(value[1])]
            except (TypeError, ValueError):
                continue
    # 旧版用 shrine_start_offset 表示神龛-start，这里换算成绝对坐标并写回
    if "shrine_start" not in raw and _is_point(raw.get("shrine_start_offset")):
        offset = [int(v) for v in raw["shrine_start_offset"]]
        data["shrine_start"] = [
            data["shrine"][0] + offset[0],
            data["shrine"][1] + offset[1],
        ]
        try:
            save_coords(data)
        except OSError:
            pass
    return data


def _is_point(value) -> bool:
    """判断是不是合法的 [x, y]。"""
    return isinstance(value, (list, tuple)) and len(value) == 2


def save_coords(data: dict[str, list[int]]) -> Path:
    """保存坐标并立即写回 config，保证本次运行就用新值。"""
    payload = {key: [int(value[0]), int(value[1])] for key, value in data.items()}
    COORDS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    apply_to_config(payload)
    return COORDS_PATH


def apply_to_config(data: dict[str, list[int]]) -> None:
    """把坐标写进 config 模块，运行中的流程直接读得到。"""
    mapping = {
        "shrine": "SHRINE_POINT",
        "shrine_start": "SHRINE_START_POINT",
        "sculpture": "SCULPTURE_ENTRY_POINT",
        "sculpture_start": "SCULPTURE_START_POINT",
    }
    for key, attr in mapping.items():
        value = data.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            setattr(config, attr, (int(value[0]), int(value[1])))
