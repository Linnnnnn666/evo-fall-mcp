════════════════════════════════════════════════════════════════
  EvoAgent 系统全流程运作说明书（超详细版 · 2026-08-21）
  定位：新会话的"系统圣经"——读完即懂整个系统怎么运转
════════════════════════════════════════════════════════════════

════════════════════════════════════════════════════════════════
一、系统总览
════════════════════════════════════════════════════════════════
一句话：一个自然语言驱动的自进化硬件开发系统——用户说话，AI 系统
（DSH）负责写代码、造工具、编译固件、管理设备（OTA/烧录），并且
把每次干活的经验沉淀下来，能力越用越多。

六层架构（自上而下）：
  L1 用户层：人（说话/听播报/看 OLED/按物理键）
  L2 语音层：语音板 + xiaozhi-server（ASR/TTS/LLM 代理/服务员）
  L3 能力层：fall-mcp 能力中枢（28 工具 + 动态库 + 记忆 + 经验）
  L4 大脑层：DSH-1（干活）/ DSH-2（进化，隔离环境）
  L5 数据层：fall server API + PostgreSQL + mosquitto + OTA 仓库 + 注册表
  L6 设备层：跌倒板 / 烧录板 / 显示板 / 任意目标板

════════════════════════════════════════════════════════════════
二、组件详解（每个部分的作用与内部机制）
════════════════════════════════════════════════════════════════

【L1 用户】
- 说话下达意图（可以口误、含糊，ASR 容错 + LLM 推断）
- 听语音播报（平时安静，出错/完成才播报）
- 看 OLED（烧录进度实时显示，零打扰）
- 按物理键（BOOT=确认/切换，烧录等高危操作的双保险）

【L2 语音板（ESP32S3-N16R8，COMxx，evo-voice-v1 小安固件）】
- 麦克风+喇叭，唤醒词"你好小安"（或类似），本地 ASR 前端（esp-sr）
- 通过 WiFi 连 xiaozhi-server（:8001），WebSocket 音频流
- 语音板（evo-voice-v1）：唤醒「你好小安」，TTS 播报；OLED 显示已退役（板改造为语音板）

【L2 xiaozhi-server（:8001/:8003，systemd 守护）】
- 语音大脑：ASR 识别 → LLM 理解 → TTS 合成播报
- MCP 客户端：data/.config.yaml 里 mcp_endpoint: ws://127.0.0.1:8002/mcp/
  ——把 fall-mcp 的 28 个工具注入 LLM 提示词（LLM 可见即可调）
- 提示词 agent-base-prompt.txt：核心规则——ASR 容错（口误推断真实意图、
  绝不纠正用户发音）、输出语言强制、简洁直接、退出机制
- 上下文约束：max_prompt_message_length=50（只保留最近 50 条，
  防长会话撑爆 LLM 上下文——用户决策）
- MCP 客户端代码：core/providers/tools/device_mcp/
  （mcp_client/mcp_handler/mcp_executor，WebSocket JSON-RPC）

【L3 fall-mcp 能力中枢（:8002，systemd 守护）】
- MCP 服务器：ws://127.0.0.1:8002/mcp/，JSON-RPC 2.0
  （initialize → tools/list → tools/call），不是 HTTP 也不是 MQTT
- 28 工具 = 内置 12（fall×4 + dev×8）+ 动态 16（dynamic_tools/ 热加载）
- 长任务（dev_create_tool/dev_dispatch_task/dev_ota_deploy）异步：
  立即返回"已开始"，后台线程跑 DSH，dev_query_task 查进度
- 记忆层：manifest.json（工具注册表+调用统计）、evolution.log（进化流水）、
  task_results/（任务结果）、经验索引（bigram）
- 插件轮询器：每 10s 扫 plugin_requests/ → 有新需求自动造插件
- devices.json：设备注册表（型号/MAC/版本/能力/ota_verified）
- 无权限门控（规划 #10：开发者模式未启用时工具列表过滤）

【L4 DSH（DeepSeek Harness，两个 profile）】
- headless：DSH-1 干活用（工具生成/任务执行/固件迭代）
- headless-builder：DSH-2 进化用（造插件，空 patch + base/headless bundles
  + router + vision，不含业务插件——装坏不影响主系统，可回滚）
- 执行环境：nvm node 22（v22.23.2）、reasoningEffort=max
- 权限：任务以 DSH_PERMISSION_MODE=danger-full-access 运行（服务器内部可信）
- 传参方式：任务文本写临时文件，dsh --profile X "$(cat 文件)"——避免 shell 转义

