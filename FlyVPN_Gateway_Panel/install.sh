#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/flyvpn-gateway-panel"
SERVICE="flyvpn-panel"
PYTHON_BIN="python3"

echo "🪽 FlyVPN Gateway Panel installer"
if [[ $EUID -ne 0 ]]; then echo "Запусти от root: sudo bash install.sh"; exit 1; fi

apt update
apt install -y python3 python3-venv python3-pip unzip curl ufw

mkdir -p "$APP_DIR"
rsync -a --exclude venv --exclude storage ./ "$APP_DIR/"
cd "$APP_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  SECRET=$(openssl rand -hex 32 2>/dev/null || date +%s%N)
  sed -i "s/^PANEL_SECRET=.*/PANEL_SECRET=$SECRET/" .env
fi

$PYTHON_BIN -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt

mkdir -p /etc/flyvpn/xray

cat > /etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=FlyVPN Gateway Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host \${PANEL_HOST:-0.0.0.0} --port \${PANEL_PORT:-8090}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE"

ufw allow 8090/tcp || true
ufw allow 8443/tcp || true
ufw allow 8443/udp || true

echo
echo "✅ Панель установлена: http://SERVER_IP:8090"
echo "   Логин/пароль смотри и меняй в $APP_DIR/.env"
echo
echo "Дальше: открой панель → Settings → проверь PUBLIC_HOST → Xray → Rebuild."
echo "Для Xray поставь xray-core и service name xray, либо укажи свой сервис в Settings."
