# EvoAgent — 自进化的 AI 硬件开发系统

> **一句话**：用语音或文字指挥 AI 智能体，为 ESP32 板卡完成「写固件 → 编译 → OTA 部署 → 遥测验收 → 经验沉淀」的完整开发闭环——**核心链路已验证、架构完整、持续迭代中的系统原型**，双层自进化，人在环兜底。

```
                              ┌──────────────┐
                              │     用户     │
                              └──────┬───────┘
                语音「你好小安」        │       文字（DSH 会话）
                     │               │              │
                     ▼               ▼              ▼
        ┌──────────────────┐  ┌────────────────────────────┐
        │ 语音链路         │  │ 智能体层                    │
        │                  │  │ ┌──────────────────────┐   │
        │ 语音板           │  │ │ DSH-1 干活者         │    │
        │ (evo-voice-      │  │ │ 写代码/编译/部署/排障 ◄── ┼── 插件装入
        │  terminal)       │  │ └──────────┬───────────┘    │
        │   │ WS/opus      │  │            │ 能力缺口       │
        │   ▼              │  │            ▼                │
        │ xiaozhi-server   │  │ ┌──────────────────────┐    │
        │ ASR→LLM→TTS      │  │ │ DSH-2 进化者          │   │
        └────────┬─────────┘  │ │ (隔离环境开发插件)     │──┼── req_*.json
                 │            │ └──────────────────────┘    │
                 │ 工具调用    └────────────────────────────┘
                 ▼
        ┌───────────────────────────────────────────────────┐
        │ 能力中枢 (fall-mcp) —— 47 工具                    │
        │ 部署/烧录/播报/自验收/门控/工具工厂/经验库/插件轮询│
        └───────┬──────────────────────────┬────────────────┘
                │ MQTT / HTTP / OTA        │ 遥测 · 事件回流
                ▼                          ▲
        ┌───────────────────────────────────────────────┐
        │ 硬件层 —— ESP32 板卡                          │
        │ 跌倒检测板 · 云端烧录板 · 业务板（OTA 双分区） │
        └───────────────────────────────────────────────┘

   进化回流：工具工厂/经验库 → 注入下一次任务 · DSH-2 插件 → 装入 DSH-1
   人在环：关键决策经语音板播报确认（confirm 队列）——AI 全自动不可信
```

## 系统的灵魂：双层自进化

**这不是一个"用 AI 写固件"的项目，而是一个"AI 自己给自己升级能力"的系统。**
系统不仅越用越熟练（经验沉淀），还能**自己发现自己缺什么能力、自己把能力造出来装上**——两层进化闭环，各司其职，互为增强。

### 第一层 · 系统自进化 —— 进化"手"（工具与经验）

系统层的进化发生在能力中枢：每干完一次活，自动复盘两件事。

```
DSH-1 干完活
   └─ 复盘①：这个需求以后还会遇到吗？
        ├─ 会 → 工具工厂：直接生成 MCP 工具（编译+验证通过才注册，不合格进不了库）
        └─ 不会 → 说明"无需固化"
   同时：任务结果 → 经验库（bigram 索引）→ 下次相似任务自动注入【参考经验】
```

真实案例：用户说"查一下银价" → 系统生成 `query_silver_price` 工具 → 验证注册 →
之后语音直接调用；"接入一块新板" → 从零建出 board-template 通用引导固件 →
之后每块新板复用模板（**固件能力模板化**，硬件的"长出手"）。

### 第二层 · 智能体自进化 —— 进化"大脑"（DSH 插件）

DSH 是双角色设计：**DSH-1 干活，DSH-2 进化**。DSH-1 干完活不只复盘任务，
还会复盘**自己的能力缺口**——这层进化让"大脑"本身的能力集变多。

```
DSH-1（干活者，headless profile）
   └─ 复盘②：这次遇到自己能力不足了吗？（如解析 PDF/Excel、缺某种 DSH 工具）
        ├─ 会再遇到 → 不硬造！写插件需求文件 plugin_requests/req_*.json
        │     （capability / 建议工具 / task_type / urgency）
        └─ 不需要 → 说明"无需插件需求"
              │
              ▼
   plugin-poller（后台线程）检测到需求
              │
              ▼
   DSH-2（进化者，headless-builder 隔离 profile）
        → 在隔离环境开发插件（/opt/dsh-plugins/）
        → 装入 DSH-1（cordis.yml 注册）
        → 健康检查 + 扫描验证
        → 装坏了？DSH-2 修复：最小改动 + 坏插件移入 quarantine/ 隔离区
```

