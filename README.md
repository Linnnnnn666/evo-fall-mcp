# EvoAgent Capability Hub (fall-mcp)

EvoAgent 的能力中枢 —— 连接语音服务员（xiaozhi）、DSH（DeepSeek Harness 自动化智能体）与硬件设备的 MCP 服务器。47 个工具，覆盖部署/烧录/指令/接入/自验收/自进化/确认反馈全链路。

## 它在系统中的位置

```
用户语音 → 小安（语音板） → xiaozhi-server → MCP 工具调用 ──┐
                                                             ├→ fall-mcp（本仓库）
用户文字 → DSH（DeepSeek Harness）→ MCP 工具调用 ────────────┤
                                                             └→ MQTT/OTA/HTTP → ESP32 板卡
```

- **MCP 端点**：`ws://127.0.0.1:8002/mcp/`（JSON-RPC 2.0）
- **工具**：47 个（部署/烧录/指令/接入/回滚/自验收/门控/进化/查询）
- **自进化**：工具工厂 + 经验库（RAG）+ 自动复盘 + 失败聚类 + 插件进化

## 核心工具

| 工具 | 能力 |
|---|---|
| `dev_ota_deploy` | 固件迭代部署闭环：DSH 改码→编译→归档(app.bin)→自行发布 ota_check→OTA→**遥测自验收**→人在环确认→动态播报 |
| `dev_ota_rollback` | 一键回滚 OTA 版本（目标低于板子当前时提示需串口降级） |
| `dev_self_verify` | 功能级遥测自验收：断言字段（led/color/fw/temp…）通过即"已验证"，不问用户 |
| `dev_board_command` | 运行时指令（led_on/off/green_on/blue_on/ota_check），**按板卡能力白名单校验** |
| `dev_first_flash` | 新板全自动接入：配置→编译→烧录→遥测自检→注册→**接入档案沉淀** |
| `dev_flash_start/abort/status/supervise` | 云端烧录执行与监工 |
| `dev_enable/disable_developer_mode` | **开发者模式门控**：语音启用后才开放开发工具与板卡告警播报 |
| `dev_speak` / `dev_talk_to_dsh` / `dev_query_confirm` | 厨师开口 / 反馈通道 / 确认查询（人在环） |
| `dev_create_tool` / `dev_delete_tool` | 工具工厂：DSH 自造工具（失败自动播报原因） |
| `query_board_telemetry` / `dev_list_boards` | 板卡状态查询（遥测 API 失效时本地事件流兜底） |
| `query_fall_*` / `query_*` | 跌倒监测 / 通用查询（动态工具） |

## 后台线程

| 线程 | 职责 |
|---|---|
| confirm poller | 10s 轮询确认队列 → 语音播报（每轮 1 条/1h 过期/去重） |
| flash monitor | MQTT 监听：事件/日志/遥测 → 事件流 + 在线状态 |
| offline alert | 板子掉线 5 分钟提醒 + 恢复播报（30 分钟限频，**开发者模式门控**） |
| telemetry health | 重启（豁免 OTA）/内存/信号异常告警 |
| dsh task guard | >45 分钟任务进程清理 |
| plugin poller | 插件需求 + 健康检查 |
| thread watchdog | 30s 检测线程死亡自动重启 |

## 数据模型

- `flash_events.jsonl`：唯一事实源（遥测/事件/**命令溯源** direction=cmd）
- `devices.json`：设备注册表（runtime_commands 能力感知）
- `confirm_queue/<id>.jsonl`：人在环确认队列
- `task_results/`：任务结果 + 经验库（RAG 索引 + 置信度）
- `developer_mode.json`：开发者模式状态

## 快速开始

```bash
pip install paho-mqtt httpx websockets dashscope
cp .env.example .env   # 填写 MQTT/API keys（凭据全部环境变量化）
cp devices.example.json devices.json
python3 mcp_server.py  # 监听 ws://127.0.0.1:8002/mcp/
```

## 安全设计

- 凭据全部 env 注入（`.env`），代码零硬编码
- **开发者模式门控**：开发工具 + 板卡告警播报仅在语音启用后开放；查询/工具工厂/语音链路永开
- 命令白名单 + 板卡能力感知（runtime_commands），LLM 无法虚构能力
- MCP 工具异常返回错误文本（不崩连接）；未知工具返回 isError

## License

MIT © 2026 EvoAgent
