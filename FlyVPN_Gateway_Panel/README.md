# 🪽 FlyVPN Gateway Panel

Красивая web-панель для схемы **клиент → твой Xray Gateway → импортированные JSON VPN-конфиги**.

Она нужна именно для твоего сценария, когда есть готовые клиентские JSON-конфиги, но ты хочешь выдавать покупателям **свои ссылки** и управлять доступом по сроку.

## Что умеет

- Тёмная панель в стиле современных VPN-панелей.
- Импорт готовых Happ/Xray JSON-конфигов.
- Автоматически вытаскивает `outbounds` с `vless/vmess/trojan/shadowsocks`.
- Генерирует серверный Xray config.
- Создаёт отдельный UUID каждому клиенту.
- Роутит каждого клиента в выбранную страну/upstream.
- Выдаёт subscription URL `/sub/<token>` и JSON `/api/sub/<token>`.
- Управление пользователями: срок, активность, страна, заметки.
- Управление upstream-конфигами: включить/выключить/удалить.
- Кнопки rebuild/validate/restart Xray.

## Важная схема

```text
Happ пользователя
  ↓ vless://USER_UUID@твой_vps:8443
Твой Xray Gateway
  ↓ upstream outbound из импортированного JSON
Готовый VPN-сервер из конфига
```

Покупатель не видит upstream UUID и не получает твой исходный JSON.

## Быстрый запуск на VPS

```bash
unzip FlyVPN_Gateway_Panel.zip
cd FlyVPN_Gateway_Panel
sudo bash install.sh
```

Открой:

```text
http://IP_СЕРВЕРА:8090
```

Логин и пароль находятся в `.env`:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_me
```

Поменяй пароль сразу.

## Настройка

В панели зайди в **Settings** и проверь:

```text
PUBLIC_HOST=IP или домен твоего VPS
PUBLIC_PORT=8443
XRAY_CONFIG_PATH=/etc/flyvpn/xray/config.json
XRAY_SERVICE_NAME=xray
```

Потом зайди в **Xray → Записать config**.

## Установка Xray

Поставь xray-core любым нормальным способом. Если у тебя уже стоит `/usr/local/bin/xray`, можно использовать готовый service:

```bash
sudo cp systemd/xray.service /etc/systemd/system/xray.service
sudo systemctl daemon-reload
sudo systemctl enable --now xray
```

Открой порт:

```bash
sudo ufw allow 8443/tcp
sudo ufw allow 8443/udp
```

Проверка:

```bash
sudo systemctl status xray --no-pager -l
sudo ss -lntup | grep 8443
```

## Как пользоваться

1. **Upstreams** → импортируй JSON-конфиг страны.
2. **Users** → создай клиента и выбери страну.
3. **Xray** → Rebuild → Validate → Restart.
4. Открой ссылку пользователя `/sub/<token>`.
5. Импортируй её в Happ.

## Что уже добавлено

В папке `data/upstreams` уже лежат твои конфиги:

- Россия
- Германия
- Швеция
- Нидерланды
- Великобритания
- Великобритания [2]

При первом запуске панель сама добавит их в SQLite.

## Ограничение

Технически эта схема использует готовые upstream-конфиги. Используй только те конфиги/серверы, на которые у тебя есть право. Если upstream умрёт — соответствующая страна тоже перестанет работать.
