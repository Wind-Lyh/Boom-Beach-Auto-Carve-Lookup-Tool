"""雕刻查询项目的基础配置（供 utils 依赖，与声纳项目独立）。"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ADB 连接的默认设备地址（MuMu 15 实例端口）
ADB_SERIAL = "127.0.0.1:16384"

# 截图与日志目录
DEFAULT_SCREENSHOT_NAME = "screen.png"
SCREENSHOT_DIR = BASE_DIR / "_debug" / "screenshots"
LOG_DIR = BASE_DIR / "_debug" / "logs"
LOG_FILE = LOG_DIR / "bbma.log"

# 模板匹配默认参数
DEFAULT_MATCH_THRESHOLD = 0.86
DEFAULT_TEMPLATE_SHAPE_WEIGHT = 0.9
DEFAULT_TEMPLATE_SHAPE_POWER = 3.0

# 日志级别，可选 DEBUG、INFO、WARNING、ERROR
LOG_LEVEL = "INFO"
