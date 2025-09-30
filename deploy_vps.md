# VPS部署指南

## 1. 服务器配置选择
- **最低配置**: 1核CPU, 1GB内存, 10GB硬盘
- **推荐配置**: 2核CPU, 2GB内存, 20GB硬盘
- **系统**: Ubuntu 20.04/22.04 LTS

## 2. 服务器环境准备

### 安装Python和依赖
```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装Python和pip
sudo apt install python3 python3-pip python3-venv -y

# 安装git
sudo apt install git -y

# 创建项目目录
cd ~
git clone <你的代码仓库地址> aster
cd aster

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 配置文件安全
```bash
# 创建配置文件（不要提交到git）
cp my_config.yaml config_backup.yaml
chmod 600 my_config.yaml  # 只限所有者读写
```

## 3. 进程管理（使用systemd）

### 创建systemd服务文件
```bash
sudo nano /etc/systemd/system/aster-bot.service
```

**服务配置内容：**
```ini
[Unit]
Description=Aster Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/aster
Environment=PATH=/home/ubuntu/aster/venv/bin
ExecStart=/home/ubuntu/aster/venv/bin/python -m bot my_config.yaml
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 启动和管理服务
```bash
# 重新加载systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start aster-bot

# 设置开机自启
sudo systemctl enable aster-bot

# 查看状态
sudo systemctl status aster-bot

# 查看日志
sudo journalctl -u aster-bot -f
```

## 4. 监控和日志

### 设置日志轮转
```bash
sudo nano /etc/logrotate.d/aster-bot
```

**配置内容：**
```
/home/ubuntu/aster/bot.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
```

### 创建监控脚本
```bash
nano ~/aster/check_bot.sh
```

**脚本内容：**
```bash
#!/bin/bash
if ! systemctl is-active --quiet aster-bot; then
    echo "Bot is not running! Restarting..."
    sudo systemctl restart aster-bot
    echo "Bot restarted at $(date)" >> /home/ubuntu/aster/restart.log
fi
```

**设置定时检查：**
```bash
# 每5分钟检查一次
crontab -e
# 添加：*/5 * * * * /home/ubuntu/aster/check_bot.sh
```

## 5. 安全配置

### SSH安全
```bash
# 修改SSH端口
sudo nano /etc/ssh/sshd_config
# Port 2222

# 重启SSH
sudo systemctl restart ssh

# 设置防火墙
sudo ufw enable
sudo ufw allow 2222
sudo ufw allow from 你的IP地址
```

### API密钥安全
- 使用环境变量存储API密钥
- 定期轮换密钥
- 限制API权限（只允许期货交易）

## 6. 备份策略

### 自动备份脚本
```bash
nano ~/aster/backup.sh
```

**脚本内容：**
```bash
#!/bin/bash
BACKUP_DIR="/home/ubuntu/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# 备份配置和日志
tar -czf $BACKUP_DIR/aster_backup_$DATE.tar.gz \
    my_config.yaml \
    bot.log \
    --exclude='__pycache__'

# 保留最近7天的备份
find $BACKUP_DIR -name "aster_backup_*.tar.gz" -mtime +7 -delete
```

**设置每日备份：**
```bash
crontab -e
# 添加：0 2 * * * /home/ubuntu/aster/backup.sh
```