真实案例：DSH-1 干活时发现需要 base64/字符串反转/文本统计能力 → 写需求 →
DSH-2 造出 `base64-codec` / `reverse-string` / `text-stats` 三个插件装入 →
DSH-1 之后自带这些能力（**quarantine/ 隔离区里躺着装坏过的插件——可回滚的进化**）。

> 📦 **实物都在本仓库**：插件产物见 [`plugins/`](plugins/README.md)（DSH-2 真实制造的进化成果）；
> 双角色装配全流程见 [`docs/DSH_EVOLUTION_SETUP.md`](docs/DSH_EVOLUTION_SETUP.md)。

### 双层闭环（合起来看）

```
       用户需求
          │
          ▼
   ┌─────────────┐   复盘① 值得固化？ ──是──► 工具工厂（编译+验证）──► 能力中枢 47+ 工具
   │   DSH-1     │                             ▲
   │   干活者     │──── 干完活 ──► 经验库（bigram）└── 下次自动注入经验
   └──────┬──────┘
          │ 复盘② 能力缺口？
          │    │ 是 → 写 req_*.json
          ▼    ▼
   ┌─────────────┐   隔离环境开发 ──► 装入 DSH-1 ──► 健康检查
   │   DSH-2     │    （装坏→修复）      │              │
   │   进化者     │                      ▼              ▼
   └─────────────┘               DSH-1 能力+1      quarantine/ 隔离
```

**层间关系**：DSH-2 给 DSH-1 造插件（大脑变强）→ DSH-1 干得更好 →
任务复盘产出更多工具与经验（手变强）→ 系统整体能力螺旋上升。
**进化的是能力容器（工具/插件/经验），不碰模型**——可控、可解释、可回滚。

### 为什么不会失控（三层保险）

1. **隔离**：DSH-2 在独立 profile 开发插件，装坏不影响主系统；坏插件移入 `quarantine/` 可回滚
2. **验证**：工具生成后必须编译+验证才注册；插件安装后健康检查 + 扫描
3. **人在环**：关键决策仍由人确认（见下）——AI 全自动不可信，人在环是信任底座

## 两个支撑信念

1. **人在环验收（AI 可靠性的底座）**
   系统先自己验：遥测字段断言（如 `led_color=#0000FF`），验不了/验不过才语音问你。修复循环最多 3 轮，再不行求助人类。

2. **AI 协作开发（工程师的进化方向）**
   AI 写代码、编译、归档、部署；人类负责架构、硬件驱动、全链路排障、最终验收。这个仓库群就是这套协作模式的完整实践。

## 仓库地图（三件套）

