# EvoAgent 自进化档案 —— 证据链（公开版）

> "越用越好用"不是口号，是系统被设计成**可测量、可回滚**的验收标准。
> 本档案条目均可通过本仓库及姊妹仓库核实；运行时数据来自系统自己的脱敏快照
> （`evolution-log-20260928.txt` 事件流 + `manifest-live-20260928.json` 工具注册表；
> 快照由系统内的导出脚本定期生成，路径/凭据/密钥自动脱敏）。

## 一、进化时间线（git 可查证）

| 日期 | 进化事件 | 证据 |
|---|---|---|
| 08-18 | 固件开发"系统化"：配置化引导模板（双分区 OTA + 遥测） | evo-firmware `feat(board-template)` |
| 08-19 | 能力中枢诞生：MCP 服务器 + 工具工厂（首批 11 工具） | 本仓库 `feat(mcp)` |
| 08-19~20 | **第一轮进化闭环**：task-review 自动生成 query_a_share_index / query_system_status / query_board_telemetry | manifest `created_by=task-review` |
| 08-20 | 云端烧录板交付（节奏：1 天/板） | evo-firmware `feat(flasher)` |
| 08-21 | **智能体层进化**：DSH-2 产出 base64-codec；plugin_requests 需求文件 + 2 份病理样本 | manifest `created_by=DSH`；plugins/quarantine/ |
| 08-22 | 跌倒板固件交付（模板 + 工具 + 经验复用） | evo-firmware `feat(fall-board)` |
| 08-25 | 语音板从零 bring-up（5 小时，7 类 bug）→ 全量开源 | evo-voice-terminal |
| 08-26 | 双层自进化 + 插件装配文档 + 四层架构图 | 本仓库 |
| 08-27 | task-review 生成 `schedule_voice_announce`（定时语音播报） | manifest `created_by=task-review` |
| 08-28 | keep-alive：维修闹钟闭环（服务器 404 → 心跳方案） | evo-voice-terminal `feat(keepalive)` |
| 09-03 | 语音板唤醒词与人设统一 | evo-voice-terminal `docs` |
| **09-27** | **DSH 升级（0.1.5-rc.3）+ 收敛标准模式 + 第四次插件进化**：DSH-2 产出 `ld6002b-frame`（雷达帧解析），并完成端到端闭环验证 | `docs/DSH_UPGRADE_20260927.md`、`plugins/ld6002b-frame.mjs`、`plugin_requests/req_1790446719.json` |
| **09-28** | **审计缺口修复 + 第五次插件进化**：补上插件注册表落盘与 `plugin_installed` 安装审计事件（并回填 4 个已装插件），随后 DSH-2 产出 `evolution-log-summary`（进化日志摘要）验证新链路端到端可用 | `docs/AUDIT_GAP_FIX_20260928.md`、`plugins/evolution-log-summary.mjs`、`plugin_requests/req_1787900001.json`（新增病理样本）、`plugin_requests/req_1787900002.json` |

## 二、工具层进化证据（manifest 实拍）

**21 个动态工具**，来源三分：**manual 12 / task-review（复盘自动生成）7 / DSH（进化者）2**。

- `query_board_telemetry`：复盘自动生成 → **真实被调用 19 次** → 一次进化，长期复用
- `schedule_voice_announce`：复盘生成，当天为"主动开口"能力补上工具位
- `base64_codec` / `hash_string`：**智能体层进化的产物**——前者由 DSH-2 隔离开发后装入；
  后者当时由 DSH-2 按"市场优先"策略从开源市场选定并安装
- 累计 **45 次工具调用**记录在案（manifest.calls 总和）

## 三、智能体层进化证据（插件体系）

```
plugin_requests/req_*.json  ×10（DSH-1 复盘写的"能力缺口"）
   ↓ 字段校验 + 工具名冲突检测（不合格直接 rejected，不消耗 DSH-2）
插件轮询器（10 秒周期）→ DSH-2 隔离开发（先搜市场，无果才自制）
   ↓
plugins/  base64-codec · reverse-string · text-stats · ld6002b-frame · evolution-log-summary
   ↓ 语法检查 + 危险模式扫描 + 健康检查
装入 DSH-1；同时写入 plugins.json 插件注册表 + 记 plugin_installed 审计事件
   → 故障插件 → quarantine/（可回滚）
病理样本：req_conflict.json / req_invalid.json / req_1787900001.json（证明隔离与校验真实存在）
```

