#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/flyvpn-gateway-panel"
SERVICE="flyvpn-panel"
PYTHON_BIN="python3"
DEFAULT_IP="$(curl -4 -fsS https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"

say(){ echo -e "$*"; }

say "🪽 FlyVPN Gateway Panel v2 installer"
if [[ $EUID -ne 0 ]]; then say "Запусти от root: sudo bash install.sh"; exit 1; fi

apt update
apt install -y python3 python3-venv python3-pip unzip curl ufw rsync openssl ca-certificates

mkdir -p "$APP_DIR"
rsync -a --delete --exclude venv --exclude storage --exclude .git ./ "$APP_DIR/"
cd "$APP_DIR"

# If old .env was accidentally copied from Remnawave or another app, back it up and create a FlyVPN one.
if [[ ! -f .env ]] || ! grep -q '^ADMIN_USERNAME=' .env 2>/dev/null; then
  if [[ -f .env ]]; then cp .env ".env.backup.$(date +%s)"; fi
  cp .env.example .env
  SECRET="$(openssl rand -hex 32 2>/dev/null || date +%s%N)"
  sed -i "s/^PANEL_SECRET=.*/PANEL_SECRET=$SECRET/" .env
  sed -i "s/^PUBLIC_HOST=.*/PUBLIC_HOST=${DEFAULT_IP:-127.0.0.1}/" .env
fi

$PYTHON_BIN -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt

mkdir -p /usr/local/etc/xray /var/log/xray

if ! command -v xray >/dev/null 2>&1 && [[ ! -x /usr/local/bin/xray ]]; then
  say "⚙️ Xray не найден, ставлю xray-core..."
  bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install -u root
fi

cat > /etc/systemd/system/${SERVICE}.service <<'EOF'
[Unit]
Description=FlyVPN Gateway Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/flyvpn-gateway-panel
EnvironmentFile=/opt/flyvpn-gateway-panel/.env
ExecStart=/bin/bash -lc 'set -a; source /opt/flyvpn-gateway-panel/.env; set +a; exec /opt/flyvpn-gateway-panel/venv/bin/uvicorn app.main:app --host "${PANEL_HOST:-0.0.0.0}" --port "${PANEL_PORT:-8090}"'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Generate Xray config once so xray.service has a valid file.
source venv/bin/activate
python - <<'PY'
from app import xray
print('Xray config:', xray.write_config())
PY

systemctl daemon-reload
systemctl enable --now "$SERVICE"

# xray installer creates xray.service; if available, start/restart it.
if systemctl list-unit-files | grep -q '^xray.service'; then
  systemctl enable xray >/dev/null 2>&1 || true
  systemctl restart xray || true
fi

ufw allow 8090/tcp || true
ufw allow 8443/tcp || true
ufw allow 8443/udp || true

say ""
say "✅ Панель установлена: http://${DEFAULT_IP:-SERVER_IP}:8090"
say "   Логин/пароль: смотри и меняй в $APP_DIR/.env"
say ""
say "Дальше: открой панель → Users → создай клиента с 'Все локации' → Xray → Validate → Restart."
say "Проверка: curl http://127.0.0.1:8090/api/health"
