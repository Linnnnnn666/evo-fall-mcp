════════════════════════════════════════════════════════════════
  能力中枢与核心链路解析（2026-08-21 实测版）
  覆盖：工具全集 / 自进化造工具 / DSH 派活 / 语音链路 / OTA 迭代 / 接线无线分流
════════════════════════════════════════════════════════════════

一、能力中枢工具全集（实测 :8002，共 28 个）
────────────────────────────────────────────────────────────────
接入方式：**WebSocket MCP**（ws://127.0.0.1:8002/mcp/，JSON-RPC 2.0
protocol，initialize → tools/list → tools/call）。**不是 HTTP REST**，
也不是 MQTT。语音板链路经 xiaozhi-server 的 mcp_endpoint 走同一个 ws。

【内置 12 个】（fall_mcp_server.py 硬编码 TOOLS）
跌倒类（fall×4）：
  query_fall_status / query_device_status / query_fall_history / query_fall_stats
  参数：device_id/hours/days/limit——走 fall server API（HTTP :8000）
开发类（dev×8）：
  dev_create_tool   工具工厂（description → LLM 生成工具）
  dev_delete_tool   删除自定义工具
  dev_dispatch_task 任务派发（task → DSH headless 执行）
  dev_query_task    查询任务状态/结果
  dev_list_tools    工具注册表（含调用次数）
  dev_register_board 登记新板（4 步 next_steps 指引）
  dev_list_boards   列出所有板（含 current_version/ota_verified）
  dev_ota_deploy    固件迭代（requirement + board_id，多板化）