**五次进化的产物规模**：1.9 KB / 1.7 KB / 2.6 KB / **18.6 KB** / 6.0 KB（最近这次
`evolution-log-summary` 对应审计痛点——安装审计事件补齐后，需要一个能把 `evolution.log`
读薄的复盘工具；它自己就是这套审计能力的第一个使用者。DSH-2 同样是先搜市场无果后自制）。

## 四、开发效能进化证据

| 阶段 | 事实 | 证据 |
|---|---|---|
| 早期全手工 | 语音板 bring-up：5 小时，7 类 bug | 交接文档 / 博客稿 |
| 系统化后 | 每 1-2 天交付一个板卡固件 | evo-firmware git：08-18 模板 / 08-20 烧录板 / 08-21 OLED / 08-22 跌倒板 |
| 持续迭代 | fall-board OTA v1.0.1 → v1.8.0（9 版）；全系统 23 次固件迭代 | OTA 版本目录 + latest.json + sha256 |

**解读**：速度提升不是"人变强了"，是系统把复用件（模板/工具/经验）固化，让新任务踩在旧任务的肩膀上。

## 五、运行时安全与可审计

| 机制 | 说明 |
|---|---|
| 需求准入 | 字段校验（工具名格式 / desc 必填）+ 工具名冲突三表检测（内置 / **已装插件** / MCP）→ 不合格直接 rejected |
| 制作约束 | 零第三方依赖；禁止 shell / 文件写入 / 环境变量读取；产出前 `node --check` + 危险模式扫描 |
| 安装原子性 | 先备份 patch → 安装锁 → 临时文件原子替换 → YAML 合法性校验 → 失败回滚重试（≤3 轮） |
| 装后保障 | 独立线程健康检查 DSH-1；异常则自动修复，修不动交 DSH-2 兜底；坏插件移入 `quarantine/` |
| 审计 | 全部事件写 `evolution.log`（谁发起、生成了什么、**装上了什么**、调用几次）；工具目录本身是 git 仓库，每次注册自动 commit |

### 已闭环的缺口：插件注册表落盘 + 安装审计（2026-09-28）

这条曾作为"已知不足"如实公开。现补上并给出实证（完整记录见
[`docs/AUDIT_GAP_FIX_20260928.md`](../AUDIT_GAP_FIX_20260928.md)）：

| 项 | 修复前 | 修复后 |
|---|---|---|
| 插件注册表 `plugins.json` | **从未被写入**（`_save_plugins` 有实现、零调用点） | 每次安装成功自动落盘，已回填 4 个历史插件 |
| 安装审计事件 | 只有 `plugin_req_started`，没有"装好了" | 新增 `plugin_installed` / `plugin_register_warn` |
| 工具名冲突检测（"已装插件"这张表） | 恒为空集 → **拦不住** | 生效 —— 重复能力需求 5 秒内被 rejected |
| 防重复投递 | 恒为空集 → 会重复构建 | 生效（同一事实来源） |

**登记以"文件真实存在"为准**：需求标了 `done` 但插件文件已被删除的（如早先从市场安装、
后来按用户要求清理掉的 `dsh-qrcode` / `dsh-tool-encoding`），会被拒绝登记并记
`plugin_register_warn` —— 注册表反映**已装现实**，不是纸面声明。

> 仍如实记录的边界：① 危险模式扫描用**裸子串**匹配，`"exec("` 会误报正则的
> `.exec(` 调用（已知误报，尚未精细化，保守起见宁可误报）；② 修好之后"重复投递"会先被
> 工具名冲突检查拦成 `rejected`，`plugin_req_skipped` 这条路径对精确同名场景更难触发；
> ③ 系统 node 18 与 DSH 所需 node ≥20 的分工（见 `docs/DSH_UPGRADE_20260927.md` 第五节）。

## 六、一句话总结

> "系统进化的是能力容器（工具/插件/经验），不碰模型——可控、可解释、可回滚。
> 证据就在这里：**21 个动态工具里 9 个是系统自己生成的，其中一个被复用 19 次；
> 五次插件进化都是 DSH-2 在隔离环境做出来的，装了能回滚；同样类型的板卡任务，
> 从 5 小时 bring-up 到 1-2 天交付**。'越用越好用'不是我说的，是数据说的。"
