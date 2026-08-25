# DSH 双层自进化装配指南（DSH_EVOLUTION_SETUP）

本文档说明如何搭建 EvoAgent 的第二层自进化（智能体自进化）：
**DSH-1 干活、DSH-2 进化**的双角色架构，以及插件的开发→安装→隔离→回滚全流程。

> 前置：已部署 fall-mcp（见仓库 README 快速开始）；DSH = DeepSeek Harness（第三方工具，自行安装）。

## 一、双角色架构

| 角色 | profile | 职责 | 隔离级别 |
|------|---------|------|---------|
| DSH-1（干活者） | `headless` | 执行任务：改代码/编译/部署/排障 | 生产环境，受保护 |
| DSH-2（进化者） | `headless-builder` | 开发插件、安装/修复插件 | 独立 profile，装坏不影响 DSH-1 |

```
DSH-1 干完活
   ├─ 复盘① 任务复盘 → 值得固化？→ 工具工厂造 MCP 工具（系统层进化）
   └─ 复盘② 能力缺口 → 写 plugin_requests/req_*.json
                              │
                              ▼
         fall-mcp plugin-poller 线程检测到需求
                              │
                              ▼
         DSH-2（headless-builder）开发插件
                              │
                              ▼
         装入 DSH-1（cordis.patch.yml 注册）→ 健康检查
                              │ 失败
                              ▼
                    修复 / 移入 plugins/quarantine/
```

## 二、Profile 装配（~/.dsh/profiles/）

DSH 的 profile 是"空根 + 补丁树"结构：

```
~/.dsh/profiles/
├── headless/                  # DSH-1 干活者
│   ├── cordis.yml             # 空根（[]）——不要直接编辑
│   ├── cordis.patch.yml       # 插件注册入口（insert 条目）
│   ├── package.json           # bundles 声明（dsh.profile.bundles）
│   └── node_modules/          # 运行时依赖
└── headless-builder/          # DSH-2 进化者（隔离环境）
    ├── cordis.yml             # 空根
    ├── cordis.patch.yml       # builder 自举条目（router-bootstrap 等）
    └── ...
```

**关键机制**：`cordis.yml` 是空条目列表，实际装配由补丁叠加：
`package.json 的 bundles → cordis.patch.yml → --patch 命令行覆盖`。
所以插件的安装 = 在 DSH-1 的 `cordis.patch.yml` 增加一条 `insert`，卸载/隔离 = 移除该条。

### 安装一个插件的 patch 示例

```yaml
# ~/.dsh/profiles/headless/cordis.patch.yml
- insert:
    - id: base64-codec
      name: /opt/dsh-plugins/base64-codec.mjs   # 插件文件路径
      config: {}
```

### 隔离（装坏时）

1. 从 `cordis.patch.yml` 移除该插件的 `insert` 条目（保持 DSH-1 可用）
2. 插件文件移入 `/opt/dsh-plugins/quarantine/`（仓库中对应 `plugins/quarantine/`）
3. DSH-2 修复后重新走安装流程

## 三、插件格式

插件是 ESM 模块，约定导出：

```js
export const name = 'base64-codec'   // 插件名
export const inject = ['tools']      // 注入点（tools = 作为工具暴露）
```

DSH 运行时按 patch 加载后，插件能力即出现在 DSH-1 的工具列表中。

## 四、完整闭环验证

1. DSH-1 执行一个需要"文本统计"能力的任务（如分析日志）
2. 观察 `plugin_requests/` 出现新需求文件（capability/tools/task_type/urgency）
3. 插件轮询器自动拉起 DSH-2（日志：`[plugin-poller] DSH-2 done ...`）
4. 产物出现在 `/opt/dsh-plugins/`（本仓库 `plugins/` 有真实样例）
5. DSH-1 后续任务可直接调用该能力

## 五、常见问题

- **Q：DSH-2 会看到其他需求文件吗？** A：任务模板明确约束"只处理指定需求文件"，
  多需求并行时互不干扰（每个 DSH-2 实例只读自己的 req 文件）。
- **Q：插件装坏了主系统会崩吗？** A：不会——DSH-2 隔离开发 + patch 可回滚 +
  quarantine 隔离区，主系统最多短暂失去该能力，不会崩溃。
- **Q：需要人工介入吗？** A：默认全自动；涉及破坏性/业务影响的操作由人在环确认
  （fall-mcp confirm 队列 → 语音板播报 → 用户确认）。
