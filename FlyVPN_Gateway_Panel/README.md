# FlyVPN Gateway Panel v3

Собственная панель FlyVPN Gateway для схемы:

```text
Покупатель -> твой VPS/Xray inbound -> импортированный Xray/Happ JSON upstream -> интернет
```

## Главное в v3

- Красивый тёмный интерфейс, вдохновлённый современными VPN-панелями, без копирования чужого кода.
- Импорт чужих/партнёрских Happ/Xray JSON-конфигов как upstream-локаций. Используй только конфиги, на которые у тебя есть право.
- Один клиент может получить все включённые локации сразу: подписка отдаёт несколько `vless://` строк.
- Больше нет публичных `/users/1/sub`: клиентский URL только `/sub/<long_random_token>`. Token генерируется через `secrets.token_urlsafe(32)`.
- Для каждой пары клиент+локация генерируется отдельный стабильный UUID, чтобы Xray маршрутизировал трафик в нужную страну.
- `install.sh` сам ставит зависимости, чинит `.env`, ставит Xray, создаёт systemd-сервис и открывает порты.
- В панели: Users, Locations, Xray Rebuild/Validate/Restart, Settings, secure token rotate.

## Установка/обновление на VPS

```bash
cd /opt
rm -rf /opt/MasterVPN
git clone https://github.com/SystemHubC/MasterVPN.git /opt/MasterVPN
cd /opt/MasterVPN/FlyVPN_Gateway_Panel
bash install.sh
```

Проверка:

```bash
systemctl status flyvpn-panel --no-pager -l
systemctl status xray --no-pager -l
curl http://127.0.0.1:8090/api/health
ss -lntup | grep -E '8090|8443'
```

Открыть панель:

```text
http://SERVER_IP:8090
```

Логин/пароль лежат в:

```text
/opt/flyvpn-gateway-panel/.env
```

## Как получить несколько локаций

В Users при создании клиента выбери `🌐 Все локации`. Тогда `/sub/<token>` отдаст все включённые страны отдельными строками:

```text
vless://uuid1@SERVER_IP:8443?...#FlyVPN-Германия
vless://uuid2@SERVER_IP:8443?...#FlyVPN-Швеция
vless://uuid3@SERVER_IP:8443?...#FlyVPN-Нидерланды
```

Если выбрать одну страну, подписка отдаст только её.

## Важное ограничение

Gateway mode проксирует трафик через твой VPS, а затем через импортированный upstream. Если upstream JSON умер, неверный, заблокирован или у тебя нет права им пользоваться, эта локация работать не будет.
