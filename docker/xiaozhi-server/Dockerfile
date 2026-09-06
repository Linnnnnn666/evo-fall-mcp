# ============================================================
# xiaozhi-server 自建镜像（含 EvoAgent 定制：notify 主动播报/keep-alive 配套等）
# 构建：docker build -t evoagent-xiaozhi-server:local .
# 用法：docker-compose.yml 中 xiaozhi-server.image 指向本镜像
# 注意：模型挂卷（models/ 约 100MB+），凭据走 data/.config.yaml（挂卷，不落镜像）
# ============================================================
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

WORKDIR /opt/xiaozhi-esp32-server

# 上游要求：推荐 python3.10（torch/websockets 版本约束）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
      --extra-index-url https://download.pytorch.org/whl/cpu && rm requirements.txt

# 源码（来自 xiaozhi-esp32-server 工作树；data/ 凭据与运行时状态走卷）
COPY app.py ./
COPY core/ core/
COPY config/ config/

# ASR/TTS 模型（挂卷：models/ 与 data/ 优先）
VOLUME ["/opt/xiaozhi-esp32-server/models", "/opt/xiaozhi-esp32-server/data"]

EXPOSE 8001 8003

CMD ["python3", "app.py"]
