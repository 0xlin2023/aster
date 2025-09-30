# Aster Trading Bot 部署指南

## 🚀 快速开始

### 方案1: 云服务器部署 (推荐)

#### 1. 购买服务器
- **阿里云ECS**: 1核2GB，约60元/月
- **腾讯云CVM**: 1核2GB，约70元/月
- **AWS EC2**: t2.micro免费层

#### 2. 一键部署
```bash
# 修改服务器IP后运行
bash quick_deploy.sh 你的服务器IP
```

#### 3. 监控
```bash
# 查看运行状态
ssh root@你的服务器IP "sudo systemctl status aster-bot"

# 查看日志
ssh root@你的服务器IP "sudo journalctl -u aster-bot -f"
```

### 方案2: Docker部署

```bash
# 构建运行
docker-compose up -d

# 查看日志
docker-compose logs -f
```

## 📋 部署清单

### 必须准备
- [ ] 云服务器账号
- [ ] 域名 (可选)
- [ ] API密钥
- [ ] 配置文件

### 安全检查
- [ ] 防火墙配置
- [ ] SSH密钥登录
- [ ] API权限限制
- [ ] 定期备份

## 🔧 配置说明

### 配置文件
```yaml
# my_config.yaml
symbol: BTCUSDT
leverage: 40
per_order_quote_usd: 240
grid_spacing: 20
min_levels_per_side: 1
margin_reserve_pct: 0.10
# ... 其他配置
```

### 环境变量
```bash
# API密钥 (可选，优先级高于配置文件)
export API_KEY="your-api-key"
export API_SECRET="your-api-secret"
```

## 📊 监控方案

### 1. 基础监控
```bash
# 每5分钟检查进程
*/5 * * * * /home/user/aster/check_bot.sh
```

### 2. 日志监控
- 系统日志: `journalctl -u aster-bot -f`
- 应用日志: `tail -f bot.log`
- 错误告警: `grep ERROR bot.log`

### 3. 资源监控
```bash
# 内存使用
free -h

# CPU使用
top

# 磁盘使用
df -h
```

## 🛠️ 故障排除

### 常见问题

#### 1. 服务无法启动
```bash
# 检查配置
python -m bot my_config.yaml --dry-run

# 检查日志
sudo journalctl -u aster-bot -f
```

#### 2. 网络问题
```bash
# 检查网络连接
ping api.binance.com

# 检查防火墙
sudo ufw status
```

#### 3. 权限问题
```bash
# 修复文件权限
chmod 600 my_config.yaml
chown botuser:botuser my_config.yaml
```

### 紧急停止
```bash
# 停止服务
sudo systemctl stop aster-bot

# 取消所有挂单
python -m utils/cancel_all_orders.py

# 平仓
python -m utils/close_position.py
```

## 💰 成本估算

### 月度成本
- 服务器: ¥60-70
- 带宽: ¥10
- **总计**: ¥70-80/月

### 优化建议
- 使用新用户优惠
- 包年包月更便宜
- 选择合适的配置

## 📞 支持

### 文档
- [详细部署指南](deploy_vps.md)
- [Docker部署](deploy_docker.md)
- [云服务对比](deploy_cloud.md)

### 联系方式
- 技术支持: 查看代码仓库
- 问题反馈: 提交Issue

---

**⚠️ 风险提示**
- 交易有风险，投资需谨慎
- 建议先在测试环境验证
- 设置好止损和风险控制