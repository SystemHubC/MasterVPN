from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app import db
from app.upstreams import upstream_to_xray_objects, safe_tag


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def brand_name() -> str:
    return db.setting("BRAND_NAME", env("BRAND_NAME", "BlackWing")) or "BlackWing"


def vpn_description() -> str:
    return db.setting("VPN_DESCRIPTION", env("VPN_DESCRIPTION", "Private VPN subscription"))


def profile_prefix() -> str:
    return db.setting("HAPP_PROFILE_PREFIX", env("HAPP_PROFILE_PREFIX", brand_name())) or brand_name()


def public_host() -> str:
    return db.setting("PUBLIC_HOST", env("PUBLIC_HOST", "127.0.0.1"))


def public_port() -> int:
    try:
        return int(db.setting("PUBLIC_PORT", env("PUBLIC_PORT", "8443")))
    except Exception:
        return 8443


def config_path() -> Path:
    return Path(db.setting("XRAY_CONFIG_PATH", env("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")))


def enabled_upstreams() -> list[dict[str, Any]]:
    return db.rows("SELECT * FROM upstreams WHERE enabled=1 ORDER BY id ASC")


def upstreams_for_user(u: dict[str, Any], upstreams: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    all_upstreams = upstreams if upstreams is not None else enabled_upstreams()
    upid = u.get("upstream_id")
    if upid:
        return [up for up in all_upstreams if int(up["id"]) == int(upid)]
    return list(all_upstreams)


def identity_for_user_location(u: dict[str, Any], upstream_id: int) -> dict[str, str]:
    namespace = uuid.UUID(str(u["uuid"]))
    loc_uuid = str(uuid.uuid5(namespace, f"blackwing-upstream-{int(upstream_id)}"))
    email = f"{u['email']}_loc_{int(upstream_id)}"
    return {"uuid": loc_uuid, "email": email}


def build_config() -> dict[str, Any]:
    users = db.active_users()
    upstreams = enabled_upstreams()

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

    clients: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = [
        {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "DIRECT"},
    ]

    for u in users:
        for up in upstreams_for_user(u, upstreams):
            upid = int(up["id"])
            target = upstream_route.get(upid)
            if not target:
                continue
            ident = identity_for_user_location(u, upid)
            clients.append({"id": ident["uuid"], "email": ident["email"], "level": 0})
            kind, tag = target
            rule: dict[str, Any] = {"type": "field", "user": [ident["email"]]}
            if kind == "balancer":
                rule["balancerTag"] = tag
            else:
                rule["outboundTag"] = tag
            rules.append(rule)

    outbounds.extend([
        {"tag": "DIRECT", "protocol": "freedom", "settings": {"domainStrategy": "UseIP"}},
        {"tag": "BLOCK", "protocol": "blackhole"},
        {"tag": "api", "protocol": "freedom"},
    ])

    return {
        "log": {
            "loglevel": "warning",
            "access": "/var/log/xray/blackwing-access.log",
            "error": "/var/log/xray/blackwing-error.log",
        },
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
                "tag": "blackwing-users",
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
        p = subprocess.run(["systemctl", "restart", service], capture_output=True, text=True, timeout=30)
        ok = p.returncode == 0
        return ok, (p.stdout + p.stderr).strip() or ("ok" if ok else "failed")
    except Exception as e:
        return False, str(e)


def validate_with_xray() -> tuple[bool, str]:
    path = write_config()
    exe = "/usr/local/bin/xray" if Path("/usr/local/bin/xray").exists() else "xray"
    try:
        p = subprocess.run([exe, "run", "-test", "-config", str(path)], capture_output=True, text=True, timeout=30)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except Exception as e:
        return False, str(e)


def link_label(up: dict[str, Any]) -> str:
    loc = str(up.get("remark") or up.get("name") or "Location").strip()
    prefix = profile_prefix()
    desc = vpn_description().strip()
    # Happ usually displays the URL fragment as profile name. Keep it readable and branded.
    if desc:
        return f"{prefix} · {loc} — {desc[:70]}"
    return f"{prefix} · {loc}"


def vless_link_for_location(u: dict[str, Any], up: dict[str, Any]) -> str:
    ident = identity_for_user_location(u, int(up["id"]))
    return f"vless://{ident['uuid']}@{public_host()}:{public_port()}?encryption=none&type=tcp&security=none#{quote(link_label(up))}"


def vless_links(u: dict[str, Any]) -> list[str]:
    return [vless_link_for_location(u, up) for up in upstreams_for_user(u)]


def vless_link(u: dict[str, Any], label: str | None = None) -> str:
    links = vless_links(u)
    if links:
        return links[0]
    label = label or f"{profile_prefix()} · {u.get('username') or u.get('email')}"
    return f"vless://{u['uuid']}@{public_host()}:{public_port()}?encryption=none&type=tcp&security=none#{quote(label)}"
