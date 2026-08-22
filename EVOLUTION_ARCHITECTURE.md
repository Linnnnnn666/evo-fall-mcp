════════════════════════════════════════════════════════════════
  EvoAgent 项目演进史与详细架构（完整版 · 2026-08-21）
  用途：让新会话完整理解"我们从哪来、为什么这样设计、系统长什么样"
════════════════════════════════════════════════════════════════

════════════════════════════════════════════════════════════════
第一部：思想演进史（逐步迭代的完整脉络）
════════════════════════════════════════════════════════════════

【第 0 步 · 起源】
智慧养老跌倒报警系统（任务书《服务器化与OTA工程迁移任务书》）：跌倒板
（ESP32-S3-Cam 雷达+视觉）检测跌倒 → 报警 → 家人/语音板通知。三硬件：
跌倒板 + 小智语音板（ESP32S3-N16R8）+ 自有服务器（阿里云）。

【第 1 步 · 转型：语音交互 → 自进化系统】
用户明确："这不是一个简单的语音交互项目"。
构想：自然语言（语音为主）驱动 DSH（DeepSeek Harness AI 开发环境）：
  · DSH 写程序/写工具/造 MCP 工具供语音消费
  · 固件可无线 OTA 下载到任意 ESP32 板
  · 系统缺什么自己造（工具工厂），经验入库，越用越贴合用户
决策：一个 DSH 安装、两种任务角色——DSH-1 干活（固件迭代等）、
DSH-2 插件进化（headless-builder 隔离环境，装坏插件不影响主系统）。
三个方向：A 多设备泛化、B 记忆深化（经验索引+质量标记）、C 自主性升级
（轻量调度器模拟主管，修复需人工确认）。

