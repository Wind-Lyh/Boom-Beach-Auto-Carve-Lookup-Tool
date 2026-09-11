from __future__ import annotations

import difflib
import re
from typing import Sequence

import numpy as np

from .config import ATTRIBUTE_NAMES, SUFFIX_QUALITY, SUFFIX_TOKEN

_engine = None


def get_engine():
    """懒加载 RapidOCR 引擎（首次调用可能较慢）。"""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def ocr_lines(image: np.ndarray) -> list[str]:
    """识别图片中的文字，返回逐行文本。"""
    result, _ = get_engine()(image)
    if not result:
        return []
    return [str(item[1]).strip() for item in result]


def _chinese_runs(line: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]+", line)


def classify_result(
    lines: Sequence[str],
    names: Sequence[str] | None = None,
) -> tuple[str | None, str]:
    """从 OCR 结果中提取属性名和品质后缀 (S/B)。

    返回 (属性名或 None, 后缀)。后缀规则：
    - 区域中出现"品质提升" -> S
    - 区域中出现"研究代币" -> B
    """
    names = list(names or ATTRIBUTE_NAMES)
    joined = "\n".join(lines)

    suffix = ""
    if SUFFIX_QUALITY in joined:
        suffix = "S"
    elif SUFFIX_TOKEN in joined:
        suffix = "B"

    # 1) 精确包含匹配
    for name in names:
        if name in joined:
            return name, suffix

    # 2) 中文片段模糊匹配，容忍 OCR 识别误差
    for line in lines:
        for run in _chinese_runs(line):
            close = difflib.get_close_matches(run, names, n=1, cutoff=0.55)
            if close:
                return close[0], suffix

    return None, suffix
