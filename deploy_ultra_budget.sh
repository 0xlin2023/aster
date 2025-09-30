#!/bin/bash

# 超预算部署脚本 - 月成本6-24元
# 适用于腾讯云Lighthouse / 阿里云轻量服务器

echo "=== Aster Trading Bot 超预算部署 ==="
echo "月成本: 6-24元 | 配置: 1核1GB | 内存: 1GB"
echo "========================================"

# 检查必要文件
if [ ! -f "my_config.yaml" ]; then
    echo "❌ 错误: 找不到 my_config.yaml"
    exit 1
fi

echo "✅ 开始优化配置以适应1GB内存..."

# 优化配置文件 (减少内存使用)
sed -i 's/max_open_orders: .*/max_open_orders: 30/' my_config.yaml
sed -i 's/max_resting_orders_per_side: .*/max_resting_orders_per_side: 15/' my_config.yaml
sed -i 's/log_level: .*/log_level: WARNING/' my_config.yaml
sed -i 's/per_order_quote_usd: .*/per_order_quote_usd: 200/' my_config.yaml

echo "✅ 配置优化完成"
echo ""

# 创建最小化部署包
echo "📦 创建最小化部署包..."
rm -rf deploy_package
mkdir deploy_package

# 复制核心文件
cp -r bot deploy_package/
cp -r client deploy_package/
cp -r config.py deploy_package/
cp -r grid.py deploy_package/
cp -r state.py deploy_package/
cp requirements.txt deploy_package/
cp my_config.yaml deploy_package/

# 创建最小requirements
cat > deploy_package/requirements_minimal.txt << 'EOF'
aiohttp>=3.8.0
websockets>=10.0
pyyaml>=6.0
requests>=2.28.0
EOF

echo "✅ 部署包创建完成"
echo ""

# 自动部署到服务器
echo "🚀 请输入服务器信息:"
read -p "服务器IP: " SERVER_IP
read -p "用户名 (默认root): " USERNAME
USERNAME=${USERNAME:-root}
read -p "SSH密钥路径 (默认~/.ssh/id_rsa): " SSH_KEY
SSH_KEY=${SSH_KEY:-~/.ssh/id_rsa}

echo ""
echo "📤 正在上传最小化部署包..."

# 上传部署包
scp -i "$SSH_KEY" -r deploy_package/* "$USERNAME@$SERVER_IP:~/aster/"

# 远程部署脚本
REMOTE_SCRIPT='
#!/bin/bash
echo "🔧 开始超预算部署..."

cd ~/aster

# 更新系统 (只更新必要包)
apt update
apt install -y python3 python3-pip git --no-install-recommends

# 安装最小依赖
python3 -m pip install --no-cache-dir -r requirements_minimal.txt

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 优化Python环境
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# 创建监控脚本
cat > monitor.sh << "MONEOF"
#!/bin/bash
cd ~/aster
source venv/bin/activate
if ! pgrep -f "python -m bot"; then
    echo "$(date): Bot restarted" >> restart.log
    nohup python -m bot my_config.yaml >> bot.log 2>&1 &
fi
MONEOF

chmod +x monitor.sh

# 创建日志清理脚本
cat > cleanup.sh << "CLEANEOF"
#!/bin/bash
find ~/aster -name "*.log" -mtime -7 -delete
find /tmp -name "*aster*" -mtime -1 -delete
CLEANEOF

chmod +x cleanup.sh

# 设置定时任务
crontab -l 2>/dev/null > current_cron
echo "*/5 * * * * cd /root/aster && ./monitor.sh" >> current_cron
echo "0 */6 * * * cd /root/aster && ./cleanup.sh" >> current_cron
crontab current_cron

# 后台启动机器人
nohup python -m bot my_config.yaml >> bot.log 2>&1 &

echo "✅ 部署完成!"
echo "内存使用: $(free -h | grep Mem)"
echo "进程状态: $(ps aux | grep python | grep -v grep)"

echo ""
echo "📊 监控命令:"
echo "tail -f ~/aster/bot.log"
echo "ps aux | grep python"
echo "free -h"
'

# 执行远程部署
echo "🚀 正在远程部署..."
ssh -i "$SSH_KEY" "$USERNAME@$SERVER_IP" "$REMOTE_SCRIPT"

echo ""
echo "🎉 超预算部署完成!"
echo ""
echo "📊 服务器资源使用监控:"
ssh -i "$SSH_KEY" "$USERNAME@$SERVER_IP" "free -h && echo '---' && ps aux | grep python | grep -v grep"

echo ""
echo "📋 重要信息:"
echo "服务器: $USERNAME@$SERVER_IP"
echo "监控日志: ssh -i $SSH_KEY $USERNAME@$SERVER_IP 'tail -f ~/aster/bot.log'"
echo "重启服务: ssh -i $SSH_KEY $USERNAME@$SERVER_IP 'cd ~/aster && source venv/bin/activate && python -m bot my_config.yaml'"
echo ""
echo "💰 月成本: 6-24元"
echo "🎯 预计内存使用: <400MB"
echo "⚡ 预计CPU使用: <10%"