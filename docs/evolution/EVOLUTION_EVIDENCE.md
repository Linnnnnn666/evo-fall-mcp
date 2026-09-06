# EvoAgent 自进化档案 —— 证据链（公开版）

> "越用越好用"不是口号，是系统被设计成**可测量、可回滚**的验收标准。
> 本档案条目均可通过本仓库及姊妹仓库核实；运行时数据来自系统自己的 manifest 快照（见 `manifest-20260903.json`）。

## 一、进化时间线（git 可查证）

| 日期 | 进化事件 | 证据 |
|---|---|---|
| 08-18 | 固件开发"系统化"：配置化引导模板（双分区 OTA + 遥测） | evo-firmware `feat(board-template)` |
| 08-19 | 能力中枢诞生：MCP 服务器 + 工具工厂（首批 11 工具） | 本仓库 `feat(mcp)`（47 工具） |
| 08-19~20 | **第一轮进化闭环**：task-review 自动生成 query_a_share_index / query_system_status / query_board_telemetry | manifest `created_by=task-review` |
| 08-20 | 云端烧录板交付（节奏：1 天/板） | evo-firmware `feat(flasher)` |
| 08-21 | **智能体层进化**：DSH-2 生成 base64_codec / hash_string；plugin_requests 7 份需求 + 2 份病理样本 | manifest `created_by=DSH`；plugins/quarantine/ |
| 08-22 | 跌倒板固件交付（模板 + 工具 + 经验复用） | evo-firmware `feat(fall-board)` |
| 08-25 | 语音板从零 bring-up（5 小时，7 类 bug）→ 全量开源 | evo-voice-terminal |
| 08-26 | 双层自进化 + 插件装配文档 + 四层架构图 | 本仓库 |
| 08-27 | **最新一次进化**：task-review 生成 schedule_voice_announce（定时语音播报） | manifest `created_by=task-review` |
| 08-28 | keep-alive：维修闹钟闭环（服务器 404 → 心跳方案） | evo-voice-terminal `feat(keepalive)` |

## 二、工具层进化证据（manifest 实拍）

21 个动态工具，来源三分：**manual 12 / task-review（复盘自动生成）7 / DSH（进化者）2**。

- `query_board_telemetry`：复盘自动生成 → **真实被调用 19 次** → 一次进化，长期复用
- `schedule_voice_announce`：08-27 复盘生成，当天为"主动开口"能力补上工具位
- `base64_codec / hash_string`：DSH-2 隔离开发，健康检查后装入——智能体层进化的真实产物
- 累计 45 次工具调用记录在案（manifest.calls 总和）

## 三、智能体层进化证据（插件体系）

```
plugin_requests/req_*.json  ×7（DSH-1 复盘写的"能力缺口"）
   ↓ DSH-2 隔离开发
plugins/  base64-codec / reverse-string / text-stats
   ↓ 健康检查
装入 DSH-1；故障插件 → quarantine/（可回滚）
病理样本：req_conflict.json / req_invalid.json（证明隔离与校验真实存在）
```

## 四、开发效能进化证据

| 阶段 | 事实 | 证据 |
|---|---|---|
| 早期全手工 | 语音板 bring-up：5 小时，7 类 bug | 交接文档/博客稿 |
| 系统化后 | 每 1-2 天交付一个板卡固件 | evo-firmware git：08-18 模板/08-20 烧录板/08-21 OLED/08-22 跌倒板 |
| 持续迭代 | fall-board OTA v1.0.1 → v1.8.0（9 版）；全系统 23 次固件迭代 | OTA 版本目录 + latest.json + sha256 |

**解读**：速度提升不是"人变强了"，是系统把复用件（模板/工具/经验）固化，让新任务踩在旧任务的肩膀上。

## 五、一句话总结

> "系统进化的是能力容器（工具/插件/经验），不碰模型——可控、可解释、可回滚。证据就在这里：21 个工具里 9 个是系统自己生成的，一个被复用 19 次；DSH-2 隔离开发的插件装了能回滚；同样任务从 5 小时 bring-up 到 1-2 天交付。**'越用越好用'不是我说的，是数据说的。**"
