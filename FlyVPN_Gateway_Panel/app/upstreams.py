from __future__ import annotations

import copy
import json
import re
from typing import Any

PROXY_PROTOCOLS = {"vless", "vmess", "trojan", "shadowsocks"}
SYSTEM_TAGS = {"direct", "block", "dns-out", "dns", "freedom", "blackhole"}


def remark_from_config(data: dict[str, Any]) -> str:
    return str(data.get("remarks") or data.get("remark") or data.get("name") or "").strip()


def safe_tag(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    return value.strip("-") or "upstream"


def proxy_outbounds(data: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ob in data.get("outbounds") or []:
        if not isinstance(ob, dict):
            continue
        proto = str(ob.get("protocol") or "").lower()
        tag = str(ob.get("tag") or "").lower()
        if proto in PROXY_PROTOCOLS and tag not in SYSTEM_TAGS:
            result.append(ob)
    return result


def count_proxy_outbounds(data: dict[str, Any]) -> int:
    return len(proxy_outbounds(data))


def normalize_import(raw: str) -> tuple[dict[str, Any], str, int]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON должен быть объектом")
    cnt = count_proxy_outbounds(data)
    if cnt <= 0:
        raise ValueError("Не найдено proxy outbounds: vless/vmess/trojan/shadowsocks")
    remark = remark_from_config(data) or "Imported VPN"
    return data, remark, cnt


def upstream_to_xray_objects(upstream_id: int, upstream_name: str, config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    outbounds: list[dict[str, Any]] = []
    tags: list[str] = []
    for index, ob in enumerate(proxy_outbounds(config), start=1):
        cloned = copy.deepcopy(ob)
        tag = f"up-{upstream_id}-{safe_tag(str(ob.get('tag') or upstream_name))}-{index}"
        cloned["tag"] = tag

        # Client JSON often has local-only sockopt/routing helpers. They can break server-side gateway mode.
        ss = cloned.get("streamSettings")
        if isinstance(ss, dict):
            sock = ss.get("sockopt")
            if isinstance(sock, dict):
                sock.pop("dialerProxy", None)
                sock.pop("interface", None)
                sock.pop("mark", None)
                if not sock:
                    ss.pop("sockopt", None)
        outbounds.append(cloned)
        tags.append(tag)
    return outbounds, tags