| 仓库 | 角色 | 一句话 |
|------|------|--------|
| **[evo-firmware](https://github.com/Linnnnnn666/evo-firmware)** | 硬件端 | ESP32-S3 固件集合：跌倒检测板（端侧 AI）、云端烧录板、配置化引导固件 |
| **[evo-fall-mcp](https://github.com/Linnnnnn666/evo-fall-mcp)** | 能力中枢 | MCP 服务器（47 工具）：部署/烧录/播报/自验收/自进化，连接 AI 与硬件 |
| **[evo-voice-terminal](https://github.com/Linnnnnn666/evo-voice-terminal)** | 语音入口 | 语音板板卡包：唤醒「你好小安」→ 语音对话 → TTS 播报 |

**本仓库是其中的「能力中枢」**——AI 与硬件之间的"手"：所有部署、烧录、播报、验收、进化动作都通过这里的工具完成。

---

# EvoAgent Capability Hub (fall-mcp)

连接语音服务员（xiaozhi）、DSH（DeepSeek Harness 自动化智能体）与硬件设备的 MCP 服务器。47 个工具，覆盖部署/烧录/指令/接入/自验收/自进化/确认反馈全链路。

## 一次完整的迭代，系统内部发生了什么

```
DSH 改完 C++ 代码
   │ dev_ota_deploy
   ▼
编译 → 归档 app.bin → 发布 ota_check（MQTT）
   │
   ▼
板卡 OTA 升级重启 → 10s 遥测回流
   │ dev_self_verify（断言 led_color / fw 版本…）
   ├─ 通过 → 「已验证」，不问用户
   └─ 不通过/验不了 → 语音问你（人在环）
           │
           ▼
   经验沉淀进知识库（下次同样的坑直接避开）
```

## MCP 端点与工具

- **MCP 端点**：`ws://127.0.0.1:8002/mcp/`（JSON-RPC 2.0）
- **工具**：47 个，分三类——
  - **核心工具**（生产链路验证）：部署/烧录/播报/自验收/门控/进化——`dev_ota_deploy`、`dev_self_verify`、`dev_board_command`、`dev_first_flash`、`dev_flash_*`、`dev_speak`、`dev_enable/disable_developer_mode`、`dev_create_tool` 等
  - **查询工具**：`query_fall_*`（跌倒监测）、`query_board_telemetry`（板卡状态）及系统/外部数据查询
  - **动态生成工具**（AI 自造、随需注册，示例性）：如 `query_silver_price` 等——验证"工具工厂"机制的实物

> 成熟度说明：核心工具经生产链路实测；查询/动态工具覆盖典型场景，属持续迭代中的原型组件。

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

> 分层入口：**[0 层 · 先看效果](#)**（演示视频，规划中）· **[1 层 · 纯软件 5 分钟跑通](#快速开始)**（本段，无需硬件）·
> **[2 层 · 单板体验](#)**（烧录一块 ESP32-S3）· **[3 层 · 完整系统](docs/QUICK_START.md)**（服务器+中枢+固件+语音板全链路）

### 1 层：纯软件 5 分钟跑通（无需硬件）

```bash
# 1. 依赖（实测清单）
pip install paho-mqtt httpx websockets dashscope pyyaml aiohttp

# 2. 配置（凭据全部环境变量化）
cp .env.example .env          # 填 MQTT/API keys（无 MQTT 时查询类工具也能用）
cp devices.example.json devices.json

# 3. 启动能力中枢（MCP 服务器）
python3 mcp_server.py         # 监听 ws://127.0.0.1:8002/mcp/，47 个工具

# 4. 验证：用任意 MCP 客户端调用只读工具
#    例如 dev_list_boards（设备注册表）/ query_system_status（服务器状态）
```

**真实调用示例**（MCP JSON-RPC 2.0）：

```json
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
← {"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"fall-monitor-mcp","version":"1.0.0"}}}

→ {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
← {"jsonrpc":"2.0","id":2,"result":{"tools":[{...47 个工具...}]}}

→ {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"dev_list_boards","arguments":{}}}
← {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"ok\":true,\"devices\":[{\"device_id\":\"board-xxx\",\"model\":\"...\",\"capabilities\":[\"ota\",\"telemetry\"]}]}"}]}}
```

5 分钟内：起服务 → 看到 47 个工具 → 调用只读工具拿到真实返回——**不需要任何硬件**。

### 3 层：完整系统

从零搭建「服务器 + 能力中枢 + 固件 + 语音板」全链路见 **[docs/QUICK_START.md](docs/QUICK_START.md)**（分步指南，含每步耗时与最低硬件要求）。

## 安全设计

- 凭据全部 env 注入（`.env`），代码零硬编码
- **开发者模式门控**：开发工具 + 板卡告警播报仅在语音启用后开放；查询/工具工厂/语音链路永开
- 命令白名单 + 板卡能力感知（runtime_commands），LLM 无法虚构能力
- MCP 工具异常返回错误文本（不崩连接）；未知工具返回 isError

## 迭代历史

- `feat(mcp)` 能力中枢核心：MCP 服务器 + 工具工厂 + 线程看门狗（08-19）
- `feat(tools)` 动态工具集 + 配置示例（凭据 env 化）（08-21）
- `docs` 架构文档：自进化六层/人在环验收/开发者模式门控（08-23）
- `chore` MIT 许可（08-25）

## License

MIT © 2026 EvoAgent
