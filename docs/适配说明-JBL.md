# JBL Smart Tx：识别与验证采集

构建标识 `20261026-jbl-evidence`；监测程序 v1.0.2、诊断工具 v1.5.0 均不变。

## 已加入

- 日志 e35f371d 的 `0ECB:214B`，产品字符串 JBL Smart Tx，作为发射器显示一个设备项。FF13:0001 / Input、Output 62 / Feature 0 为主接口；FF14:0001 / Input、Output 512 / Feature 0 为辅助采集接口，不显示成另一副耳机。
- 监测程序显示“USB 耳机/发射器电量协议待验证”，电量为空，不显示猜测的 0%。该 ID 的路由在此结束，不会继续尝试怒喵等其他品牌协议。
- 诊断工具对这两个严格匹配的厂商接口分别最多监听 8 秒、256 帧、32 种报文；保留报告 ID 过滤、字段隐私检查和去重。以无写权限句柄打开，不发送猜测的 USB 命令。
- Smart Tx 存在时，诊断工具额外收集 Windows 已有的 JBL TOUR ONE M3 / Smart Tx 蓝牙电量属性；保留连接、缓存状态及匿名设备标识，单独列出，不将蓝牙设备自动关联到 USB 发射器。主程序原有蓝牙电量功能继续使用这些系统属性。

## 尚未加入

**没有实现 Smart Tx USB 电量百分比解码。** 用户旧日志只有接口资料，没有可核对的电量回复。不能把“识别与采集已加入”说成“电量协议已适配”。耳机与发射器分别有电池，USB 接收器的存在也不能证明某副耳机已连接。

核对过的公开资料：

- [JBL 官方说明书](https://www.jbl.com.sg/on/demandware.static/-/Sites-masterCatalog_Harman/default/dwd02ec98d/pdfs/LS_JBL_Smart%20Tx_QSG_Multilingual.pdf)：发射器本身有电池；未提供此 USB 接口的查询命令。
- [HaloBattery 的 Quantum 910 协议记录](https://github.com/HeyOkay/HaloBattery/issues/90)：针对 0ECB:2088 的主动 RID08 电量上报；本机日志的 214B 声明 RID07/RID16，不能套用该解码。
- [OpenJBL 协议记录](https://github.com/NiceDayZc/openjbl/blob/main/docs/PROTOCOL.md)：面向 JBL Portable 的 BLE/SPP 传输，不能据此把蓝牙帧直接发入 Smart Tx USB 接口。

## 实机验证

软件验证：360 项主程序测试、25 项通用诊断测试、22 项怒喵诊断测试，共 407 项回归通过；124 个 Python 文件语法检查通过。没有执行 JBL 实机电量验证。

把 Smart Tx 接到电脑并保持 TOUR ONE M3 开机，双击新版诊断工具，等待桌面日志生成。可在采集期间查看 Smart Tx 的耳机电量页面，记录当时耳机和发射器各自的电量；不必更改设备设置。若耳机已通过电脑蓝牙连接，可同时采集系统电量，但不要求改变现有配对。

无主动报文时，日志会明确记录“未收到主动报文”。这时仍需官方通信资料或受控抓包，不能由空日志推算电量协议。
