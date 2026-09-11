from pathlib import Path

# 项目根目录（StatueExplorer）
BASE_DIR = Path(__file__).resolve().parents[1]

# ---- ADB / 模拟器 ----
MUMU_ADB_DIR = Path(r"D:\mumu模拟器\MuMuPlayer\nx_device\15.0\shell")
ADB_SERIAL = "127.0.0.1:16384"
GAME_PACKAGE_NAME = "com.tencent.tmgp.supercell.boombeach"

# ---- 模板目录（本项目的 ref_images）----
REF_DIR = BASE_DIR / "ref_images"

# 模板清单
TEMPLATES = {
    "sculpture_entry": REF_DIR / "sculpture.png",
    "sculpture_start": REF_DIR / "sculpture-start.png",
    "sculpture_main": REF_DIR / "sculpture-main.png",
    "main_island": REF_DIR / "mianland.png",
    # 登录按钮模板：从实测点击位置(970,879)重新裁剪，避免原模板误匹配
    "login_button": BASE_DIR / "statue_auto" / "templates" / "login.png",
    "research_button": REF_DIR / "sculpture-exploration.png",
    "exploration_start": [
        REF_DIR / "exploration-start1.png",
        REF_DIR / "exploration-start2.png",
        REF_DIR / "exploration-start3.png",
        REF_DIR / "exploration-start4.png",
    ],
    "accelerate_button": REF_DIR / "exploration-accelerate.png",
    "acceleration": REF_DIR / "acceleration.png",
    "accelerate_check": REF_DIR / "accelerate-check.png",
    "result_panel": REF_DIR / "exploration-result.png",
    "continue_button": REF_DIR / "exploration-continue.png",
    # 锚点复制为 ASCII 文件名，避免 OpenCV 中文路径问题
    "text_anchor": BASE_DIR / "statue_auto" / "templates" / "text_anchor.png",
}

# 匹配阈值：模板相近容易误匹配，默认取 0.86
DEFAULT_MATCH_THRESHOLD = 0.86

# ---- QNET 弱网工具（RICK）----
# 工具包名：环境检测/运行期都用它定位（不再依赖图像识别）
QNET_PACKAGE = "com.tencent.qnet.rick"
# 悬浮窗开关（也就是指示灯）在屏幕上的固定位置
QNET_LED_POINT = (73, 695)
QNET_LED_HALF = 12  # 取点周围 12 像素的正方形区域判色
# 使用从真实工具窗口裁剪的模板（RICK+窗口背景），原 NET-TOOL.png 几乎全透明、误匹配过多
QNET_TEMPLATE = BASE_DIR / "statue_auto" / "templates" / "qnet_rick.png"
QNET_TEMPLATE_THRESHOLD = 0.8
# 模板中心向右偏移量，即开关按钮位置
QNET_CLICK_OFFSET_X = 60
# 运行期不再识别工具位置，直接点这个固定坐标（工具开关）
QNET_FIXED_SWITCH_POINT = (73, 693)
# 环境检测要求工具停在屏幕左下角：横向不超过该比例、纵向不低于该比例
QNET_CORNER_X_RATIO = 0.3
QNET_CORNER_Y_RATIO = 0.6
# 状态灯采样区域（相对模板中心，实测 LED 在中心左上方）
QNET_LED_DX0 = 34
QNET_LED_DX1 = 54
QNET_LED_DY0 = -32
QNET_LED_DY1 = -5
# 点击开关后等待验证的间隔
QNET_CLICK_VERIFY_GAP = 0.5

# 成功运行结束后，顺带清理早于该天数的历史调试图
DEBUG_KEEP_DAYS = 3

# ---- 神龛(shrine)流程：开弱网之后、进雕塑入口之前的一系列固定操作 ----
SHRINE_FLOW_ENABLED = True
# 每一步之后的等待秒数
SHRINE_STEP_DELAY = 0.3
# 一开始依次点击的三个固定点位
SHRINE_OPEN_POINTS = [(47, 48), (130, 190), (700, 140)]
# 滑动：坐标固定，时长留空待调试
SHRINE_SWIPE_START = (840, 360)
SHRINE_SWIPE_END = (400, 360)
SHRINE_SWIPE_TIMES = 2
# 留空待调试：None 表示不指定时长（改用库默认手势时长），需要时改成毫秒整数
SHRINE_SWIPE_DURATION_MS: int = 50
SHRINE_SWIPE_GAP = 0.2  # 两次滑动之间的间隔，待调试
SHRINE_TAP_AFTER_SWIPE = (857, 560)
SHRINE_TAP_AFTER_SWIPE_DELAY = 0.5
SHRINE_TAP_MENU = (1024, 129)
# 神龛坐标：不再识别，由 GUI「设置坐标」里手动设置（存 coords.json）
SHRINE_POINT = (607, 585)
# 神龛-start 坐标：同样是手动设置，不再靠偏移/识别推算
SHRINE_START_POINT = (637, 655)
# 神龛流程里单次点击后的通用等待（秒）
SHRINE_TAP_DELAY = 0.3
SHRINE_TAP_CENTER = (380, 360)
# 卡位 x = 180 + 150 * i（i 为整体轮次 1~4）
SHRINE_CARD_X_BASE = 180
SHRINE_CARD_X_STEP = 150
SHRINE_CARD_Y = 140
SHRINE_TAP_LAST = (900, 460)
SHRINE_TAP_LAST_DELAY = 0.5
# 点完 (900, 460) 之后再点一下的位置
SHRINE_TAP_AFTER_LAST = (739, 460)

