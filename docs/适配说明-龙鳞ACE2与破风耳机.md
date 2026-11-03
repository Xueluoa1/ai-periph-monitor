# 龙鳞 ACE 2 与破风 PELTA 电量适配

日期：2026-11-03。主程序 v1.0.2、诊断工具 v1.5.0，版本号不变；构建标识 `20261103-rog-harpe2-pelta`。

## 本次实际加入

| 用户确认型号 | 日志身份 | 电量接口 | 新增功能 |
| --- | --- | --- | --- |
| ROG 龙鳞 ACE 2 红色 | 0B05:1AD0，ROG SPEEDNOVA 8K RECEIVER | MI_02 / COL03，FF01:0001，64 字节，RID 03 | 自动读取百分比、充电状态，电压值有效时显示电压 |
| ROG 破风耳机 | 0B05:1B84，ROG PELTA (2.4GHz) | MI_03 / COL03，FF00:0001，64 字节，RID CC | 自动读取百分比，另行查询充电状态；单独的充电查询失败时保留有效电量 |

主程序、内置调试器和独立诊断工具共用同一读取协议。主页显示两个外设，不把 SPEEDNOVA 的空键盘通道列为另一台键盘，不向 PELTA 的 FF07／FF0B 接口套用鼠标命令。

接收器 ID 本身不能自动确定鼠标颜色；“红色”来自用户确认。SPEEDNOVA 接收器可能用于其他配对型号，名称保留接收器信息，不据此开放所有型号的配置写入。本次接入电量监测，未新增 DPI、回报率、灯效、音频、降噪或固件设置。

## 日志确认了什么

输入 ZIP：`外设诊断日志_20261101_144907_fa0303ce.zip`，旧诊断构建 `20261029-gamepads-headsets`。

日志确认了上述 VID/PID、产品名、集合、报告长度，以及鼠标 RID 03、耳机 RID CC 的 Input/Output 能力表。旧工具把两个型号都归入通用采集，没有发送对应电量 GET，因此日志没有可用于实机核对的电量百分比。监听到的鼠标 `05 12 01…` 是另一条状态报告，不能当成电量。

同一日志里的 AURA LED Controller、RYUJIN III EVA 水冷和主板 USB Audio 不属于这两台无线外设，不用它们的报文推测电量。PELTA 通用 Feature 快照中的设备信息也不是电量结果。

## 协议差异和读取规则

- 鼠标发送 `03 12 07` 并补齐 64 字节；回复第 5 字节为百分比，第 10 字节为充电状态（按包含 RID 的零起始索引）。
- PELTA 发送 `CC 12 07`；第 5 字节为休眠时间，**第 6 字节才是百分比**。不能套用鼠标偏移。`CC 12 08` 单独读取充电状态。
- 严格检查接口、报告 ID、长度、回复命令和百分比范围；请求回显、全零、FF AA 错误、截断及其他状态报告均不当成有效电量。
- 最多等待每条查询 2 秒。没有回复时显示未知／等待设备，不填入 0%。如果充电回复与全零请求完全相同，无法区分请求回显时保留充电状态未知。
- 只发送已确认的电量 GET，不进行模式切换、配置保存或固件操作。

## 验证范围与下一步

接口证据来自用户真实日志；电量格式依据公开协议实现独立编写。回归覆盖两个偏移、错误报文、空槽过滤、主页显示和诊断自动查询。测试中的百分比报文是合成样本，**不是从用户日志读出的电量，也不代表实机验证通过**。

退出旧主程序后运行同版本新 EXE，连接并唤醒鼠标和耳机，再刷新。需要核验实际读数时使用同版本更新后的诊断工具；它这次会自动发送上述电量查询并记录原始回包，不需要手动选择接口。可同时记录奥创／Gear Link 的电量与充电状态，供对照。

## 公开协议依据

- [G-Helper Harpe II Ace 型号、接口和 Report ID](https://github.com/seerge/g-helper/blob/main/app/Peripherals/Mouse/Models/HarpeIIAce.cs)
- [G-Helper 鼠标电量字段](https://github.com/seerge/g-helper/blob/main/app/Peripherals/Mouse/AsusMouse.cs)
- [G-Helper PELTA 型号](https://github.com/seerge/g-helper/blob/main/app/Peripherals/Headset/Models/Pelta.cs)
- [G-Helper 耳机电量与独立充电查询](https://github.com/seerge/g-helper/blob/main/app/Peripherals/Headset/AsusHeadset.cs)

仅按公开的报文事实实现；未打包第三方驱动，未执行下载源码。