【第 2 步 · 多板接入实战（方向 A）】
接入第二块板（COMxx 新板，MAC xx:xx:xx:xx:xx:xx）：
  · 建 board-template 通用引导固件（配置化 boards/*.h + build_board.sh）
  · 踩坑：qio/80M 起不来 → 根因是 bootloader 编译配置（esptool 参数只改
    header）→ sdkconfig.defaults 固化 DIO/40M 全量重编 → 启动成功
  · 踩坑：工程缺 network 模块（lwIP 未初始化，MQTT 一连接就 assert）→
    新增轻量 network.cpp
  · 多板化基础设施：devices.json 注册表、dev_register_board/dev_list_boards
    工具、dev_ota_deploy 多板化（BOARD_SPECS 动态发现 boards/ 目录）、
    mosquitto ACL 通配 fall/commands/#、OTA 路由 /firmware/<id>/ 通配
  · 用户提出验收标准："遥测通不算接入完成，OTA 往返验证通过才算"
    → 固化了 dev_register_board 的 4 步 next_steps + ota_verified 字段
  · 踩坑：fw_newer 版本比较 bug（无 v 前缀解析失败 → 每 6h 自刷）→
    双工程修复 + 实测"已是最新"不再重复 OTA

【第 3 步 · 板烧板设想（烧录器项目启动）】
用户构想：语音板从云端取固件，通过串口给新板首次烧录（摆脱电脑）。
确认可行（esp-serial-flasher 官方组件，ESP32 互烧有先例）。
阶段1：读验证（连接/识别/读 flash 对比基准）——用跌倒板当烧录器原型，
  目标板实验板，手动 BOOT+RST 进下载模式；esp32_port.c 打了 NC 引脚补丁
阶段1b：写验证（擦除/写入/MD5/回读一致）
阶段2：云端烧录闭环（WiFi 拉 latest.json → 流式下载 full.bin → 串口烧录
  → MD5）——v2 固件在测试板2 验证 100% PASS
架构演进（用户逐步提出，最终定型）：
  · 最初：语音板本地拉取+本地烧录（后来否定：缺"监工"）
  · 监工模式：DSH 全程在线，语音 LLM 是服务员/转述者，DSH 是厨师，
    用户是顾客；"服务员传话、厨师干活、出错才开口"
  · 复读确认：高危操作意图级复述（防嘴瓢）+ BOOT 物理确认双保险
· 播报策略：语音播报进度与结果（OLED 显示板已退役）
  · 开发者模式权限门控：未启用时 LLM 看不到生产工具
  · 最终决策：烧录板与显示板物理分离（见第 4 步原因）

【第 4 步 · 崩溃诊断 → 架构定型的决定性事件】
OLED 版烧录固件（v5-v10）在烧录中反复崩溃：
  · 现象：i2c handle not initialized + IDLE1 任务栈溢出 + HTTP event 失败
  · 排查：backtrace 只到调度器；IDLE 栈配置名写错
    （CONFIG_FREERTOS_IDLE_TASK_STACKSIZE 才是对的，带下划线无效）；
    v10 完全禁 OLED 仍中断；v2（无 OLED）100% PASS
  · 结论：OLED/I2C 驱动与烧录高压并发（UART 高速 + HTTP + WiFi）导致
    崩溃，且全新 sdkconfig 也有嫌疑
  · 用户拍板：**OLED 不接烧录板，独立显示板**（后来升级为语音板+OLED）
  · 最终架构：烧录板 = v2 基线稳定内核 + MQTT 事件上报；
    显示板 = 独立板订阅事件渲染 HUD；两块板零引脚接触
  · v11 烧录板完成（事件 ready/waiting 实测到达服务器）
  · 显示板固件完成（MQTT 订阅 + SSD1306 HUD；PSRAM 版）
  · 测试板3 弃用（挑固件怪板：出厂/v1.6.1 能跑，自编固件不启动，
    原因未查明——PSRAM/容量/复位均排查过，用户决定不折腾）

【第 5 步 · 指令执行器化（2026-08-21）】
  · OLED 接线验证完成：SDA=GPIO8/SCL=GPIO9，启动日志 "OLED 初始化完成
    (0x3C, SDA=8 SCL=9)"；模拟事件序列 HUD 全流程（WAITING→CONNECTED→
    进度条→DONE）目视通过
  · 端到端烧录演示：跳过（无可用目标板，有板后可补）
  · 烧录板 v12（#7 MQTT 指令执行器）：订阅 fall/commands/flasher-board，
    flash_start(device|url+size)/abort/status；开机不再自动烧录；
    日志回传 fall/flasher/log（自带日志 tee + 烧录后目标板 UART 回显 60s）；
    错误路径统一回 idle 并补发 ready；实测 8/8 PASS
  · 修复 2 bug：cJSON use-after-free（flash_start 全报 no_latest 元凶）、
    错误路径不回 idle 不补 ready（显示板会卡 ERROR）
  · 注册表 devices.json 更新（board-s3-36ac=烧录板 v12 + oled-display 新增）

════════════════════════════════════════════════════════════════
第二部：详细架构（当前实现 + 最终形态）
════════════════════════════════════════════════════════════════

一、物理层（硬件清单）
────────────────────────────────────────────────────────────────
| 板 | 角色 | 固件/工程 | 关键点 |
|---|---|---|---|
| 跌倒板 | 跌倒检测 | fall-board v1.6.1 | 双分区 OTA、MQTT 三通道、NVS 队列 |
| 测试板1 | 云端烧录板 | esp-flasher-proto **v12** | v2 基线+指令执行器+事件+日志回传+GPIO48 LED |
| 测试板2 | 语音板（evo-voice-v1） | xiaozhi v2.4.2 干净重建 | 唤醒「你好小安」；由 OLED 显示板改造（2026-08-25） |
| 语音板（旧） | 已退役 | 旧固件 | 由测试板2 替代 |
| 目标板 | 任意新 ESP32 | board-template 引导固件 | 白片→OTA 全无线 |

二、网络层（MQTT 主题协议）
────────────────────────────────────────────────────────────────
| 主题 | 方向 | 用途 |
|---|---|---|
| devices/+/events/fall | 服务器发布 | 跌倒事件（fall_pub→fall_sub） |
| fall/telemetry/<id> | 板上报 | 遥测（10s，fw/uptime/rssi/led） |
| fall/commands/<id> | 服务器下发 | ota_check 等命令（烧录板 v12: flash_start/abort/status） |
| fall/commands/flasher-board | 服务器下发 | 烧录板 v12 指令通道（flash_start{device\|url,size}/abort/status） |
| fall/flasher/events | 烧录板发布 | 事件流：ready/waiting/connected/progress{written,total}/done{bytes}/error{err}/status |
| fall/flasher/log | 烧录板发布 | 日志回传：{"device":"flasher-board","line":"..."}（含 TGT> 目标回显） |
HTTP：/firmware/<id>/latest.json（含 full 字段）· /firmware/<id>/full.bin
ACL：YOUR_MQTT_USER 通配 fall/#（telemetry/commands/flasher 读写）

三、云端层（服务与工具）
────────────────────────────────────────────────────────────────
· DSH-1 / DSH-2（headless-builder 隔离）
· fall-mcp（:8002）：内置 TOOLS（fall×4+dev×6 含 dev_ota_deploy 多板化/
  dev_register_board/dev_list_boards）+ 动态工具库（13+）+ 记忆层
  （manifest/evolution.log/经验 bigram 索引）+ 插件轮询器 + devices.json
· xiaozhi-server（:8001/:8003）· fall server API（:8000 容器）
· Caddy（:80）· mosquitto（:1883 容器）· systemd 守护
· /opt/ota/<id>/：版本目录 + merged.bin + manifest.json + latest.json + full.bin

四、固件层（工程清单）
────────────────────────────────────────────────────────────────
· /opt/firmware/fall-board：跌倒检测（雷达+视觉+双通道上报+NVS 队列）
· /opt/firmware/board-template：通用引导固件（boards/<id>.h 配置化，
  network/ota/telemetry 模块，双分区 OTA，保守 flash DIO/40M）
· /opt/firmware/esp-flasher-proto：烧录板 v12（esp-serial-flasher 2.0 组件
  git clone 在 components/，port NC 补丁，指令执行器+事件+日志回传+LED）
· /opt/firmware/oled-display：显示板（复用 oled.c/h + network.cpp/h，
  MQTT 订阅 + HUD 状态机，PSRAM OCT 配置）
· 关键 sdkconfig：DIO/40M 保守 flash、IDLE 栈 4096（STACKSIZE）、
  main 栈 12288、N16R8 开 PSRAM

五、流程层（关键状态机）
────────────────────────────────────────────────────────────────
新板接入（最终形态 8 步）：意图确认 → DSH 编译 → 烧录指令 →
  烧录板执行（事件流）→ 显示/播报 → DSH 监工 → 自检验证 → 迭代
烧录板流程（v12 已实现）：WiFi → MQTT → 订阅 fall/commands/flasher-board →
  flash_start{device} → 拉 latest.json 解析 → waiting（连目标重试，abort 可中断）
  → connected → 流式烧录（10% 事件，abort 可中断）→ MD5 → done
  → 目标日志回显 60s（TGT>）→ ready 回空闲；错误统一回 idle 并补发 ready
显示板流程（已实现）：WiFi → MQTT 订阅 → 事件解析 → HUD 渲染
  （等待/连接/进度条/完成/出错）

六、决策记录（为什么这样设计）
────────────────────────────────────────────────────────────────
1. DSH 双角色隔离（DSH-2 装坏插件不影响主系统）
2. 经验索引用字符 bigram（词块粒度太粗曾 0 命中）
3. 烧录板独立 + v2 基线（OLED/I2C 并发崩溃实证，物理隔离根治）
4. 显示板独立 + MQTT 事件驱动（解耦，语音板后续兼显示）
5. 事件流水 = 唯一事实源（双 LLM 记忆都从它读）
6. 意图级复读确认 + BOOT 物理确认（防嘴瓢/幻觉，双保险）
7. 开发者模式门控（工具列表按模式过滤，LLM 看不到=调不了）
8. 监工模式（DSH 决策、固件执行、LLM 转述三层分离）
9. 引导固件配置化 boards/*.h + 动态发现（新板零代码接入）
10. 保守 flash DIO/40M 编译进 bootloader（劣质 flash 兼容）

七、路线图
────────────────────────────────────────────────────────────────
✅ 阶段0-5、方向A、板烧板读写、云端烧录、烧录/显示分离、v11+显示固件、事件协议
✅ OLED 接线验证（2026-08-21）· ✅ #7 MQTT 指令执行器化（v12，2026-08-21）
⏸️ 跳过：端到端烧录演示（无目标板，有板后可补）
📌 #8 DSH 监工循环 → #9 dev_speak/dev_talk_to_dsh
   → #10 开发者模式门控 → #11 意图确认 → #15 自检上报
   → #16 语音板集成（需用户确认断语音服务）→ #17 云端编排
   → #18 防呆全量 → #19 运行时配网
⏸️ 暂缓：#12 MAC 核对 / #13 断点续烧 / #14 错误码体系 / 界面中文化

════════════════════════════════════════════════════════════════
新会话开场：先读 /opt/fall-mcp/HANDOVER.md 的"开场 checklist"
探测服务/板子/接线，再按本文档理解背景与架构。
════════════════════════════════════════════════════════════════
