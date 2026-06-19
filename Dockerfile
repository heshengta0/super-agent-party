FROM python:3.12-slim

# 1. 安装系统依赖和 Node.js (包含 npm)
RUN apt-get update && \
    # 安装基础工具包和 CA 证书（curl https 时需要）
    apt-get install -y gcc curl git ca-certificates && \
    # 获取 NodeSource 的 Node 20.x 安装脚本并执行
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    # 安装 nodejs (apt 安装 nodejs 会自动包含 npm)
    apt-get install -y nodejs && \
    # 清理 APT 缓存，减小镜像体积
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 全局安装 acpx（这样任何地方都能直接用）
RUN npm install -g acpx@latest

WORKDIR /app

# 2. 先只复制依赖文件 (利用缓存)
COPY pyproject.toml uv.lock ./

# 3. 安装 Python 依赖
RUN pip install uv && \
    uv venv && \
    uv sync

# 4. 最后再复制源代码 (这样改代码不会触发重新安装依赖)
COPY . .

# 5. 拉取安全词表（默认不启用，仅 Steam 发布时需要）
ARG FETCH_SAFETY_WORDS=false
RUN if [ "$FETCH_SAFETY_WORDS" = "true" ]; then python scripts/fetch_safety_words.py; fi

# 6. 设置权限和目录
RUN mkdir -p uploaded_files && \
    chmod 755 uploaded_files

EXPOSE 3456

# 6. 配置环境变量
# 移除 ELECTRON_NODE_EXEC，增加 IS_DOCKER=1 让 Python 后端明确知道自己在哪
ENV HOST=0.0.0.0 \
    PORT=3456 \
    PYTHONUNBUFFERED=1 \
    IS_DOCKER=1

CMD [".venv/bin/python", "server.py", "--host", "0.0.0.0", "--port", "3456"]