# ---- 记录文件 ----
RECORDS_DIR = BASE_DIR / "records"
COLUMNS = ["绿雕", "蓝雕", "红雕", "紫雕"]
OUTER_COUNT = len(COLUMNS)

# 登录按钮固定坐标（用户实测点击位置，模板匹配失败时兜底）
# 1280x720 下 OCR 定位到"登陆岛屿"按钮在 (638, 590)
LOGIN_FIXED_POINT = (638, 590)

# 1280x720 下 adb input tap 直接生效，无需 sendevent 旋转映射
USE_INPUT_TAP = True

# 1280x720 实测校准的关键点位（用户手动点击 + getevent 抓取）
SCULPTURE_ENTRY_POINT = (928, 314)   # 雕像研究所建筑
SCULPTURE_START_POINT = (945, 377)   # 面板上的 start 图标
RESEARCH_BUTTON_POINT = (906, 100)   # 雕塑页"研究"按钮

# 研究界面完整内循环固定坐标（1280x720 实测）
CARD_START_POINTS = [
    (331, 538),  # 绿雕/生命（实测）
    (530, 533),  # 蓝雕/寒冰（按布局推断，待验证）
    (743, 533),  # 红雕/熔岩（按布局推断，待验证）
    (957, 533),  # 紫雕/暗黑（按布局推断，待验证）
]
ACCELERATE_POINT = (339, 541)              # 加速
ACCEL_CHOICE_DIAMOND_POINT = (499, 549)    # 加速选择：钻石
ACCEL_CONFIRM_POINT = (635, 477)           # 确认加速
CLAIM_POINT = (315, 543)                   # 领取（查看研究成果）
CONTINUE_POINT = (334, 547)                # 继续（回到初始状态）

# 各步骤间等待秒数（用户实测可压缩到 0.2s，跳过点击后 0.3s 再截图）
DELAY_START_TO_ACCEL = 0.2
DELAY_ACCEL_TO_CHOICE = 0.2
DELAY_CHOICE_TO_CONFIRM = 0.2
DELAY_CONFIRM_TO_CLAIM = 0.2
DELAY_CLAIM_TO_SKIP = 0.5
DELAY_SKIP_TO_OCR = 0.3
DELAY_CONTINUE_TO_NEXT = 0.3

# 结果 OCR：裁剪到卡片区域（更快），失败时回退整屏
CARD_CROP_HALF_W = 160
CARD_CROP_Y0 = 130
CARD_CROP_Y1 = 610
RESULT_OCR_ATTEMPTS = 3
RESULT_OCR_RETRY_GAP = 0.8

# 登录后过渡连拍调试截图（已稳定，默认关闭省时间）
SAVE_LOGIN_BURST = False

# 主岛确认：登录后先等 7 秒，未出现"军备值"则每 2 秒重试，超过上限仍未出现才中止
MAIN_ISLAND_INITIAL_WAIT = 7.0
MAIN_ISLAND_RETRY_GAP = 2.0
MAIN_ISLAND_TIMEOUT = 60.0

# 1280x720 下使用固定点位导航（模板匹配作为后备路径）
USE_FIXED_NAVIGATION = True

# ---- OCR 候选 ----
# 从模板.txt 提取的全部属性名称（覆盖用户所说的 20 种）
ATTRIBUTE_NAMES = [
    "保险", "信号", "修补", "力竭", "加固", "干扰", "情报", "战地", "投诚",
    "接地", "昏沉", "活力", "炮火", "炼金", "硬化", "绝缘", "肾上", "能量",
    "辅助", "过热", "过载",
]

# 后缀判定关键词
SUFFIX_QUALITY = "品质提升"  # -> S
SUFFIX_TOKEN = "研究代币"    # -> B

# OCR 裁剪倍数（以文字锚点为中心扩大识别区域）
TEXT_CROP_SCALE_W = 2.2
TEXT_CROP_SCALE_H = 2.6
