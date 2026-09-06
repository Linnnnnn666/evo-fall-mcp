# ============================================================
# EvoAgent 能力中枢 fall-mcp 容器化
# 关键设计：
# 1. 工作目录 = /opt/fall-mcp（代码/DSH 子代理模板/状态路径全部与宿主一致，
#    硬编码路径零改动，容器内外可互换）
# 2. 网络 = host（依赖 localhost:1883 MQTT / 127.0.0.1:8000 API / 宿主 DSH-2）
# 3. 状态（dynamic_tools/.git 回滚库、evolution.log、队列、devices.json、
#    flash_events.jsonl 等）= "系统的记忆"，全部挂卷持久化，容器重建不丢
# 4. 镜像只含代码；凭据全部 env 注入（不落镜像）
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 运行根目录与宿主生产布局一致（DSH 子代理 bash 模板硬编码 /opt/fall-mcp/*）
WORKDIR /opt/fall-mcp

# 依赖（fall-mcp 本体 + 动态工具运行时）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && rm requirements.txt

# 代码
COPY mcp_server.py dev_tool_factory.py speech_log.py ./
COPY plugins/ plugins/

# 状态目录（内容由卷提供；空目录兜底）
RUN mkdir -p dynamic_tools confirm_queue plugin_requests task_results \
             voice_alarm onboarding_archives

# MCP 端点（websocket，host 网络下即宿主 127.0.0.1:8002）
EXPOSE 8002

CMD ["python3", "mcp_server.py"]