【L3 dev_tool_factory.py（fall-mcp 同目录）】
- create_tool(requirement)：三路径——
  a) BUILD_PROMPT 直接 LLM 生成（要求：白名单 import、国内可达 API、
     浏览器 UA、异常全捕获、JSON 输出格式）
  b) 失败 → DSH headless agent（DSH_TASK_TEMPLATE，实测接口可达性）
  c) py_compile 验证 → 语法错误回喂 LLM 自修复（最多 3 轮）
- 落盘 dynamic_tools/<name>.py → 下次 tools/list 热加载发现
- _sync_manifest()：注册进 manifest + evolution.log + git commit
- 安全边界：import 白名单（json/re/httpx/…）、禁止本地文件/内网/shell/
  数据库、编译验证通过才注册
- dispatch_task(task)：bigram 匹配历史任务 → 有相关经验则追加
  【参考经验】段 → 后台 DSH headless 执行（2400s 超时）→ 结果写
  task_results/<id>.txt → 复盘可固化工具
- 经验索引细节：字符 bigram（不是词块——词块粒度太粗曾 0 命中），
  相似度阈值 ≥3；task_results 两种格式兼容（标准"任务:"与 DSH 覆写
  "【任务概述】"）

【L5 fall server API（:8000，docker server-api + postgres）】
- /api/v1/devices/{id}/events：跌倒事件上报/查询（require_app_write/read
  token 认证）
- /api/v1/devices/{id}/telemetry：遥测入库（7 天滚动，MQTT listener 订阅
  fall/telemetry/# 写入）
- /api/v1/devices/{id}/status、/heartbeat、OTA 模块
  （FirmwareRelease/OtaReport，rollout_percent 字段预留灰度）
- 认证：设备 token / APP 只读 token 分离（真实值仅存服务器环境变量/本地配置，不入库）

【L5 mosquitto（:1883，docker）】
- 账号与 ACL（关键约束，mosquitto.conf + acl 文件）：
  fall_pub：只写 devices/+/events/fall（服务器发跌倒事件）
  fall_sub：只读 devices/+/events/fall（语音板收跌倒）
  YOUR_MQTT_USER：fall/telemetry/# 读写 + fall/commands/# 读写
    + fall/flasher/# 读写（烧录事件，新加）
- 三通道：事件 devices/+/events/fall · 遥测 fall/telemetry/<id> ·
  命令 fall/commands/<id>（ota_check）
- 命令 QoS 0（丢了就靠板子定时自检兜底）