【动态 16 个】（dynamic_tools/*.py 热加载，DSH 造的）
  query_a_share_index（A股）· query_bili_up_info / query_bili_video_stats（B站）
  · query_gold_price / query_silver_price（金银）· query_btc_price
  · query_usd_cny_rate（汇率）· query_douban_movie（电影）
  · query_github_user / github_repo_info（GitHub）· query_ip_location
  · query_system_status（服务器状态）· query_YOUR_MQTT_USER（设备遥测）
  · hash_string / base64_codec（本地计算，DSH-2 造的插件）
  · ask_user_requirement（需求引导问答）

调用方式：tools/call {"name": "...", "arguments": {...}} → 返回
{"content":[{"text":"JSON 字符串"}]}。长任务（dev_create_tool/
dev_dispatch_task/dev_ota_deploy）是**异步**：立即返回"已开始"，后台
DSH 执行，用 dev_query_task 查询进度。

二、自进化造 MCP 工具（完整闭环）
────────────────────────────────────────────────────────────────
1. 需求进入：语音"帮我做个工具查XXX" → xiaozhi LLM 调 dev_create_tool
2. 后台 DSH：dev_tool_factory.create_tool() → 3 条路径依次尝试：
   a. 直接 LLM 生成（BUILD_PROMPT，要求国内可达 API、白名单 import）
   b. 失败 → DSH headless agent（DSH_TASK_TEMPLATE，实测接口可达性）
   c. 编译验证（py_compile）→ 语法错误反馈 LLM 自修复（最多 3 轮）
3. 落盘：写入 dynamic_tools/<name>.py → 热加载（下次 tools/list 自动发现）
4. 注册记忆：_sync_manifest() → manifest.json 记录
   {name/description/file/version/created_by/calls} + evolution.log 写
   "tool_created" + git commit（回滚基础）
5. 复用：语音直接说需求 → xiaozhi LLM 看到工具 → 调用 → _record_tool_call()
   更新统计
安全边界：import 白名单（标准库+httpx）、禁止本地文件/内网/shell、
生成后编译+热加载验证才注册、失败反馈 LLM 自修复。

三、DSH 干活（dispatch_task 派发流程）
────────────────────────────────────────────────────────────────
1. dev_dispatch_task(task) → dev_tool_factory.dispatch_task()
2. 经验注入：字符 bigram 相似度匹配 task_results/ 历史任务 → 找到相关
   经验则追加【参考经验】段（实测：上证指数任务 12 分命中注入）
3. 后台 DSH：_dsh_cmd(task, profile="headless") 构造 dsh 命令
   （nvm node 22 环境 + DSH_PERMISSION_MODE=danger-full-access）
   任务通过临时文件传参避免转义（dsh --profile headless "$(cat 文件)"）
4. 结果：写入 task_results/<task_id>.txt（两种格式兼容：标准"任务:"
   与 DSH 覆写的"【任务概述】"）→ dev_query_task 读取
5. 复盘：DSH 完成可调用 dev_create_tool 把方法固化成工具（自进化闭环）

四、语音链路（完整调用链）
────────────────────────────────────────────────────────────────
语音板(COMxx) → xiaozhi-server(:8001/:8003) →
  data/.config.yaml：mcp_endpoint: ws://127.0.0.1:8002/mcp/
  （max_prompt_message_length: 50 = 上下文截断 50 条）
→ fall-mcp(:8002) 工具列表注入 LLM 提示词
→ LLM 决策调工具 → 结果回填对话 → TTS 播报
提示词基础：agent-base-prompt.txt（ASR 容错规则：用户口误要推断真实
意图、输出语言强制、简洁直接）。xiaozhi 侧 MCP 客户端代码在
core/providers/tools/device_mcp/（mcp_client/mcp_handler/mcp_executor）。

五、OTA 迭代链路（dev_ota_deploy 全流程）
────────────────────────────────────────────────────────────────
1. 需求 → dev_ota_deploy(requirement, board_id)
2. 后台 DSH（OTA_DEPLOY_TEMPLATE 参数化）：
   读目标工程代码 → 最小改动 → 版本递增（version_file 的宏）
   → idf.py 编译（IDF 5.5.4，IDF_SKIP_CHECK_SUBMODULES=1）
   → 失败自修最多 5 轮 → 成功归档：
     /opt/ota/<device>/v<新版本>/merged.bin + manifest.json + sha256.txt
     → 更新 latest.json → git commit
3. 发布：MQTT publish "ota_check" → fall/commands/<mqtt_device_id>
   （YOUR_MQTT_USER 账号，ACL 已通配）
4. 目标板：收到命令 → ota_check_now() → HTTP 拉 latest.json →
   版本比对（fw_newer，兼容 v 前缀）→ esp_https_ota 下载写入另一分区
   → 镜像校验 → mark_app_valid_cancel_rollback → 重启
   （失败自动回滚，双分区兜底）
5. 板子启动新版本 → 遥测上报 fw 版本 → 验证完成
   （board-template 版：开机 30s + 每 6h 自动检查；命令随时触发）
产物路径：/opt/ota/<device>/latest.json {"version","bin","size","sha256",
"full":{"bin":"full.bin",...}}；full.bin = merge_bin 全镜像（烧录板用）。

六、接线/无线分流（现状与缺口——演示时第一个可能发现的坑）
────────────────────────────────────────────────────────────────
设计意图：DSH 判断"改功能走 OTA（无线）还是需要物理接线"。
判定依据（现状）：
  · devices.json 的 ota_verified=true → 该板可无线 OTA
  · 遥测 last_seen 新鲜 → 板子在线
  · 功能涉及新外设/新引脚 → 需要接线
【已知缺口】（如实记录）：
  1. 烧录板（v11）和显示板**不报遥测**（只发 flasher 事件），devices.json
     里没有它们的在线状态——DSH 无法知道它们是否在线
  2. 烧录板不报"目标板已连接"以外的目标在线信息（如目标板 MAC/固件状态）
  3. 显示板不在注册表（dev_list_boards 看不到）
  → 演示接线/无线分流前需补：烧录板/显示板心跳上报（可复用
    fall/telemetry/<id> 或新增 fall/flasher/status），注册表登记两块板
    ——这是路线图 #7（执行器化）的组成部分

════════════════════════════════════════════════════════════════
新会话演示建议顺序：先 tools/list 看 28 工具 → 用 dev_list_tools 看统计
→ 造一个真实小工具验证自进化闭环 → dispatch_task 派任务看经验注入 →
OTA 链路用 board-s3-36ac 或显示板演示（跌倒板不在手边）
→ 接线/无线分流前先补心跳缺口。
════════════════════════════════════════════════════════════════
