# BlackWing Gateway Panel v6

Панель для продажи VPN-подписок BlackWing с импортом готовых Happ/Xray JSON-конфигов.

## Главное в v6

- Подписка в Happ выглядит как нормальная VPN-группа: **BlackWing VPN**, автообновление **1 час**, срок и лимит трафика через subscription headers.
- `/sub/<secure_token>` в режиме `direct + array` отдаёт **массив JSON-профилей**, один профиль на каждую локацию. Это ближе к виду: `Нидерланды / Финляндия / Германия`, а не куча `vless://...@IP`.
- Названия локаций короткие: `🇩🇪 Германия (🔥 Новые блокировки)`, без длинного описания в каждой строке.
- Есть `/s/<secure_token>` — красивая страница с кнопкой **Открыть в Happ**.
- Есть `/links/<secure_token>` — отдельные gateway VLESS links, если они нужны.
- Поддерживается импорт `vless`, `vmess`, `trojan`, `shadowsocks`, `hysteria`, `hysteria2`, `tuic`, `wireguard`, `socks`, `http`.
- Source URL и автообновление upstream-конфигов через `blackwing-updater.timer`.

## Установка / обновление

```bash
cd /opt/MasterVPN
# сначала залей v6 в GitHub, потом:
git pull
cd /opt/MasterVPN/FlyVPN_Gateway_Panel
bash install.sh
systemctl restart flyvpn-panel
```

Панель:

```text
http://SERVER_IP:8090
```

## Рекомендуемые настройки для Happ

В панели открой **Settings** и поставь:

```text
SUBSCRIPTION_MODE = direct
DIRECT_OUTPUT_MODE = array
HAPP_SUBSCRIPTION_TITLE = BlackWing VPN
HAPP_LOCATION_SUFFIX = 🔥 Новые блокировки
SUB_UPDATE_INTERVAL_HOURS = 1
DEFAULT_TRAFFIC_LIMIT_GB = 10
```

После этого клиенту давай:

```text
http://SERVER_IP:8090/s/<secure_token>
```

Happ будет получать подписку:

```text
http://SERVER_IP:8090/sub/<secure_token>
```

## Проверка

```bash
systemctl status flyvpn-panel --no-pager -l
systemctl status blackwing-updater.timer --no-pager -l
curl http://127.0.0.1:8090/api/health
ss -lntup | grep -E '8090|8443'
```

## Важно

Direct-режим нужен для конфигов, которые работают именно в Happ: Reality/gRPC, Hysteria/finalmask и другие client-specific варианты. Gateway-режим даёт больше контроля доступа, но не все чужие протоколы можно стабильно проксировать через серверный Xray.

Используй только свои или партнёрские конфиги, на которые у тебя есть разрешение.
