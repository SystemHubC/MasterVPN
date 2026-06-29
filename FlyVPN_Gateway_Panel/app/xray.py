from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app import db
from app.upstreams import upstream_to_xray_objects, safe_tag


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def public_host() -> str:
    return db.setting("PUBLIC_HOST", env("PUBLIC_HOST", "127.0.0.1"))


def public_port() -> int:
    try:
        return int(db.setting("PUBLIC_PORT", env("PUBLIC_PORT", "8443")))
    except Exception:
        return 8443


def config_path() -> Path:
    return Path(db.setting("XRAY_CONFIG_PATH", env("XRAY_CONFIG_PATH", "/etc/flyvpn/xray/config.json")))


def build_config() -> dict[str, Any]:
    users = db.active_users()
    upstreams = db.rows("SELECT * FROM upstreams WHERE enabled=1 ORDER BY id ASC")

    clients = []
    for u in users:
        clients.append({"id": u["uuid"], "email": u["email"], "level": 0})

    outbounds: list[dict[str, Any]] = []
    balancers: list[dict[str, Any]] = []
    upstream_route: dict[int, tuple[str, str]] = {}

    for up in upstreams:
        try:
            cfg = json.loads(up["json_text"])
        except Exception:
            continue
        obs, tags = upstream_to_xray_objects(int(up["id"]), str(up["name"]), cfg)
        outbounds.extend(obs)
        if not tags:
            continue
        if len(tags) == 1:
            upstream_route[int(up["id"])] = ("outbound", tags[0])
        else:
            btag = f"bal-{int(up['id'])}-{safe_tag(str(up['name']))}"
            balancers.append({"tag": btag, "selector": tags, "strategy": {"type": "random"}})
            upstream_route[int(up["id"])] = ("balancer", btag)

    outbounds.extend([
        {"tag": "DIRECT", "protocol": "freedom", "settings": {"domainStrategy": "UseIP"}},
        {"tag": "BLOCK", "protocol": "blackhole"},
        {"tag": "api", "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}},
    ])

    rules: list[dict[str, Any]] = [
        {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "BLOCK"},
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "DIRECT"},
    ]

    default_target: tuple[str, str] | None = None
    if upstream_route:
        default_target = next(iter(upstream_route.values()))

    for u in users:
        upid = u.get("upstream_id")
        target = upstream_route.get(int(upid)) if upid else default_target
        if not target:
            continue
        kind, tag = target
        rule = {"type": "field", "user": [u["email"]]}
        if kind == "balancer":
            rule["balancerTag"] = tag
        else:
            rule["outboundTag"] = tag
        rules.append(rule)

    if default_target:
        kind, tag = default_target
        fallback = {"type": "field", "inboundTag": ["flyvpn-users"]}
        if kind == "balancer":
            fallback["balancerTag"] = tag
        else:
            fallback["outboundTag"] = tag
        rules.append(fallback)

    return {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 61000,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            },
            {
                "tag": "flyvpn-users",
                "listen": "0.0.0.0",
                "port": public_port(),
                "protocol": "vless",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {"network": "tcp", "security": "none", "tcpSettings": {"header": {"type": "none"}}},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            },
        ],
        "outbounds": outbounds,
        "routing": {"domainStrategy": "IPIfNonMatch", "balancers": balancers, "rules": rules},
    }


def write_config() -> Path:
    cfg = build_config()
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def restart_xray() -> tuple[bool, str]:
    service = db.setting("XRAY_SERVICE_NAME", env("XRAY_SERVICE_NAME", "xray"))
    try:
        p = subprocess.run(["systemctl", "restart", service], capture_output=True, text=True, timeout=20)
        ok = p.returncode == 0
        return ok, (p.stdout + p.stderr).strip() or ("ok" if ok else "failed")
    except Exception as e:
        return False, str(e)


def validate_with_xray() -> tuple[bool, str]:
    path = write_config()
    exe = "/usr/local/bin/xray" if Path("/usr/local/bin/xray").exists() else "xray"
    try:
        p = subprocess.run([exe, "run", "-test", "-config", str(path)], capture_output=True, text=True, timeout=20)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except Exception as e:
        return False, str(e)


def vless_link(u: dict[str, Any], label: str | None = None) -> str:
    host = public_host()
    port = public_port()
    label = label or f"FlyVPN-{u.get('username') or u.get('email')}"
    from urllib.parse import quote
    return f"vless://{u['uuid']}@{host}:{port}?encryption=none&type=tcp&security=none#{quote(label)}"
