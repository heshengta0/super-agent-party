# Super Agent Party Docker 部署指南

這個指南將幫助您使用 Docker Compose 部署 Super Agent Party，支持開發和生產環境。

## 🚀 快速開始

### 開發環境（推薦）

開發環境會掛載整個專案目錄，修改代碼後無需重新構建鏡像。

```bash
# 構建並啟動開發環境
./docker-manager.sh dev-up

# 或者手動執行
docker-compose -f docker-compose.dev.yml up -d
```

### 生產環境

```bash
# 構建並啟動生產環境
./docker-manager.sh prod-up

# 或者手動執行
docker-compose up -d
```

## 📁 項目結構

```
super-agent-party/
├── docker-compose.yml          # 生產環境配置
├── docker-compose.dev.yml      # 開發環境配置
├── Dockerfile                  # 生產環境鏡像
├── Dockerfile.dev             # 開發環境鏡像
├── docker-manager.sh          # Docker 管理腳本
├── data/                      # 數據持久化目錄
└── uploaded_files/            # 上傳文件目錄
```

## 🛠 Docker 管理腳本

使用 `docker-manager.sh` 腳本可以方便地管理 Docker 容器：

```bash
# 查看所有可用命令
./docker-manager.sh help

# 開發環境命令
./docker-manager.sh dev-build    # 構建開發環境鏡像
./docker-manager.sh dev-up       # 啟動開發環境
./docker-manager.sh dev-down     # 停止開發環境
./docker-manager.sh dev-logs     # 查看開發環境日誌
./docker-manager.sh dev-shell    # 進入開發環境容器

# 生產環境命令
./docker-manager.sh prod-build   # 構建生產環境鏡像
./docker-manager.sh prod-up      # 啟動生產環境
./docker-manager.sh prod-down    # 停止生產環境
./docker-manager.sh prod-logs    # 查看生產環境日誌

# 清理命令
./docker-manager.sh clean        # 清理所有 Docker 資源
```

## 🔧 配置說明

### 開發環境特性

- **Volume 掛載**：整個專案目錄掛載到容器中，修改代碼立即生效
- **依賴實時安裝**：容器啟動時安裝最新的 Python 依賴
- **調試友好**：支持 stdin/tty，方便調試

### 生產環境特性

- **優化構建**：依賴預先安裝在鏡像中，啟動更快
- **資源隔離**：只暴露必要的端口和文件
- **穩定性**：使用固定版本的依賴

### 數據持久化

- `./data`：應用數據目錄，映射到容器的 `/root/.local/share/Super-Agent-Party`
- `./uploaded_files`：上傳文件目錄

### 端口映射

- `3456`：Web 界面端口，訪問 `http://localhost:3456`

## 🐛 故障排除

### 端口已被佔用
```bash
# 檢查端口使用情況
sudo netstat -tulpn | grep 3456

# 停止占用端口的進程
sudo kill -9 <PID>
```

### 權限問題
```bash
# 確保 Docker 用戶權限
sudo usermod -aG docker $USER
# 重新登錄或執行
newgrp docker
```

### 容器無法啟動
```bash
# 查看詳細日誌
./docker-manager.sh dev-logs
# 或
docker-compose -f docker-compose.dev.yml logs
```

### 清理所有資源
```bash
# 停止並删除所有相關容器和鏡像
./docker-manager.sh clean
```

## 📝 開發工作流

1. **首次設置**：
   ```bash
   git clone <repository>
   cd super-agent-party
   ./docker-manager.sh dev-up
   ```

2. **日常開發**：
   - 修改代碼（自動同步到容器）
   - 如果修改了 `requirements.txt`，重啟容器：
     ```bash
     ./docker-manager.sh dev-down
     ./docker-manager.sh dev-up
     ```

3. **調試**：
   ```bash
   # 進入容器進行調試
   ./docker-manager.sh dev-shell
   ```

4. **部署到生產**：
   ```bash
   ./docker-manager.sh dev-down
   ./docker-manager.sh prod-up
   ```

## 🌟 優勢

- **🔄 快速開發**：Volume 掛載，修改代碼無需重新構建
- **📦 一致環境**：Docker 確保開發和生產環境一致
- **🚀 簡單部署**：一鍵啟動和停止
- **📊 易於管理**：統一的管理腳本
- **💾 數據安全**：數據持久化，容器重啟數據不丟失

## 🆚 對比傳統方式

| 特性 | 傳統方式 | Docker Compose |
|------|----------|----------------|
| 環境隔離 | ❌ | ✅ |
| 依賴管理 | 手動 | 自動化 |
| 部署複雜度 | 高 | 低 |
| 開發效率 | 一般 | 高 |
| 環境一致性 | 低 | 高 |

## 📞 支持

如果遇到問題，請檢查：
1. Docker 和 Docker Compose 是否正確安裝
2. 端口 3456 是否被其他程序占用
3. 文件權限是否正確
4. 系統資源是否充足

更多問題請參考項目文檔或提交 Issue。
