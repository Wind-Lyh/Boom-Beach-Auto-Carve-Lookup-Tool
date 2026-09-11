from __future__ import annotations

import datetime
from pathlib import Path

from .config import COLUMNS

HEADER_COMMENT = "------S代表品质提升,B代表代币------"


def unique_record_path(records_dir: str | Path, stem: str) -> Path:
    """生成不覆盖已有文件的记录路径：文件名末尾追加当前时间。"""
    records_dir = Path(records_dir)
    records_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%H%M%S")
    candidate = records_dir / f"{stem}_{stamp}.txt"
    serial_no = 1
    while candidate.exists():
        candidate = records_dir / f"{stem}_{stamp}_{serial_no}.txt"
        serial_no += 1
    return candidate


class StatueRecorder:
    """以表格形式维护探索记录文件：次数 + 绿/蓝/红/紫四列。

    第 i 轮外循环只填第 i 列；每更新一格立即写盘，中断也不丢数据。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: dict[int, list[str]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("---") or line.startswith("次数"):
                continue
            parts = line.split()
            try:
                row = int(parts[0])
            except ValueError:
                continue
            cells = parts[1:]
            self._rows[row] = (cells + [""] * len(COLUMNS))[: len(COLUMNS)]

    def update(self, row: int, column: int, text: str) -> None:
        if row <= 0:
            raise ValueError("行号必须从 1 开始")
        if not 0 <= column < len(COLUMNS):
            raise ValueError(f"列号必须在 0..{len(COLUMNS) - 1}")
        cells = self._rows.setdefault(row, [""] * len(COLUMNS))
        cells[column] = text
        self._flush()

    def _flush(self) -> None:
        lines = [HEADER_COMMENT, "次数 " + " ".join(COLUMNS)]
        for row in sorted(self._rows):
            cells = [c if c else "-" for c in self._rows[row]]
            lines.append(f"{row} " + " ".join(cells))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def rows(self) -> dict[int, list[str]]:
        return dict(self._rows)
