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



def subscription_mode() -> str:
    # gateway: client receives VLESS links to your VPS, VPS routes through upstreams
    # direct: client receives a Happ/Xray JSON based on imported upstreams directly
    # hybrid: /sub returns gateway links, /client/{token}.json returns direct JSON
    mode = (db.setting("SUBSCRIPTION_MODE", env("SUBSCRIPTION_MODE", "direct")) or "direct").strip().lower()
    return mode if mode in {"direct", "gateway", "hybrid"} else "direct"


def _client_base_config(label: str) -> dict[str, Any]:
    return {
        "dns": {
            "tag": "dns-inbound",
            "queryStrategy": "UseIPv4",
            "servers": [
                "https://8.8.8.8/dns-query",
                "https://8.8.4.4/dns-query",
                "https://1.1.1.1/dns-query",
                "https://1.0.0.1/dns-query",
            ],
        },
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks",
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True, "userLevel": 8},
                "sniffing": {"enabled": True, "routeOnly": True, "destOverride": ["http", "tls", "quic"]},
            },
            {
                "tag": "http",
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
                "settings": {"allowTransparent": False, "userLevel": 8},
                "sniffing": {"enabled": True, "routeOnly": True, "destOverride": ["http", "tls", "quic"]},
            },
        ],
        "outbounds": [],
        "policy": {
            "levels": {"8": {"connIdle": 300, "downlinkOnly": 1, "handshake": 4, "uplinkOnly": 1}},
            "system": {"statsOutboundDownlink": True, "statsOutboundUplink": True},
        },
        "remarks": label,
        "routing": {
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "port": 53, "outboundTag": "dns-out"},
                {"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
            ],
        },
        "stats": {},
    }


def _clean_location_name(up: dict[str, Any]) -> str:
    raw = str(up.get("remark") or up.get("name") or "Location").strip()
    prefix = profile_prefix()
    brand = brand_name()
    for lead in (f"{prefix} · ", f"{brand} · "):
        if raw.startswith(lead):
            raw = raw[len(lead):].strip()
    if " — " in raw and len(raw) > 64:
        raw = raw.split(" — ", 1)[0].strip()
    return raw or "Location"


def subscription_title() -> str:
    return db.setting("HAPP_SUBSCRIPTION_TITLE", env("HAPP_SUBSCRIPTION_TITLE", f"{brand_name()} VPN")) or f"{brand_name()} VPN"


def update_interval_hours() -> int:
    try:
        return max(1, int(db.setting("SUB_UPDATE_INTERVAL_HOURS", env("SUB_UPDATE_INTERVAL_HOURS", "1"))))
    except Exception:
        return 1


def default_traffic_limit_gb() -> int:
    try:
        return max(0, int(db.setting("DEFAULT_TRAFFIC_LIMIT_GB", env("DEFAULT_TRAFFIC_LIMIT_GB", "10"))))
    except Exception:
        return 10


def _location_suffix() -> str:
    return db.setting("HAPP_LOCATION_SUFFIX", env("HAPP_LOCATION_SUFFIX", "🔥 Новые блокировки")).strip()


def _display_location_label(up: dict[str, Any]) -> str:
    loc = _clean_location_name(up)
    suffix = _location_suffix()
    if suffix and suffix not in loc:
        return f"{loc} ({suffix})"
    return loc


def _direct_label(u: dict[str, Any], ups: list[dict[str, Any]]) -> str:
    if len(ups) == 1:
        return _display_location_label(ups[0])
    return subscription_title()

