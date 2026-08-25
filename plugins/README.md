# plugins/ — 智能体自进化的实物产物

这是**第二层自进化（DSH 插件进化）的真实产出**：DSH-1（干活者）在任务中发现自己
缺少某些能力 → 写插件需求文件 → DSH-2（进化者，隔离环境）开发出这些插件 → 装入 DSH-1。

## 插件清单

| 插件 | 能力 | 由谁制造 |
|------|------|---------|
| `base64-codec.mjs` | base64 编解码（字符串/对象入参兼容） | DSH-2（headless-builder） |
| `reverse-string.mjs` | 字符串反转 | DSH-2 |
| `text-stats.mjs` | 文本统计（字符/单词/行数/频率） | DSH-2 |

## 插件的形态

每个插件是一个 ESM 模块，导出 `name` 与 `inject` 声明：

```js
export const name = 'base64-codec'   // 插件名（注入 DSH 工具列表）
export const inject = ['tools']       // 注入点：作为工具暴露给 DSH-1
```

DSH-2 开发完成后，通过 profile patch（见 `docs/DSH_EVOLUTION_SETUP.md`）
把插件装入 DSH-1 的运行时，健康检查通过即生效。

## quarantine/ —— 进化失败的隔离区

装坏过的插件会被移入此目录（可回滚，不影响主系统）。
`quarantine/` 保留空目录作为机制说明：**进化实验允许失败，失败不污染主系统**。

## 如何复现这层进化

1. 部署 fall-mcp（见 README 快速开始），配置 DSH headless / headless-builder 双 profile
2. DSH-1 干活时遇到能力缺口 → 自动写 `plugin_requests/req_*.json`
3. 插件轮询器检测到需求 → 启动 DSH-2 隔离开发 → 产物放本目录 → 装入 DSH-1
4. 完整装配指南见 `docs/DSH_EVOLUTION_SETUP.md`
