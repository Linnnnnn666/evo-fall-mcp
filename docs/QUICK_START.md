# EvoAgent 完整系统搭建指南（QUICK_START）

> 从零搭建 EvoAgent 全链路：服务器 → 能力中枢 → 固件 → 语音板。
> 分层入口：0 层看效果 · 1 层纯软件 5 分钟（见 evo-fall-mcp README）· 2 层单板体验 · 3 层完整系统（本文档）。

## 分层总览

| 层 | 目标 | 需要 | 耗时 |
|----|------|------|------|
| 0 | 先看效果：演示视频/架构图 | 无 | 1 分钟 |
| 1 | 纯软件跑通能力中枢（MCP 47 工具） | 一台 Linux/Windows 电脑 | 5 分钟 |
| 2 | 单板体验：一块 ESP32-S3 跑起来 | 一块 ESP32-S3 开发板 | 2 小时 |
| 3 | 完整系统：服务器+中枢+固件+语音板 | 云服务器 + 2~3 块板卡 | 1~2 天 |

---

## 第 0 层 · 先看效果

- 架构图与系统说明：各仓库 README 顶部
- 演示视频：（规划中）

## 第 1 层 · 纯软件 5 分钟

见 [evo-fall-mcp README「快速开始」](https://github.com/Linnnnnn666/evo-fall-mcp)：
起 MCP 服务 → 看到 47 个工具 → 调用只读工具拿到真实返回。无需硬件。

## 第 2 层 · 单板体验（跌倒板，推荐先做）

1. 准备：一块 ESP32-S3（N16R8，8MB PSRAM）+ ESP-IDF v5.5.4 环境
2. 克隆固件：`git clone https://github.com/Linnnnnn666/evo-firmware`
3. 编译跌倒板：
   ```bash
   cd evo-firmware/fall-board
   cp main/device_config.h.example main/device_config.h   # 填 WiFi/服务器地址
   idf.py set-target esp32s3 && idf.py build
   ```
4. 烧录：`idf.py -p COMxx flash monitor`
5. 验证：串口看到 WiFi 连接 + 10s 遥测日志；有 LD6002B 雷达则插上（UART1：TX=GPIO1/RX=GPIO2），
   观察站立/倒地状态机日志 `[RADAR] Standing baseline acquired` / fall 触发
6. （可选）OTA：服务器归档 app.bin + latest.json，向板子发 `ota_check` 命令

## 第 3 层 · 完整系统（服务器 + 中枢 + 固件 + 语音板）

### 3.1 服务器准备（阿里云/任意 Linux，约 30 分钟）

| 组件 | 用途 | 端口 |
|------|------|------|
| mosquitto | MQTT broker（板卡遥测/命令） | 1883 |
| Caddy | 反向代理 + 静态文件（/firmware/* OTA 产物） | 80/443 |
| xiaozhi-server | 语音服务（ASR/LLM/TTS，WebSocket） | 8001/8003 |
| fall-mcp | 能力中枢（本系统核心） | 8002 |

```bash
# MQTT
apt install mosquitto mosquitto-clients
# 配置 ACL：允许板卡遥测/命令主题（fall/#），创建用户（凭据放 .env）

# Caddy
apt install caddy
# Caddyfile：reverse_proxy /xiaozhi/* → :8001；handle /firmware/* → 静态目录
```

### 3.2 部署能力中枢（约 15 分钟）

```bash
git clone https://github.com/Linnnnnn666/evo-fall-mcp
cd evo-fall-mcp
pip install paho-mqtt httpx websockets dashscope pyyaml aiohttp
cp .env.example .env            # MQTT 凭据 / FALL_APP_TOKEN / 服务器地址
cp devices.example.json devices.json
python3 mcp_server.py           # ws://127.0.0.1:8002/mcp/
# 生产建议：systemd 服务 + EnvironmentFile=/opt/fall-mcp/env（见仓库 README 安全设计）
```

### 3.3 固件接入（每块板约 30 分钟）

```bash
# 以业务板为例（board-template，配置化接入）
cd evo-firmware/board-template
cp main/boards/board.example.h main/boards/my-board.h   # 填 device_id/WiFi/MQTT
idf.py build && idf.py -p COMxx flash
# 板卡上线后：10s 遥测出现在 MQTT fall/telemetry/<id>，能力中枢自动识别
```

### 3.4 语音板（约 1 小时）

```bash
git clone https://github.com/78/xiaozhi-esp32.git -b v2.4.2
cp -r evo-voice-terminal/boards/evo-voice-v1 xiaozhi/main/boards/
# 注册板卡（Kconfig.projbuild / CMakeLists.txt）+ sdkconfig 清单（见 evo-voice-terminal README）
idf.py build && idf.py -p COMxx flash
# 接线：INMP441(12/13/21) + MAX98357A(4/14/18/5)，充电器供电
# 喊「你好小安」→ 对话；fall-mcp dev_speak 可向板子主动播报
```

### 3.5 双层自进化（智能体层，可选）

DSH 双角色装配（DSH-1 干活者 + DSH-2 进化者）见
[`DSH_EVOLUTION_SETUP.md`](DSH_EVOLUTION_SETUP.md)：
profile 结构 / cordis.patch.yml 插件注册 / 需求文件闭环 / quarantine 隔离回滚。

---

## 常见问题

- **Q：没有雷达/语音板能跑吗？** A：能。第 1 层纯软件即可体验能力中枢；
  第 2 层跌倒板不插雷达也能验证遥测/OTA 链路。
- **Q：最小投入是什么？** A：一台电脑（第 1 层）→ 加一块 ESP32-S3（第 2 层）→
  加一台云服务器（第 3 层核心）。
- **Q：凭据怎么管理？** A：全部 env 注入（.env / systemd EnvironmentFile），
  代码零硬编码；示例文件均为 YOUR_* 占位符。
