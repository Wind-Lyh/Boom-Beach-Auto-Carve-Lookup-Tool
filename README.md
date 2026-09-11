# 雕像探索自动化（StatueExplorer）

独立于声纳项目的雕像探索（雕刻查询）自动化模块，仅依赖本文件夹内的代码与素材，可整体拷贝到其他电脑使用。

## 功能

- 自动登录（OCR 识别"登陆岛屿"按钮）→ 确认主岛 → 开启弱网（QNET 工具点击控制）→ 导航到研究页 → 加速研究 → OCR 识别属性与品质 → 写入记录
- 支持四张卡（绿/蓝/红/紫）全部或单卡运行
- 神龛（雕像制造所）不再做图像识别：坐标在 GUI 的「设置坐标」里手动设一次，
  存在 `coords.json`，流程按这两个坐标点击（start 位置 = 神龛坐标 + 偏移）
- 弱网由第三方工具 QNET（RICK 悬浮窗）控制：运行期按固定坐标点它的开关（不再识别位置与状态），
  环境检测改用 adb 查悬浮窗（`dumpsys window`，不依赖图像识别）确认工具已开启、停在屏幕左下角，
  并按固定坐标读开关灯色确认弱网处于关闭状态（只提示，不代点）
- 提供 GUI 主控制台（循环次数、卡片选择、弱网手动开关、环境检测、记录查看）

## 快速开始

```bash
python main_statue.py --x 2              # 四卡各 2 次
python main_statue.py --x 2 --outer 1    # 仅绿雕 2 次
python main_statue.py --check-env        # 环境自检（设备/游戏/分辨率/QNET 工具）
python main_statue_gui.py                # 启动 GUI
```

## 依赖环境

- MuMu 15 模拟器，分辨率 1280×720（横屏），ADB 端口 127.0.0.1:16384（可在 `config.py` 修改）
- QNET 弱网工具需提前手动打开，并把悬浮窗放到屏幕左下角（运行期按固定坐标点开关）
- 依赖安装：`pip install -r requirements.txt`

## 配置表替换

`utils/csv_patch.py` 负责把 `artifacts.csv`、`achievements.csv` 两张表从模拟器共享目录
`/storage/emulated/0/1/csv` 替换进游戏数据目录。流程是：备份整个 `update` 目录（设备与本机各一份）
→ 强制停止游戏 → 覆盖目标文件 → 调整权限与属主 → 逐个校验 SHA-1，失败可一键还原。

主流程已默认接入：每次运行（CLI 与 GUI）都会在环境自检通过后自动替换两张表，跑完
（含手动停止、异常退出）自动还原并清理临时备份。替换失败会直接终止本次运行并提示，
不会带着半成品状态继续跑。GUI 上可用“跑前替换配置表”勾选关闭，CLI 用 `--no-patch-csv`。

环境自检（GUI 的“环境检测”、CLI 的 `--check-env`，以及每次运行的启动自检）会顺带检查
上次异常退出残留的备份：若游戏目录仍是替换状态就先还原，再删掉设备与本机的备份；没有残留
则只记一行日志。

```bash
python -m utils.csv_patch replace             # 备份并替换
python -m utils.csv_patch status              # 查看是否已替换、是否还有可用备份
python -m utils.csv_patch restore --cleanup   # 还原并删除临时备份
python -m utils.csv_patch cleanup             # 只删备份
```

也可以在代码里直接调用：

```python
from utils.csv_patch import GameCsvPatcher

patcher = GameCsvPatcher()
patcher.replace()             # 替换
# ... 跑自动化流程 ...
patcher.restore(cleanup=True) # 还原并清理备份
```

## 记录文件

结果记录在 `records/` 目录，文件名自动追加时间戳防止覆盖；成功运行结束后自动清理本次产生的调试截图。

## 坐标设置（神龛）

神龛、神龛-start、雕塑、雕塑-start 这四个位置都不做图像识别，坐标由 GUI 的
「设置坐标」按钮设置：可以手填，也可以点「截取当前画面」后在截图上直接点选。
流程点击时直接用这些坐标，不再做任何偏移换算。设置结果存在项目根目录的 `coords.json`：

```json
{
  "shrine": [915, 290],           // 神龛（雕像制造所）
  "shrine_start": [945, 360],     // 神龛-start
  "sculpture": [928, 314],        // 雕塑（研究所建筑）
  "sculpture_start": [945, 377]   // 雕塑-start
}
```

研究按钮 (906, 100) 仍是 `config.py` 里写死的固定坐标，未纳入设置。
