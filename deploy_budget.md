# 经济实惠的部署方案

## 💰 性价比最高的选择

### 方案1: 轻量应用服务器 (推荐)

#### 1.1 腾讯云Lighthouse (最佳选择)
- **配置**: 1核1GB + 25GB SSD + 1Mbps带宽
- **价格**: ¥24/月 (新用户首年¥12/月)
- **优点**:
  - 预装Docker，一键部署
  - 独立IP，带宽不限
  - 控制面板简单易用
  - 流量1000GB/月，完全够用

#### 1.2 阿里云轻量服务器
- **配置**: 1核1GB + 25GB SSD + 1Mbps带宽
- **价格**: ¥24/月 (经常有6元/月的促销)
- **优点**:
  - 系统镜像丰富
  - 流量1000GB/月
  - 轻量应用，管理简单

### 方案2: 学生优惠/试用

#### 2.1 阿里云学生计划
- **配置**: 1核2GB + 40GB SSD
- **价格**: 学生¥10/月 (需学生认证)
- **申请**: [阿里云学生中心](https://developer.aliyun.com/adc/student/)

#### 2.2 腾讯云校园计划
- **配置**: 1核2GB + 50GB SSD
- **价格**: 学生¥10/月
- **申请**: [腾讯云校园计划](https://cloud.tencent.com/act/campus)

#### 2.3 华为云学生套餐
- **配置**: 1核2GB + 2Mbps带宽
- **价格**: 学生¥9/月
- **申请**: [华为云学生计划](https://activity.huaweicloud.com/discount_area_v0/index.html)

### 方案3: 免费方案

#### 3.1 腾讯云体验馆
- **时长**: 7天免费试用
- **用途**: 测试部署流程
- **链接**: [腾讯云体验馆](https://cloud.tencent.com/act/free)

#### 3.2 阿里云体验
- **时长**: 7天免费
- **用途**: 验证配置正确性

## 🚀 快速部署指南

### 第一步：选择服务器 (5分钟)

```bash
# 推荐：腾讯云Lighthouse
1. 访问: https://curl.qcloud.com/P2y3uX0d
2. 选择：Ubuntu 22.04
3. 地域：就近选择 (上海/北京/广州)
4. 套餐：1核1GB 24元/月
5. 设置密码，创建实例
```

### 第二步：一键部署 (10分钟)

```bash
# 连接服务器
ssh root@你的IP

# 更新系统
apt update && apt upgrade -y

# 安装Python
apt install -y python3 python3-pip git

# 克隆代码
git clone <你的仓库地址>
cd aster

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 后台运行
nohup python -m bot my_config.yaml > bot.log 2>&1 &

# 查看运行状态
ps aux | grep python
tail -f bot.log
```

### 第三步：简单监控 (5分钟)

```bash
# 创建监控脚本
cat > monitor.sh << 'EOF'
#!/bin/bash
if ! pgrep -f "python -m bot"; then
    echo "$(date): Bot is down, restarting..." >> monitor.log
    source venv/bin/activate
    nohup python -m bot my_config.yaml > bot.log 2>&1 &
fi
EOF

# 设置定时任务
crontab -e
# 添加：*/5 * * * * cd /root/aster && ./monitor.sh
```

## 📊 资源使用评估

### 内存需求 (1GB足够)
- Python: ~50MB
- 依赖库: ~100MB
- 系统进程: ~200MB
- **总计**: ~350MB (剩余650MB)

### CPU需求 (1核足够)
- 日常运行: <5% CPU
- 交易高峰: ~20% CPU
- 重启时: ~50% CPU

### 网络需求 (1Mbps足够)
- WebSocket数据流: ~10KB/s
- API调用: ~1KB/s
- **总计**: ~50KB/s (0.4Mbps)

### 存储需求 (25GB足够)
- 代码: ~50MB
- 日志: ~100MB/月
- 系统: ~5GB
- **总计**: ~6GB (剩余19GB)

## 🔧 优化配置 (降低资源使用)

### 1. 配置文件优化
```yaml
# 降低内存使用
log_level: WARNING  # 减少日志输出
max_open_orders: 40  # 减少订单数量
max_resting_orders_per_side: 15

# 降低CPU使用
recenter_threshold: 0.2  # 减少重新计算频率
maker_guard_ticks: 2  # 减少价格调整
```

### 2. 日志优化
```bash
# 日志轮转 (节省磁盘)
cat > logrotate.conf << 'EOF'
/root/aster/bot.log {
    daily
    rotate 3
    compress
    missingok
    notifempty
    size 10M
}
EOF

# 应用日志轮转
sudo cp logrotate.conf /etc/logrotate.d/aster
```

## 🛡️ 安全配置 (免费)

### 1. 基础安全
```bash
# 更新系统
apt update && apt upgrade -y

# 配置防火墙
ufw enable
ufw allow 22
ufw deny 80
ufw deny 443

# 禁用root登录
useradd -m bot
usermod -aG sudo bot
# 修改SSH配置，禁用root登录
```

### 2. API安全
```bash
# 使用环境变量
echo "export API_KEY='your-key'" >> /home/bot/.bashrc
echo "export API_SECRET='your-secret'" >> /home/bot/.bashrc
```

## 💰 成本总结

### 月度成本 (按年付费)
- **腾讯云Lighthouse**: ¥12-24/月
- **阿里云轻量**: ¥6-24/月 (经常有促销)
- **学生优惠**: ¥9-10/月

### 首次成本 (3个月测试)
- **总花费**: ¥36-72 (3个月)
- **预期收益**: 通过优化策略提升交易量
- **ROI**: 1-2个月即可回本

## 📈 推荐方案

### 🥇 最佳性价比：腾讯云Lighthouse
- **价格**: ¥24/月
- **配置**: 1核1GB + 25GB + 1Mbps
- **适合**: 长期稳定运行

### 🥈 最便宜：阿里云促销
- **价格**: ¥6/月 (促销时)
- **配置**: 1核1GB + 25GB + 1Mbps
- **适合**: 预算有限时

### 🥉 学生首选：学生优惠
- **价格**: ¥9-10/月
- **配置**: 1核2GB + 更大带宽
- **适合**: 学生身份用户

## 🎯 立即行动

1. **腾讯云购买链接**: https://curl.qcloud.com/P2y3uX0d
2. **阿里云优惠**: 关注官网促销活动
3. **学生优惠**: 准备学生证，申请学生认证

这样你每月只需花费**¥6-24**就能让机器人24小时稳定运行，性价比极高！