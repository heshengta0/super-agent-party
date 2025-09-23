# 使用官方Python镜像
FROM python:3.12-slim

# 安装系統依賴
RUN apt-get update && \
    apt-get install -y gcc curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \ 
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 先單獨複製依賴文件
COPY requirements.txt package.json package-lock.json ./

# 安裝 Python 依賴
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 安裝 Node.js 依賴
RUN npm install --production --legacy-peer-deps

# 複製其他項目文件
COPY . .

# 創建必要目錄
RUN mkdir -p uploaded_files

# 暴露端口
EXPOSE 3456

# 設置環境變量
ENV HOST=0.0.0.0 \
    PORT=3456 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 啟動命令
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "3456"]
