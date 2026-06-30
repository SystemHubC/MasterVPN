from __future__ import annotations

import copy
import json
import re
from typing import Any

# Supported upstream protocols. The panel imports them as SERVER-SIDE outbounds.
# Whether a protocol actually works depends on the installed Xray-core build.
PROXY_PROTOCOLS = {
    "vless", "vmess", "trojan", "shadowsocks", "ss",
    "hysteria", "hysteria2", "hy2", "tuic", "wireguard",
    "socks", "http",
}
SYSTEM_PROTOCOLS = {"freedom", "blackhole", "dns", "loopback"}
SYSTEM_TAGS = {"direct", "block", "dns-out", "dns", "freedom", "blackhole", "api"}


def remark_from_config(data: dict[str, Any]) -> str:
    return str(data.get("remarks") or data.get("remark") or data.get("name") or "").strip()


def safe_tag(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    return value.strip("-") or "upstream"


def _is_proxy_outbound(ob: dict[str, Any]) -> bool:
    proto = str(ob.get("protocol") or "").lower()
    tag = str(ob.get("tag") or "").lower()
    if proto in SYSTEM_PROTOCOLS or tag in SYSTEM_TAGS:
        return False
    if proto in PROXY_PROTOCOLS:
        return True
    # Many imported client configs call the main outbound "proxy". Accept it even
    # when the protocol is new/unknown, but never accept direct/block/dns helpers.
    if tag in {"proxy", "vpn", "upstream", "main"} and proto:
        return True
    return False


def proxy_outbounds(data: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ob in data.get("outbounds") or []:
        if isinstance(ob, dict) and _is_proxy_outbound(ob):
            result.append(ob)
    return result


def protocol_summary(data: dict[str, Any]) -> str:
    protos: list[str] = []
    for ob in proxy_outbounds(data):
        proto = str(ob.get("protocol") or "unknown").lower()
        if proto == "ss":
            proto = "shadowsocks"
        if proto not in protos:
            protos.append(proto)
    return ", ".join(protos) if protos else "none"


def count_proxy_outbounds(data: dict[str, Any]) -> int:
    return len(proxy_outbounds(data))


def normalize_import(raw: str) -> tuple[dict[str, Any], str, int, str]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON должен быть объектом")
    cnt = count_proxy_outbounds(data)
    if cnt <= 0:
        raise ValueError(
            "Не найден proxy outbound. Поддерживаются: vless/vmess/trojan/shadowsocks/"
            "hysteria/hysteria2/tuic/wireguard/socks/http. Проверь, что основной outbound "
            "имеет tag proxy/vpn/main или один из этих protocol."
        )
    remark = remark_from_config(data) or "Imported VPN"
    return data, remark, cnt, protocol_summary(data)


def _strip_client_only_fields(cloned: dict[str, Any]) -> dict[str, Any]:
    # Client configs often contain local-only helpers. They may break server-side gateway mode.
    ss = cloned.get("streamSettings")
    if isinstance(ss, dict):
        # "finalmask" is used by some mobile clients/forks and often breaks vanilla xray validation.
        # Strip it in gateway mode; if your custom xray supports it, import through advanced mode later.
        ss.pop("finalmask", None)
        sock = ss.get("sockopt")
        if isinstance(sock, dict):
            sock.pop("dialerProxy", None)
            sock.pop("interface", None)
            sock.pop("mark", None)
            sock.pop("TcpNoDelay", None)
            if not sock:
                ss.pop("sockopt", None)
    return cloned


def upstream_to_xray_objects(upstream_id: int, upstream_name: str, config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    outbounds: list[dict[str, Any]] = []
    tags: list[str] = []
    for index, ob in enumerate(proxy_outbounds(config), start=1):
        cloned = copy.deepcopy(ob)
        tag = f"up-{upstream_id}-{safe_tag(str(ob.get('tag') or upstream_name))}-{index}"
        cloned["tag"] = tag
        cloned = _strip_client_only_fields(cloned)
        outbounds.append(cloned)
        tags.append(tag)
    return outbounds, tags
