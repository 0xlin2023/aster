#!/bin/bash

# 快速部署脚本
# 使用方法: bash quick_deploy.sh <服务器IP> <用户名> <密钥路径>

SERVER_IP=${1:-"your-server-ip"}
USERNAME=${2:-"root"}
SSH_KEY=${3:-"~/.ssh/id_rsa"}

echo "=== Aster Trading Bot 快速部署 ==="
echo "服务器: $USERNAME@$SERVER_IP"
echo "SSH密钥: $SSH_KEY"
echo ""

# 检查本地文件
if [ ! -f "my_config.yaml" ]; then
    echo "❌ 错误: 找不到 my_config.yaml 文件"
    exit 1
fi

echo "✅ 本地文件检查完成"
echo ""

# 上传代码
echo "📤 正在上传代码到服务器..."
scp -i "$SSH_KEY" -r . "$USERNAME@$SERVER_IP:~/aster/"

# 远程部署脚本
REMOTE_SCRIPT='
#!/bin/bash
echo "🔧 开始远程部署..."

# 进入项目目录
cd ~/aster

# 安装Python和依赖
sudo apt update
sudo apt install -y python3 python3-pip python3-venv

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 设置systemd服务
sudo tee /etc/systemd/system/aster-bot.service > /dev/null <<EOF
[Unit]
Description=Aster Trading Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/aster
Environment=PATH=/home/$USER/aster/venv/bin
ExecStart=/home/$USER/aster/venv/bin/python -m bot my_config.yaml
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 启动服务
sudo systemctl daemon-reload
sudo systemctl start aster-bot
sudo systemctl enable aster-bot

# 检查状态
echo "📊 服务状态:"
sudo systemctl status aster-bot --no-pager

echo ""
echo "✅ 部署完成!"
echo "查看日志: sudo journalctl -u aster-bot -f"
echo "停止服务: sudo systemctl stop aster-bot"
echo "重启服务: sudo systemctl restart aster-bot"
'

# 执行远程部署
echo "🚀 开始远程部署..."
ssh -i "$SSH_KEY" "$USERNAME@$SERVER_IP" "$REMOTE_SCRIPT"

echo ""
echo "🎉 部署完成!"
echo ""
echo "常用命令:"
echo "连接服务器: ssh -i $SSH_KEY $USERNAME@$SERVER_IP"
echo "查看日志: ssh -i $SSH_KEY $USERNAME@$SERVER_IP 'sudo journalctl -u aster-bot -f'"
echo "重启服务: ssh -i $SSH_KEY $USERNAME@$SERVER_IP 'sudo systemctl restart aster-bot'"