#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/flyvpn-gateway-panel"
SERVICE="flyvpn-panel"
PYTHON_BIN="python3"
DEFAULT_IP="$(curl -4 -fsS https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"

say(){ echo -e "$*"; }

say "🪽 BlackWing Gateway Panel v6 installer"
if [[ $EUID -ne 0 ]]; then say "Запусти от root: sudo bash install.sh"; exit 1; fi

apt update
apt install -y python3 python3-venv python3-pip unzip curl ufw rsync openssl ca-certificates

mkdir -p "$APP_DIR"
rsync -a --delete --exclude venv --exclude storage --exclude .git ./ "$APP_DIR/"
cd "$APP_DIR"

if [[ ! -f .env ]] || ! grep -q '^ADMIN_USERNAME=' .env 2>/dev/null; then
  if [[ -f .env ]]; then cp .env ".env.backup.$(date +%s)"; fi
  cp .env.example .env
fi
SECRET="$(openssl rand -hex 32 2>/dev/null || date +%s%N)"
grep -q '^PANEL_SECRET=' .env && sed -i "s/^PANEL_SECRET=.*/PANEL_SECRET=$SECRET/" .env || echo "PANEL_SECRET=$SECRET" >> .env
grep -q '^PUBLIC_HOST=' .env && sed -i "s/^PUBLIC_HOST=.*/PUBLIC_HOST=${DEFAULT_IP:-127.0.0.1}/" .env || echo "PUBLIC_HOST=${DEFAULT_IP:-127.0.0.1}" >> .env
grep -q '^BRAND_NAME=' .env || echo 'BRAND_NAME=BlackWing' >> .env
grep -q '^HAPP_DEEPLINK_PATTERN=' .env || echo 'HAPP_DEEPLINK_PATTERN=happ://add/{url}' >> .env
grep -q '^HAPP_SUBSCRIPTION_TITLE=' .env || echo 'HAPP_SUBSCRIPTION_TITLE=BlackWing VPN' >> .env
grep -q '^HAPP_LOCATION_SUFFIX=' .env || echo 'HAPP_LOCATION_SUFFIX=🔥 Новые блокировки' >> .env
grep -q '^SUB_UPDATE_INTERVAL_HOURS=' .env || echo 'SUB_UPDATE_INTERVAL_HOURS=1' >> .env
grep -q '^DEFAULT_TRAFFIC_LIMIT_GB=' .env || echo 'DEFAULT_TRAFFIC_LIMIT_GB=10' >> .env
grep -q '^DIRECT_OUTPUT_MODE=' .env || echo 'DIRECT_OUTPUT_MODE=array' >> .env
grep -q '^SUBSCRIPTION_MODE=' .env || echo 'SUBSCRIPTION_MODE=direct' >> .env

$PYTHON_BIN -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt

mkdir -p /usr/local/etc/xray /var/log/xray

if ! command -v xray >/dev/null 2>&1 && [[ ! -x /usr/local/bin/xray ]]; then
  say "⚙️ Xray не найден, ставлю xray-core..."
  bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install -u root
fi

cp systemd/flyvpn-panel.service /etc/systemd/system/${SERVICE}.service
cp systemd/blackwing-updater.service /etc/systemd/system/blackwing-updater.service
cp systemd/blackwing-updater.timer /etc/systemd/system/blackwing-updater.timer

python - <<'PY'
from app import db, xray
from app.upstreams import protocol_summary, count_proxy_outbounds
import json

db.init_db()
# Backfill old upstream protocol columns.
for up in db.rows('SELECT * FROM upstreams'):
    try:
        data = json.loads(up['json_text'])
        db.execute('UPDATE upstreams SET protocol_summary=?, proxy_count=? WHERE id=?', (protocol_summary(data), count_proxy_outbounds(data), up['id']))
    except Exception:
        pass
print('Xray config:', xray.write_config())
PY

systemctl daemon-reload
systemctl enable --now "$SERVICE"
systemctl enable --now blackwing-updater.timer

if systemctl list-unit-files | grep -q '^xray.service'; then
  systemctl enable xray >/dev/null 2>&1 || true
  systemctl restart xray || true
fi

ufw allow 8090/tcp || true
ufw allow 8443/tcp || true
ufw allow 8443/udp || true

say ""
say "✅ Панель установлена: http://${DEFAULT_IP:-SERVER_IP}:8090"
say "   Бренд: BlackWing"
say "   Логин/пароль: смотри и меняй в $APP_DIR/.env"
say ""
say "Дальше: открой панель → Settings → проверь бренд и PUBLIC_HOST → Upstreams → импорт JSON → Users → создать клиента → Xray → Validate → Restart."
say "Автообновление: systemctl status blackwing-updater.timer --no-pager -l"
