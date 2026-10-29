# ai-periph-monitor · 无线外设电量监视器

Windows 无线外设电量监测与配置工具，提供主界面、任务栏数字电量、桌面挂件和低电量提醒。针对已适配设备提供 DPI、回报率和部分灯光配置；另附独立的多品牌 HID 诊断工具。

当前主程序 **v1.0.2**，诊断工具 **v1.5.0**，构建日期 **2026-10-29**，构建标识 `20261029-gamepads-headsets`。早期 v26–v34 是历史开发编号，不是比 v1.0.2 更新的正式版本。


**作者：雪落a · B站 ID：雪落a77**

[下载最新版本](https://github.com/Xueluoa1/ai-periph-monitor/releases/latest) · [软件操作手册](docs/USER_GUIDE.md) · [品牌型号适配](docs/SUPPORT.md) · [完整型号细表](docs/DEVICE_MATRIX.md) · [作者声明与致谢](AUTHOR_STATEMENT.md)

灵感来自 B站 **硅基SZ酱** 的视频和 [2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer)。在原有基础上优化 UI，扩展和完善蓝牙设备电量监测、2.4G 鼠标 DPI 设置、键鼠按键/滚轮与鼠标实际回报率测试、桌面挂件、任务栏显示、低电量提醒、浅色/暗色主题，并适配更多品牌型号。原项目已含蓝牙基础模块，本项目继续完善其集成与状态处理。

**源码完整公开，免费分享；后续更新随缘。** 采用 MIT 许可证。作者不希望被拿去售卖、改名换皮收费或付费解锁；这是作者倡议，不限制 MIT 已授予的商用与再分发权利。详见[声明](AUTHOR_STATEMENT.md)与[许可来源](NOTICE.md)。

## 功能一览

| 功能 | 说明 |
| --- | --- |
| 外设电量 | 已适配 2.4G / HID 设备，以及 Windows 提供电量属性的蓝牙设备；缓存和不可用状态明确显示 |
| XInput 手柄电量 | Windows 提供的空/低/中/满档位；不换算百分比，不参与百分比低电量提醒 |
| 外设配置 | 精确型号的 DPI 档位、回报率及部分灯光控制，写入后按通道读取确认 |
| 键鼠测试 | 按键高亮、点击次数、滚轮、同时按键峰值、鼠标实际输入 Hz；不是精确硬件延迟或键盘回报率测量 |
| 桌面挂件 | 独立选择设备、紧凑模式、尺寸、位置锁定、置顶、不透明度 |
| 任务栏电量 | 独立选择显示设备，数字电量图标与后台监测 |
| 提醒与主题 | 低电量阈值及通知测试，浅色/暗色即时切换 |
| 适配诊断 | 独立的只读多品牌采集工具，日志仅在本地生成 |

## 下载与运行

可在 [GitHub Releases](https://github.com/Xueluoa1/ai-periph-monitor/releases) 下载 `无线外设电量监视器-v1.0.2-便携版.zip`，解压后运行其中 EXE，无需安装 Python。诊断工具单独下载 `多品牌外设诊断工具-v1.5-便携版.zip`。

- 默认启动主界面和托盘；托盘、挂件及设备设置可在界面中管理。
- `--ui` 打开主界面；`--tray` 启动托盘；`--widget` 启动挂件。
- `--debugger` 打开协议调试界面，仅建议了解 HID 协议的开发者使用。
- `--once` 读取一次电量并退出。

运行环境：Windows 10/11。此次发布构建环境为 Windows x64、Python 3.14.6；源码使用 Python 标准库、`ctypes` 和 `tkinter`，不需要 hidapi、Pillow 或 pywin32。其他 Python 版本尚未在此次整理中验证。

## 设备适配范围

支持按型号、USB ID、接口及协议匹配，不是同品牌所有设备的通用驱动。识别品牌图标不代表已支持电量或设置。未知或异常回复不会伪装成有效百分比。

- 新增北通鲲鹏指定 SKU、飞智八爪鱼5/黑武士5 Pro、小鸡旋风2、八位堂指定蓝牙手柄，以及赛睿无线鼠标和 HyperX/赛睿/索尼耳机电量通道；本项目新增设备均待实机验证。详见[手柄与鼠标](docs/适配说明-无线手柄与鼠标.md)和[北通、八位堂与耳机](docs/适配说明-北通八位堂与耳机.md)。
- 新增 G-Wolves、赛睿、HyperX、NZXT、Keychron、Hitscan 的指定无线接收器电量查询，共 57 个 VID/PID 条目（不是 57 个独立型号），均待实机验证；不新增 DPI、回报率、灯效或有线直连适配。详见[无线电量扩展](docs/适配说明-无线电量扩展.md)。
- 新增 XInput 手柄电量档位与全品牌 Windows 蓝牙系统诊断，是否可读取决于系统上报；没有逐一实测全部型号。详见[用户型号核对表](docs/适配核对-用户型号清单.md)。
- 黑爵 AK820 Max Plus / XY 2.4G 键盘（1A2C:8FFF）：新增电量查询，待实机回复验证；不提供充电状态、灯效或回报率控制。共享 ID 不能唯一识别任意配对型号。
- 既有通道包括部分 ROG、ATK/VXE、怒喵、Logitech、Razer、MCHOSE 等设备。
- VXE R1 Pro Max 的此次修正有捕获报文回放依据。
- 小米电竞鼠标 2、VGN V98 电量、达摩鲨 M3 Pro 电量及 GravaStar K1 部分通道仍有待实机验证。
- 多彩 M618XSD、小鸡 G7 Pro 已实现专用电量查询，等待实机回复验证；JBL Smart Tx 已加入识别与诊断采集，尚未实现 USB 电量解码。RK-G98 2.4G 电量和灯效、西伯利亚 K03S 的专用电量协议仍未确认。

完整范围从[设备支持说明](docs/SUPPORT.md)阅读，最新变更见[更新日志](CHANGELOG.md)。自动化测试通过不代表所有设备均已实测。

## 从源码运行

在仓库根目录执行（Python 需包含 Tcl/Tk）：

```powershell
python app/launcher_main.py --ui
python tools/peripheral_diagnostic/peripheral_diagnostic.py --help
```

诊断工具无参数运行会实际读取设备并导出本地日志。详见[诊断说明](tools/peripheral_diagnostic/使用说明与适配指南.md)。

## 构建与验证

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts/run_tests.py
.\.venv\Scripts\python.exe app/build_ui_exe.py
.\.venv\Scripts\python.exe tools/peripheral_diagnostic/build_diagnostic.py
```

输出位于 `release/`。两项构建脚本只构建，不部署或更改自启动。重建可能产生不同的 EXE 哈希；发布文件以附带的 SHA256 校验值为准。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `app/` | 主程序、HID/蓝牙读取、协议适配、界面与回归测试 |
| `app/brand_assets/`、`app/fonts/` | 离线图标、字体及来源/许可证 |
| `tools/peripheral_diagnostic/` | 统一诊断采集工具及测试 |
| `tools/am_diagnostic/` | 怒喵诊断支持模块及测试 |
| `scripts/` | 独立测试运行入口 |
| `docs/` | 功能范围、适配说明、发布验证 |

## 本地数据与反馈

程序在本地保存配置和共享读取状态。诊断工具会导出设备资料、原始协议回复及错误信息，不自动上传；原始报文仍可能含设备标识。提交公开 Issue 前请检查并脱敏日志，不要直接公开整份诊断 ZIP。

## 第三方说明

协议参考、品牌标识及字体来源见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)、[品牌素材来源](app/brand_assets/品牌图标来源.md)和[字体许可证](app/fonts/OFL.txt)。品牌商标归各自权利人所有。本项目不是厂商官方工具。

项目代码采用 [MIT 许可证](LICENSE)。上游来源、保留署名及许可范围见 [NOTICE.md](NOTICE.md)，第三方素材继续适用各自条款。