def _system_outbounds_for_client() -> list[dict[str, Any]]:
    return [
        {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIP"}},
        {"tag": "block", "protocol": "blackhole", "settings": {"response": {"type": "http"}}},
        {"tag": "dns-out", "protocol": "dns"},
    ]


def _sanitize_client_routing(data: dict[str, Any], proxy_tags: list[str]) -> dict[str, Any]:
    # Preserve useful direct/block rules when possible, but avoid invalid/old geosite names that often break clients.
    routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
    clean_rules: list[dict[str, Any]] = []
    for r in routing.get("rules") or []:
        if not isinstance(r, dict):
            continue
        rule = json.loads(json.dumps(r, ensure_ascii=False))
        # geosite:TORRENT / geosite:torrent is often missing in geosite.dat; bittorrent protocol rule is safer.
        domains = rule.get("domain")
        if isinstance(domains, list) and any(str(x).lower() in {"geosite:torrent", "geosite:torrrent"} for x in domains):
            continue
        if rule.get("inboundTag"):
            continue
        # If the imported rule points to an old proxy tag, send it to our first proxy/balancer.
        if rule.get("outboundTag") and str(rule.get("outboundTag")).startswith("proxy"):
            if len(proxy_tags) == 1:
                rule["outboundTag"] = proxy_tags[0]
            else:
                rule.pop("outboundTag", None)
                rule["balancerTag"] = "blackwing-balancer"
        clean_rules.append(rule)
    base_rules = [
        {"type": "field", "port": 53, "outboundTag": "dns-out"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"},
    ]
    final_rule: dict[str, Any] = {"type": "field", "network": "tcp,udp"}
    if len(proxy_tags) == 1:
        final_rule["outboundTag"] = proxy_tags[0]
    else:
        final_rule["balancerTag"] = "blackwing-balancer"
    return {
        "domainMatcher": routing.get("domainMatcher", "hybrid"),
        "domainStrategy": routing.get("domainStrategy", "IPIfNonMatch"),
        "rules": base_rules + clean_rules + [final_rule],
    }


def _prepare_single_direct_config(u: dict[str, Any], up: dict[str, Any]) -> dict[str, Any]:
    """Return one Happ/Xray JSON profile for one upstream, preserving imported config."""
    try:
        original = json.loads(up.get("json_text") or "{}")
    except Exception:
        original = {}
    if not isinstance(original, dict):
        original = {}
    cfg = json.loads(json.dumps(original, ensure_ascii=False))

    label = _display_location_label(up)
    cfg["remarks"] = label
    cfg["name"] = label
    cfg.setdefault("log", {"loglevel": "warning"})
    return cfg


def direct_client_configs(u: dict[str, Any]) -> list[dict[str, Any]]:
    """Happ-style subscription: one JSON profile per location.

    This matches apps that show a subscription header and a list of locations:
    🇳🇱 Netherlands / 🇫🇮 Finland / 🇩🇪 Germany, each row as `VLESS | JSON` or `HYSTERIA | JSON`.
    """
    ups = upstreams_for_user(u)
    if not ups:
        return [_client_base_config(f"{subscription_title()} · No locations")]
    return [_prepare_single_direct_config(u, up) for up in ups]


def merged_direct_client_config(u: dict[str, Any]) -> dict[str, Any]:
    """Old direct mode: merge all upstream outbounds into one client JSON."""
    ups = upstreams_for_user(u)
    if not ups:
        return _client_base_config(f"{profile_prefix()} · No locations")
    if len(ups) == 1:
        return _prepare_single_direct_config(u, ups[0])

    cfg = _client_base_config(_direct_label(u, ups))
    proxy_tags: list[str] = []
    outbounds: list[dict[str, Any]] = []
    first_routing_source: dict[str, Any] | None = None
    n = 0
    from app.upstreams import proxy_outbounds
    for up in ups:
        try:
            data = json.loads(up["json_text"] or "{}")
        except Exception:
            continue
        if first_routing_source is None:
            first_routing_source = data
        for ob in proxy_outbounds(data):
            n += 1
            cloned = json.loads(json.dumps(ob, ensure_ascii=False))
            cloned["tag"] = "proxy" if n == 1 else f"proxy-{n}"
            outbounds.append(cloned)
            proxy_tags.append(cloned["tag"])
    if not proxy_tags:
        cfg["outbounds"] = _system_outbounds_for_client()
        return cfg
    cfg["outbounds"] = outbounds + _system_outbounds_for_client()
    cfg["routing"] = _sanitize_client_routing(first_routing_source or {}, proxy_tags)
    if len(proxy_tags) > 1:
        cfg["routing"]["balancers"] = [{
            "tag": "blackwing-balancer",
            "selector": proxy_tags,
            "fallbackTag": proxy_tags[0],
            "strategy": {"type": "random"},
        }]
    return cfg


def direct_client_config(u: dict[str, Any]) -> dict[str, Any]:
    # Backward-compatible single object for old callers.
    return merged_direct_client_config(u)


def link_label(up: dict[str, Any]) -> str:
    # For Happ list rows keep labels short like: "🇩🇪 Германия (🔥 Новые блокировки)".
    return _display_location_label(up)


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