【L5 Caddy（:80，docker）】
- /firmware/* → /srv/firmware 静态文件（= /opt/ota 挂载）：
  /firmware/<device>/latest.json + /firmware/<device>/<version>/merged.bin
  通配——新板建目录即可，零配置
- /xiaozhi/* → 语音服务反代；/api/* → fall server
- gzip、nosniff 头

【L5 OTA 仓库（/opt/ota/<device>/）】
- v<版本>/：merged.bin（app）+ manifest.json + sha256.txt
- latest.json：{version, bin, size, sha256, full:{bin,size,sha256}}
  ——full.bin = merge_bin 全镜像（bootloader+分区表+otadata+app，
  烧录板首次烧录用）
- /opt/ota/devices.json：OTA 产物索引（与 fall-mcp 注册表互补）

【L6 固件（4 个工程，全在 /opt/firmware/）】
1. fall-board（跌倒板 v1.6.1）：雷达+视觉跌倒检测、双通道上报
   （HTTP API + MQTT 事件）、NVS 队列断网补传（指数退避 2s→5min）、
   心跳、SNTP、双分区 OTA、遥测 10s、命令订阅
2. board-template（通用引导固件）：boards/<id>.h 配置化
   （device_id/SSID/LED/版本）、network（WiFi STA 自动重连）、
   telemetry（MQTT 10s + 命令 ota_check）、ota（开机 30s + 每 6h 自检、
   esp_https_ota + 双分区回滚）、board_extra_json 业务扩展字段、
   sdkconfig 保守 DIO/40M/16MB
3. esp-flasher-proto（烧录板 v11）：esp-serial-flasher 2.0 组件
   （git clone 在 components/，port 打了 NC 引脚补丁）、WiFi 下载
   full.bin 流式烧录（4KB 块）、MD5 校验、MQTT 事件上报
   （ready/waiting/connected/progress/done/error）、GPIO48 LED、
   UART1=GPIO1/2、手动 BOOT+RST 进下载模式
4. oled-display（显示板）：MQTT 订阅 fall/flasher/events + SSD1306 HUD
   （等待/连接/进度条/完成/出错）、PSRAM OCT 配置、GPIO8/9 I2C
关键 sdkconfig（踩坑沉淀）：
  CONFIG_ESPTOOLPY_FLASHMODE_DIO + FLASHFREQ_40M（劣质 flash 兼容）
  CONFIG_FREERTOS_IDLE_TASK_STACKSIZE=4096（无下划线！烧录崩溃修复）
  CONFIG_ESP_MAIN_TASK_STACK_SIZE=12288
  CONFIG_SPIRAM=y + OCT（N16R8 8MB PSRAM）

════════════════════════════════════════════════════════════════
三、六大链路全流程（逐步详解）
════════════════════════════════════════════════════════════════

【链路 1 · 语音问答】（例："最近家里有人跌倒吗？"）
语音板 ASR → xiaozhi-server → LLM（带 fall-mcp 工具列表）→ 判断应调
query_fall_status → MCP ws 调用 fall-mcp → 内部 httpx 调 fall server
API（:8000，Bearer 设备 token）→ 返回 JSON → LLM 组织人话 → TTS 播报。
约束：上下文 50 条、输出语言强制、口误容错。

【链路 2 · 自进化造工具】（例："做个工具查B站播放量"）
语音 → dev_create_tool(description) → 立即回复"正在生成，约1-2分钟"
→ 后台 create_tool：LLM 生成 → 失败转 DSH → py_compile 验证 →
dynamic_tools/ 落盘 → manifest 注册 + evolution.log + git commit →
热加载 → 用户重新唤醒说需求 → 新工具可用。
约束：白名单 import、国内可达 API、禁止内网/文件/shell、自修复 3 轮。

【链路 3 · DSH 派活】（例："分析服务器磁盘占用"）
dev_dispatch_task(task) → bigram 匹配经验 → 注入【参考经验】→ 后台
DSH headless（临时文件传参，2400s 超时）→ task_results/ 落盘 →
dev_query_task 查询 → 可复盘造工具。
约束：danger-full-access、reasoningEffort=max。

【链路 4 · OTA 迭代】（例："跌倒后 LED 闪烁3次"）
dev_ota_deploy(requirement, board_id=fall-board) → 立即回复"约10-20分钟"
→ 后台 DSH：读代码→改→版本递增→idf.py 编译（失败自修5轮）→
归档 /opt/ota/fall-board/v1.7.0/（merged.bin+manifest+sha256）→
更新 latest.json → git commit → MQTT publish ota_check →
fall/commands/esp32s3-cam-01 → 板子收到 → ota_check_now() →
HTTP 拉 latest.json → fw_newer 比对（兼容 v 前缀）→ esp_https_ota
下载写另一分区 → 镜像校验 → mark_app_valid_cancel_rollback → 重启 →
新版本遥测上报 → 验证完成。
约束：双分区回滚（失败不变砖）、版本比较防自刷、ACL 通配命令主题。

【链路 5 · 云端烧录】（独立烧录板 + 语音板播报验收；OLED 显示板已退役）
v11 烧录板上电：WiFi → MQTT 连接 → 拉 /firmware/<id>/latest.json
（full 字段）→ 发 ready 事件 → 无限重试等待目标板（发 waiting）→
用户给目标板接 4 根线（GPIO1→RX/GPIO2→TX/3V3/GND）+ 手动 BOOT+RST
进下载模式 → 连接成功（发 connected）→ 流式下载 full.bin 边下边烧
（每 10% 发 progress{pct,written,total}，LED 快闪）→ flash_finish
MD5 校验 → 发 done{bytes} → 出错发 error{err}（LED 慢闪）。
OLED 显示板（已退役，板 2026-08-25 改造为语音板）：
HUD 渲染（等待/连接/FLASHING+进度条/完成/出错）。
约束：烧录板 v2 基线稳定内核（无 OLED/按键干扰）；显示板独立物理隔离；
事件 QoS 0（可重发）；目标板手动进下载模式（无自动控制线）。

【链路 6 · 新板接入】（完整状态机，最终形态）
登记（dev_register_board，4 步指引）→ 意图级复读确认（语音+BOOT 双保险）
→ DSH 建 boards/<id>.h 编译 full.bin → 烧录板执行（链路 5）→
引导固件自检上报（规划 #15）→ 遥测到达 → 注册表标记 ota_verified →
OTA 往返验证 → 可离线 → 功能迭代（链路 4）。
约束：验收标准=OTA 验证通过才算接入；ACL/OTA 路由/板规格动态发现
零配置；烧录失败可重试 3 次。

════════════════════════════════════════════════════════════════
四、约束与细节全集
════════════════════════════════════════════════════════════════
· MQTT ACL：四账号分权（见上），新板命令 topic 通配自动兼容
· 安全：敏感文件 600、设备/APP token 分离、工具 import 白名单、
  动态工具禁内网/文件/shell、DSH-2 隔离环境
· 超时：DSH 任务 2400s、HTTP 10-30s、MQTT keepalive 120s、
  连接重试无限（3s 间隔）、OTA 编译自修 5 轮
· 回滚：双分区 OTA 失败自动回滚；fw_newer 修复防每 6h 自刷
· 上下文：语音 50 条截断；经验注入相似度阈值 ≥3（bigram）
· 配置：DIO/40M、IDLE 栈 4096（STACKSIZE）、main 栈 12288、PSRAM OCT
· 已知缺口：烧录板/显示板无心跳（DSH 不知在线）、显示板不在注册表、
  无权限门控（#10）、无意图确认（#11）——都在路线图

════════════════════════════════════════════════════════════════
五、用户背后的考量（决策动机，全部来自真实对话）
════════════════════════════════════════════════════════════════
1. 自进化："缺什么自己造工具装上，下次直接调用，越来越贴合需求"
   → 工具工厂 + 经验索引 + DSH-2 造插件
2. DSH-2 隔离："装坏业务插件不影响 DSH-2，可回滚修复"
   → headless-builder 空 patch 独立环境
3. 验收标准："第一版没跑通 OTA，怎么做到一次上板其他全离线？"
   → OTA 往返验证是接入必做项（ota_verified 字段）
4. 监工模式："本地拉取和烧录不好，要有监工；DSH 全程在线监工，
   语音 LLM 是服务员转述，DSH 是厨师，用户是顾客"
   → 事件流水=唯一事实源、出错才播报、用户反馈通道（#9）
5. 复读确认："有可能说话嘴瓢，口齿不清，需要 LLM 理解需求并和用户确认"
   → 意图级复读确认 + BOOT 物理确认双保险（#11）
6. OLED："不必播报进度，加个 OLED 显示器，进度放 OLED，语音就出错再播报"
   → 显示板 HUD、语音零打扰
7. 物理确认："不在板旁时说确认/取消（语音确认备用）；在板旁直接按 BOOT"
   → 物理按键优先
8. 烧录/显示分离："是不是 OLED 占用过多导致栈溢出？如果是就不用 OLED
   接语音板，另外拿块板接 OLED"——崩溃实证后的根治决策
9. 开发者模式："未启用该模式时，LLM 无权调用 ESP 的生产工具"
   → 工具列表按模式过滤（#10）
10. 方向 B/C："经验质量标记""修复需人工确认"——进化有边界，可控
11. 英文界面："我不要中文了，换回英文吧，最后再做这方面的优化"
    → 中文优化留最后
12. 测试板3："好了，不要折腾这个板了，我们不用这个板了"
    → 效率优先，怪板弃用

════════════════════════════════════════════════════════════════
六、运维与故障排查速查
════════════════════════════════════════════════════════════════
服务检查：systemctl is-active fall-mcp xiaozhi-server
          docker ps（server-api/caddy/mqtt）
板子探测：esptool --chip esp32s3 -p COMx --before default_reset
          --after hard_reset chip_id（按 MAC 认板）
烧录板事件：docker exec mqtt mosquitto_sub -u YOUR_MQTT_USER
          -P 'YOUR_MQTT_PASSWORD' -t fall/flasher/events
常见坑：
  · 烧录后"静默"→ esptool run 复位或按 RST；read_serial 已修 DTR/RTS
  · 板子进下载模式出不来 → 拔 USB 断电 5s 重插（CH343 DTR 残留）
  · 命令收不到 → 查 ACL 是否通配、板子是否订阅成功
  · 版本不更新 → 查 fw_newer 比对、latest.json、板子 WiFi

════════════════════════════════════════════════════════════════
