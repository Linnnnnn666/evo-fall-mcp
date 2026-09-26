# plugins/ — 智能体自进化的实物产物

这是**第二层自进化（DSH 插件进化）的真实产出**：DSH-1（干活者）在任务中发现自己
缺少某些能力 → 写插件需求文件 → DSH-2（进化者，隔离环境）开发出这些插件 → 装入 DSH-1。

## 插件清单

| 插件 | 能力 | 由谁制造 | 产物大小 |
|------|------|---------|---------|
| `base64-codec.mjs` | base64 编解码（字符串/对象入参兼容） | DSH-2（headless-builder） | 1.9 KB |
| `reverse-string.mjs` | 字符串反转 | DSH-2 | 1.7 KB |
| `text-stats.mjs` | 文本统计（字符/单词/行数/频率） | DSH-2 | 2.6 KB |
| `ld6002b-frame.mjs` | **LD6002B 毫米波雷达 TinyFrame 帧解析与校验**：校验 SOF/长度/双校验和（XOR 取反），payload 按小端 float32 解码为点云 | DSH-2 | 18.6 KB |

> `ld6002b-frame.mjs` 是最近一次进化（2026-09-27）的产物，对应**真实工程痛点**：
> 跌倒板 bring-up 时雷达 UART 丢字节导致帧错位，需要一个能把十六进制帧解析成
> 结构化字段、并定位校验失败的调试工具。
> 它由 DSH-2 在**市场优先策略**下先搜索开源实现、无果后自制，自带协议帧结构文档与
> 消息类型表（`0x0201 control` / `0x0202 set_area` …），零第三方依赖、无网络与文件操作。

## 插件的形态

每个插件是一个 ESM 模块，导出 `name` 与 `inject` 声明：

```js
export const name = 'base64-codec'   // 插件名（注入 DSH 工具列表）
export const inject = ['tools']       // 注入点：作为工具暴露给 DSH-1
```

DSH-2 开发完成后，通过 profile patch（见 `docs/DSH_EVOLUTION_SETUP.md`）
把插件装入 DSH-1 的运行时，健康检查通过即生效。

**安全约束（DSH-2 必须遵守）**：零第三方依赖；`execute` 只做纯计算、字符串/JSON 处理
或公开 HTTP API 调用；禁止 shell 执行、文件写入/删除；产出前自检 `node --check` 与
危险模式扫描（`child_process` / `fs.write` / `eval` 等）。

## requests/ —— 进化的输入侧（需求文件）

`plugin_requests/` 目录是本仓库从运行时镜像过来的需求文件，展示进化的**触发端**：

| 文件 | 能力 | 结果 |
|------|------|------|
| `req_1787143294.json` | BASE64 编解码 | done · **自制** · `base64-codec` |
| `req_1787145804.json` | 二维码生成 | done · 市场安装（`dsh-qrcode`） |
| `req_1787147406.json` | MD5/SHA 哈希 | done · 市场安装（`@deepseek-ai/dsh-tool-encoding`） |
| `req_1787147991.json` | 文本统计 | done · **自制** · `text-stats` |
| `req_1787160375.json` | 字符串反转 | done · **自制** · `reverse-string` |
| `req_1790446719.json` | LD6002B 帧解析 | done · **自制** · `ld6002b-frame` |
| `req_conflict.json` | （病理样本）冲突需求 | **rejected** —— 校验拦下 |
| `req_invalid.json` | （病理样本）无效需求 | **rejected** —— 校验拦下 |

> 两个 `rejected` 的病理样本是**校验机制真实存在**的证据：需求进入时先做字段校验
> （工具名格式、desc 必填）与工具名冲突检测，不合格的直接拒绝，不浪费 DSH-2 的算力。
>
> 注意「市场安装」的那两条：DSH-2 的策略是**先找现成轮子，找不到才自己造**——
> 这比无脑自制更克制。本仓库只收录自研产物（`plugins/*.mjs`），市场插件不在此目录。

## quarantine/ —— 进化失败的隔离区

装坏过的插件会被移入此目录（可回滚，不影响主系统）。
`quarantine/` 保留空目录作为机制说明：**进化实验允许失败，失败不污染主系统**。

## 如何复现这层进化

1. 部署 fall-mcp（见 README 快速开始），配置 DSH headless / headless-builder 双 profile
2. DSH-1 干活时遇到能力缺口 → 自动写 `plugin_requests/req_*.json`
3. 插件轮询器（10 秒周期）检测到需求 → 启动 DSH-2 隔离开发 → 产物放本目录 → 装入 DSH-1
4. 完整装配指南见 `docs/DSH_EVOLUTION_SETUP.md`；
   一次完整的端到端进化实录（含时间线与验证证据）见 `docs/DSH_UPGRADE_20260927.md`
