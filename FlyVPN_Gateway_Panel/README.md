# BlackWing Gateway Panel v4

Панель для продажи VPN через собственный gateway: клиент получает ссылку на твой VPS, а Xray на сервере маршрутизирует трафик через импортированные Happ/Xray JSON-upstreams.

## Что нового в v4

- Бренд по умолчанию: **BlackWing**.
- Красивая страница подписки `/s/<secure_token>` с инструкцией и кнопкой `happ://`.
- Сырые подписки остаются на `/sub/<secure_token>`.
- Никаких угадываемых `/users/1/sub` для клиента — только длинный random token.
- Настройки названия и описания VPN для Happ: `BRAND_NAME`, `VPN_DESCRIPTION`, `HAPP_PROFILE_PREFIX`.
- Импорт новых протоколов: `vless`, `vmess`, `trojan`, `shadowsocks`, `hysteria`, `hysteria2`, `tuic`, `wireguard`, `socks`, `http`.
- Source URL для upstream-конфигов и автообновление раз в час через `blackwing-updater.timer`.
- Быстрое редактирование названий локаций, чтобы в Happ было не ID сервера, а красивое название.

## Установка

```bash
cd /opt/MasterVPN/FlyVPN_Gateway_Panel
bash install.sh
```

Панель:

```text
http://SERVER_IP:8090
```

Логин и пароль в:

```bash
nano /opt/flyvpn-gateway-panel/.env
systemctl restart flyvpn-panel
```

## Проверка

```bash
systemctl status flyvpn-panel --no-pager -l
systemctl status blackwing-updater.timer --no-pager -l
systemctl status xray --no-pager -l
curl http://127.0.0.1:8090/api/health
ss -lntup | grep -E '8090|8443'
```

## Как выдавать клиенту

В панели зайди в **Users**, создай клиента и копируй **Landing URL**:

```text
http://SERVER_IP:8090/s/<secure_token>
```

На этой странице клиент увидит инструкцию, кнопку **Открыть в Happ** и fallback ссылку подписки.

## Важно про чужие конфиги

Панель технически умеет импортировать чужие/партнёрские JSON-конфиги как upstream, но использовать и продавать нужно только те конфиги, на которые у тебя есть разрешение. Если upstream не твой, его могут отключить, заменить или забанить в любой момент.

## Hysteria / TUIC / WireGuard

v4 принимает эти протоколы при импорте. Работоспособность зависит от твоего `xray-core`. Если `Xray → Validate` покажет ошибку по протоколу или неизвестному полю, значит установленный core не поддерживает конкретный формат конфига. В этом случае импорт будет сохранён, но gateway не запустит этот upstream, пока не поставишь совместимый core или не очистишь конфиг.
