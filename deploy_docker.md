# Docker部署方案

## 1. 创建Dockerfile

```dockerfile
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建非root用户
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# 运行命令
CMD ["python", "-m", "bot", "my_config.yaml"]
```

## 2. 创建docker-compose.yml

```yaml
version: '3.8'

services:
  aster-bot:
    build: .
    restart: unless-stopped
    environment:
      - PYTHONUNBUFFERED=1
    volumes:
      - ./my_config.yaml:/app/my_config.yaml:ro
      - ./logs:/app/logs
    networks:
      - bot-network

networks:
  bot-network:
    driver: bridge

volumes:
  logs:
    driver: local
```

## 3. 部署命令

```bash
# 构建和启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down

# 重启
docker-compose restart
```

## 4. 监控和健康检查

### 增强docker-compose.yml
```yaml
services:
  aster-bot:
    build: .
    restart: unless-stopped
    environment:
      - PYTHONUNBUFFERED=1
    volumes:
      - ./my_config.yaml:/app/my_config.yaml:ro
      - ./logs:/app/logs
    networks:
      - bot-network
    healthcheck:
      test: ["CMD", "pgrep", "-f", "python"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'
        reservations:
          memory: 256M
          cpus: '0.25'
```

## 5. 日志管理

### 创建日志目录和脚本
```bash
mkdir -p logs

# 创建启动脚本
cat > start.sh << 'EOF'
#!/bin/bash
docker-compose up -d
echo "Bot started at $(date)" >> logs/startup.log
EOF

chmod +x start.sh
```