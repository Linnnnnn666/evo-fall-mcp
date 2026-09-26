# EvoAgent 自进化档案 —— 证据链（公开版）

> "越用越好用"不是口号，是系统被设计成**可测量、可回滚**的验收标准。
> 本档案条目均可通过本仓库及姊妹仓库核实；运行时数据来自系统自己的脱敏快照
> （`evolution-log-20260927.txt` 事件流 + `manifest-live-20260927.json` 工具注册表；
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

## 二、工具层进化证据（manifest 实拍）

**21 个动态工具**，来源三分：**manual 12 / task-review（复盘自动生成）7 / DSH（进化者）2**。

- `query_board_telemetry`：复盘自动生成 → **真实被调用 19 次** → 一次进化，长期复用
- `schedule_voice_announce`：复盘生成，当天为"主动开口"能力补上工具位
- `base64_codec` / `hash_string`：**智能体层进化的产物**——前者由 DSH-2 隔离开发后装入；
  后者当时由 DSH-2 按"市场优先"策略从开源市场选定并安装
- 累计 **45 次工具调用**记录在案（manifest.calls 总和）

## 三、智能体层进化证据（插件体系）

```
plugin_requests/req_*.json  ×8（DSH-1 复盘写的"能力缺口"）
   ↓ 字段校验 + 工具名冲突检测（不合格直接 rejected，不消耗 DSH-2）
插件轮询器（10 秒周期）→ DSH-2 隔离开发（先搜市场，无果才自制）
   ↓
plugins/  base64-codec · reverse-string · text-stats · ld6002b-frame
   ↓ 语法检查 + 危险模式扫描 + 健康检查
装入 DSH-1；故障插件 → quarantine/（可回滚）
病理样本：req_conflict.json / req_invalid.json（证明隔离与校验真实存在）
```

**四次进化的产物规模**：1.9 KB / 1.7 KB / 2.6 KB / **18.6 KB**（最近这次 `ld6002b-frame`
自带协议帧结构文档与消息类型表；DSH-2 先搜市场无果后自制，并做了正例+反例+边界用例自验证）。

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
| 需求准入 | 字段校验（工具名格式 / desc 必填）+ 工具名冲突三表检测 → 不合格直接 rejected |
| 制作约束 | 零第三方依赖；禁止 shell / 文件写入 / 环境变量读取；产出前 `node --check` + 危险模式扫描 |
| 安装原子性 | 先备份 patch → 安装锁 → 临时文件原子替换 → YAML 合法性校验 → 失败回滚重试（≤3 轮） |
| 装后保障 | 独立线程健康检查 DSH-1；异常则自动修复，修不动交 DSH-2 兜底；坏插件移入 `quarantine/` |
| 审计 | 全部事件写 `evolution.log`（谁发起、生成了什么、调用几次）；工具目录本身是 git 仓库，每次注册自动 commit |

> 已知不足（如实记录）：插件注册表落盘与"安装成功"审计事件尚缺（详见
> `docs/DSH_UPGRADE_20260927.md` 第五节），不影响功能，正在修。

## 六、一句话总结

> "系统进化的是能力容器（工具/插件/经验），不碰模型——可控、可解释、可回滚。
> 证据就在这里：**21 个动态工具里 9 个是系统自己生成的，其中一个被复用 19 次；
> 四次插件进化都是 DSH-2 在隔离环境做出来的，装了能回滚；同样类型的板卡任务，
> 从 5 小时 bring-up 到 1-2 天交付**。'越用越好用'不是我说的，是数据说的。"
