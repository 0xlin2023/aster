# 云服务部署方案对比

## 1. 云服务器选择

### 推荐方案

#### 1.1 阿里云ECS
- **优点**: 国内访问快，价格便宜，管理界面友好
- **配置**: 1核2GB，约60元/月
- **部署时间**: 10分钟

#### 1.2 腾讯云CVM
- **优点**: 新用户有优惠，性能稳定
- **配置**: 1核2GB，约70元/月
- **部署时间**: 15分钟

#### 1.3 AWS EC2
- **优点**: 全球可用，性能稳定
- **配置**: t2.micro (免费试用)
- **部署时间**: 20分钟

## 2. 快速部署步骤

### 2.1 服务器初始化
```bash
# SSH连接到服务器
ssh root@服务器IP

# 创建用户
adduser bot
usermod -aG sudo bot
su - bot

# 基础安装
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv
```

### 2.2 代码部署
```bash
# 克隆代码
git clone <你的仓库地址>
cd aster

# 环境设置
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 配置文件
cp my_config.yaml.example my_config.yaml
nano my_config.yaml  # 填入你的API密钥
```

### 2.3 后台运行
```bash
# 方法1: screen
screen -S bot
python -m bot my_config.yaml
# 按Ctrl+A，然后按D分离

# 方法2: nohup
nohup python -m bot my_config.yaml > bot.log 2>&1 &

# 方法3: systemd (推荐)
# 参考 deploy_vps.md 中的systemd配置
```

## 3. 监控方案

### 3.1 简单监控脚本
```bash
# 创建监控脚本
cat > monitor.sh << 'EOF'
#!/bin/bash
if ! pgrep -f "python -m bot"; then
    echo "$(date): Bot is down, restarting..." >> monitor.log
    python -m bot my_config.yaml &
fi
EOF

# 设置定时任务
crontab -e
# 添加：*/5 * * * * /home/bot/aster/monitor.sh
```

### 3.2 使用云监控
- 阿里云: 云监控
- 腾讯云: 云监控告警
- AWS: CloudWatch

## 4. 安全建议

### 4.1 API密钥管理
```bash
# 使用环境变量
echo "export API_KEY='your-api-key'" >> ~/.bashrc
echo "export API_SECRET='your-api-secret'" >> ~/.bashrc
source ~/.bashrc
```

### 4.2 防火墙设置
```bash
sudo ufw enable
sudo ufw allow ssh
sudo ufw deny 80
sudo ufw deny 443
```

## 5. 成本估算

### 月度成本对比
| 服务商 | 配置 | 价格 | 备注 |
|--------|------|------|------|
| 阿里云 | 1核2GB | ¥60 | 新用户优惠 |
| 腾讯云 | 1核2GB | ¥70 | 包年包月更便宜 |
| AWS | t2.micro | 免费 | 首年免费 |
| Vultr | 1核1GB | $6 | 海外，稳定 |

### 额外成本
- 带宽: 约¥10/月
- 存储: 基本够用
- 监控: 大部分免费

## 6. 推荐方案

### 最快部署 (30分钟内)
1. 购买腾讯云CVM
2. 使用nohup运行
3. 设置简单监控

### 最稳定方案 (1小时内)
1. 阿里云ECS
2. systemd管理
3. 完整监控和备份

### 最省钱方案 (免费)
1. AWS EC2免费层
2. Docker部署
3. 基础监控