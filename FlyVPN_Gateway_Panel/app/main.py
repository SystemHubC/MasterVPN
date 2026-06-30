from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen, Request as UrlRequest

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db, xray
from app.upstreams import normalize_import, protocol_summary, count_proxy_outbounds

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

db.init_db()
db.seed_upstreams_from_dir(BASE_DIR / "data" / "upstreams")
for k in [
    "PUBLIC_HOST", "PUBLIC_PORT", "XRAY_CONFIG_PATH", "XRAY_SERVICE_NAME",
    "BRAND_NAME", "VPN_DESCRIPTION", "HAPP_PROFILE_PREFIX", "HAPP_DEEPLINK_PATTERN",
]:
    if os.getenv(k) and not db.setting(k):
        db.set_setting(k, os.getenv(k, ""))

app = FastAPI(title="BlackWing Gateway Panel")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("PANEL_SECRET", "change_me"), same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
tpl = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def is_logged(request: Request) -> bool:
    return bool(request.session.get("logged"))


def require_login(request: Request):
    if not is_logged(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def flash(request: Request, text: str, kind: str = "ok") -> None:
    request.session["flash"] = {"text": text, "kind": kind}


def public_sub_url(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/sub/{token}"


def public_landing_url(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/s/{token}"


def public_api_sub_url(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/api/sub/{token}"


def happ_deeplink(request: Request, token: str) -> str:
    raw_url = public_sub_url(request, token)
    pattern = db.setting("HAPP_DEEPLINK_PATTERN", "happ://add/{url}") or "happ://add/{url}"
    return pattern.replace("{url}", quote(raw_url, safe="")).replace("{raw_url}", raw_url)


def sub_link_count(u: dict[str, Any]) -> int:
    return len(xray.vless_links(u))


def ctx(request: Request, **extra: Any) -> dict[str, Any]:
    f = request.session.pop("flash", None)
    base = {
        "request": request,
        "flash": f,
        "setting": db.setting,
        "days_left": db.days_left,
        "sub_public_url": public_sub_url,
        "landing_public_url": public_landing_url,
        "api_sub_public_url": public_api_sub_url,
        "happ_deeplink": happ_deeplink,
        "sub_link_count": sub_link_count,
        "brand_name": db.setting("BRAND_NAME", "BlackWing"),
        "brand_icon": db.setting("BRAND_ICON", "🪽"),
        "vpn_description": db.setting("VPN_DESCRIPTION", ""),
    }
    base.update(extra)
    return base


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return tpl.TemplateResponse(request, "login.html", ctx(request, title="BlackWing Login"))


@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == os.getenv("ADMIN_USERNAME", "admin") and password == os.getenv("ADMIN_PASSWORD", "change_me"):
        request.session["logged"] = True
        return RedirectResponse("/", status_code=303)
    flash(request, "Неверный логин или пароль", "bad")
    return RedirectResponse("/login", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    require_login(request)
    stats = {
        "users": db.row("SELECT COUNT(*) n FROM users")["n"],
        "active_users": db.row("SELECT COUNT(*) n FROM users WHERE active=1 AND (expires_at IS NULL OR expires_at > ?)", (db.iso(db.utcnow()),))["n"],
        "upstreams": db.row("SELECT COUNT(*) n FROM upstreams")["n"],
        "enabled_upstreams": db.row("SELECT COUNT(*) n FROM upstreams WHERE enabled=1")["n"],
    }
    return tpl.TemplateResponse(request, "dashboard.html", ctx(
        request,
        title=f"{db.setting('BRAND_NAME','BlackWing')} Dashboard",
        stats=stats,
        recent_users=db.rows("SELECT * FROM users ORDER BY id DESC LIMIT 8"),
        upstreams=db.rows("SELECT * FROM upstreams ORDER BY id DESC LIMIT 8"),
    ))


@app.get("/users", response_class=HTMLResponse)
def users(request: Request):
    require_login(request)
    return tpl.TemplateResponse(request, "users.html", ctx(
        request,
        title="Users",
        users=db.rows("SELECT u.*, up.remark upstream_remark FROM users u LEFT JOIN upstreams up ON up.id=u.upstream_id ORDER BY u.id DESC"),
        upstreams=db.rows("SELECT * FROM upstreams WHERE enabled=1 ORDER BY remark"),
    ))


@app.post("/users/create")
def users_create(request: Request, username: str = Form(""), tg_id: str = Form(""), upstream_id: str = Form(""), days: int = Form(3), notes: str = Form("")):
    require_login(request)
    tid = int(tg_id) if tg_id.strip().isdigit() else None
    upid = int(upstream_id) if upstream_id.strip().isdigit() else None
    db.make_user(username=username or "client", tg_id=tid, upstream_id=upid, days=days, notes=notes)
    xray.write_config()
    flash(request, "Клиент создан. Давай ему landing URL: там автокнопка Happ и инструкция.")
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{uid}/update")
def users_update(request: Request, uid: int, username: str = Form(""), upstream_id: str = Form(""), active: str = Form("0"), days_add: int = Form(0), notes: str = Form("")):
    require_login(request)
    u = db.row("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404)
    exp = db.parse_iso(u.get("expires_at")) or db.utcnow()
    if days_add:
        exp = max(exp, db.utcnow()) + timedelta(days=int(days_add))
    upid = int(upstream_id) if upstream_id.strip().isdigit() else None
    db.execute("UPDATE users SET username=?, upstream_id=?, active=?, expires_at=?, notes=? WHERE id=?", (username, upid, 1 if active == "1" else 0, db.iso(exp), notes, uid))
    xray.write_config()
    flash(request, "Клиент обновлён, Xray config пересобран")
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{uid}/rotate-token")
def users_rotate_token(request: Request, uid: int):
    require_login(request)
    if not db.row("SELECT * FROM users WHERE id=?", (uid,)):
        raise HTTPException(404)
    db.rotate_sub_token(uid)
    flash(request, "Секретный subscription token обновлён. Старый URL больше не работает.")
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{uid}/delete")
def users_delete(request: Request, uid: int):
    require_login(request)
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    xray.write_config()
    flash(request, "Клиент удалён")
    return RedirectResponse("/users", status_code=303)


@app.get("/users/{uid}/sub", response_class=PlainTextResponse)
def user_sub_admin(request: Request, uid: int):
    require_login(request)
    u = db.row("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404)
    return "\n".join(xray.vless_links(u)) + "\n"


@app.get("/upstreams", response_class=HTMLResponse)
def upstreams(request: Request):
    require_login(request)
    return tpl.TemplateResponse(request, "upstreams.html", ctx(request, title="Locations", upstreams=db.rows("SELECT * FROM upstreams ORDER BY id DESC")))


def _fetch_url(url: str) -> str:
    req = UrlRequest(url, headers={"User-Agent": "BlackWing-Gateway/4"})
    with urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


@app.post("/upstreams/import")
async def upstream_import(request: Request, name: str = Form(""), source_url: str = Form(""), json_text: str = Form(""), file: UploadFile | None = File(None)):
    require_login(request)
    raw = json_text.strip()
    src = source_url.strip()
    if src:
        raw = _fetch_url(src)
    if file and file.filename:
        raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        data, remark, cnt, protos = normalize_import(raw)
    except Exception as e:
        flash(request, f"Ошибка импорта: {e}", "bad")
        return RedirectResponse("/upstreams", status_code=303)
    nm = name.strip() or remark
    db.execute(
        """INSERT INTO upstreams(name, remark, enabled, json_text, proxy_count, protocol_summary, source_url, last_update_at, created_at)
             VALUES(?,?,?,?,?,?,?,?,?)""",
        (nm, remark, 1, json.dumps(data, ensure_ascii=False, indent=2), cnt, protos, src, db.iso(db.utcnow()) if src else None, db.iso(db.utcnow())),
    )
    xray.write_config()
    flash(request, f"Импортировано: {remark}. Protocols: {protos}. Proxy outbounds: {cnt}")
    return RedirectResponse("/upstreams", status_code=303)


@app.post("/upstreams/{upid}/update")
def upstream_update(request: Request, upid: int, name: str = Form(""), remark: str = Form(""), source_url: str = Form(""), enabled: str = Form("0")):
    require_login(request)
    db.execute("UPDATE upstreams SET name=?, remark=?, source_url=?, enabled=? WHERE id=?", (name.strip() or "Location", remark.strip() or name.strip() or "Location", source_url.strip(), 1 if enabled == "1" else 0, upid))
    xray.write_config()
    flash(request, "Локация обновлена")
    return RedirectResponse("/upstreams", status_code=303)


@app.post("/upstreams/{upid}/refresh")
def upstream_refresh(request: Request, upid: int):
    require_login(request)
    up = db.row("SELECT * FROM upstreams WHERE id=?", (upid,))
    if not up:
        raise HTTPException(404)
    if not up.get("source_url"):
        flash(request, "У этой локации нет Source URL для автообновления", "bad")
        return RedirectResponse("/upstreams", status_code=303)
    try:
        raw = _fetch_url(up["source_url"])
        data, remark, cnt, protos = normalize_import(raw)
        db.execute("UPDATE upstreams SET json_text=?, remark=?, proxy_count=?, protocol_summary=?, last_update_at=?, last_error='' WHERE id=?", (json.dumps(data, ensure_ascii=False, indent=2), remark, cnt, protos, db.iso(db.utcnow()), upid))
        xray.write_config()
        flash(request, f"Локация обновлена: {remark}")
    except Exception as e:
        db.execute("UPDATE upstreams SET last_error=? WHERE id=?", (str(e)[:500], upid))
        flash(request, f"Ошибка обновления: {e}", "bad")
    return RedirectResponse("/upstreams", status_code=303)


@app.post("/upstreams/{upid}/toggle")
def upstream_toggle(request: Request, upid: int):
    require_login(request)
    up = db.row("SELECT * FROM upstreams WHERE id=?", (upid,))
    if up:
        db.execute("UPDATE upstreams SET enabled=? WHERE id=?", (0 if up["enabled"] else 1, upid))
        xray.write_config()
    return RedirectResponse("/upstreams", status_code=303)


@app.post("/upstreams/{upid}/delete")
def upstream_delete(request: Request, upid: int):
    require_login(request)
    db.execute("UPDATE users SET upstream_id=NULL WHERE upstream_id=?", (upid,))
    db.execute("DELETE FROM upstreams WHERE id=?", (upid,))
    xray.write_config()
    flash(request, "Локация удалена")
    return RedirectResponse("/upstreams", status_code=303)


@app.get("/xray", response_class=HTMLResponse)
def xray_page(request: Request):
    require_login(request)
    cfg = xray.build_config()
    return tpl.TemplateResponse(request, "xray.html", ctx(request, title="Xray", cfg=json.dumps(cfg, ensure_ascii=False, indent=2)))


@app.post("/xray/rebuild")
def xray_rebuild(request: Request):
    require_login(request)
    path = xray.write_config()
    flash(request, f"Конфиг записан: {path}")
    return RedirectResponse("/xray", status_code=303)


@app.post("/xray/validate")
def xray_validate(request: Request):
    require_login(request)
    ok, out = xray.validate_with_xray()
    flash(request, ("✅ Xray config валиден" if ok else "❌ Ошибка Xray: ") + out[:1800], "ok" if ok else "bad")
    return RedirectResponse("/xray", status_code=303)


@app.post("/xray/restart")
def xray_restart(request: Request):
    require_login(request)
    xray.write_config()
    ok, out = xray.restart_xray()
    flash(request, ("✅ Xray перезапущен" if ok else "❌ Не удалось перезапустить Xray: ") + out[:1800], "ok" if ok else "bad")
    return RedirectResponse("/xray", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_get(request: Request):
    require_login(request)
    return tpl.TemplateResponse(request, "settings.html", ctx(request, title="Settings"))


@app.post("/settings")
def settings_post(request: Request, PUBLIC_HOST: str = Form(...), PUBLIC_PORT: str = Form(...), XRAY_CONFIG_PATH: str = Form(...), XRAY_SERVICE_NAME: str = Form(...), BRAND_NAME: str = Form(...), BRAND_ICON: str = Form("🪽"), VPN_DESCRIPTION: str = Form(""), HAPP_PROFILE_PREFIX: str = Form(""), HAPP_DEEPLINK_PATTERN: str = Form("happ://add/{url}"), AUTO_RESTART_XRAY: str = Form("0")):
    require_login(request)
    data = {
        "PUBLIC_HOST": PUBLIC_HOST,
        "PUBLIC_PORT": PUBLIC_PORT,
        "XRAY_CONFIG_PATH": XRAY_CONFIG_PATH,
        "XRAY_SERVICE_NAME": XRAY_SERVICE_NAME,
        "BRAND_NAME": BRAND_NAME,
        "BRAND_ICON": BRAND_ICON,
        "VPN_DESCRIPTION": VPN_DESCRIPTION,
        "HAPP_PROFILE_PREFIX": HAPP_PROFILE_PREFIX or BRAND_NAME,
        "HAPP_DEEPLINK_PATTERN": HAPP_DEEPLINK_PATTERN,
        "AUTO_RESTART_XRAY": "1" if AUTO_RESTART_XRAY == "1" else "0",
    }
    for k, v in data.items():
        db.set_setting(k, v.strip())
    xray.write_config()
    flash(request, "Настройки сохранены")
    return RedirectResponse("/settings", status_code=303)


def _get_public_user_by_token(token: str) -> dict[str, Any]:
    u = db.row("SELECT * FROM users WHERE sub_token=?", (token,))
    if not u or not u.get("active"):
        raise HTTPException(404)
    exp = db.parse_iso(u.get("expires_at"))
    if exp and exp <= db.utcnow():
        raise HTTPException(403, "subscription expired")
    return u


@app.get("/s/{token}", response_class=HTMLResponse)
def subscription_landing(request: Request, token: str):
    u = _get_public_user_by_token(token)
    links = xray.vless_links(u)
    if not links:
        raise HTTPException(404, "no locations available")
    return tpl.TemplateResponse(request, "subscription.html", ctx(
        request,
        public_user=u,
        links=links,
        raw_sub_url=public_sub_url(request, token),
        api_sub_url=public_api_sub_url(request, token),
        happ_url=happ_deeplink(request, token),
    ))


@app.get("/happ/{token}")
def happ_redirect(request: Request, token: str):
    _get_public_user_by_token(token)
    return RedirectResponse(happ_deeplink(request, token), status_code=302)


@app.get("/sub/{token}", response_class=PlainTextResponse)
def subscription(token: str):
    u = _get_public_user_by_token(token)
    links = xray.vless_links(u)
    if not links:
        raise HTTPException(404, "no locations available")
    return "\n".join(links) + "\n"


@app.get("/api/sub/{token}")
def subscription_json(request: Request, token: str):
    try:
        u = _get_public_user_by_token(token)
    except HTTPException as e:
        return JSONResponse({"isFound": False, "links": [], "error": e.detail}, status_code=e.status_code)
    links = xray.vless_links(u)
    return {
        "isFound": True,
        "brand": db.setting("BRAND_NAME", "BlackWing"),
        "description": db.setting("VPN_DESCRIPTION", ""),
        "user": {
            "username": u["username"],
            "email": u["email"],
            "expiresAt": u["expires_at"],
            "isActive": bool(u["active"]),
            "daysLeft": db.days_left(u),
        },
        "links": links,
        "linkCount": len(links),
        "subscriptionUrl": public_sub_url(request, token),
        "landingUrl": public_landing_url(request, token),
        "happUrl": happ_deeplink(request, token),
    }


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "brand": db.setting("BRAND_NAME", "BlackWing"),
        "users": db.row("SELECT COUNT(*) n FROM users")["n"],
        "activeUsers": db.row("SELECT COUNT(*) n FROM users WHERE active=1 AND (expires_at IS NULL OR expires_at > ?)", (db.iso(db.utcnow()),))["n"],
        "enabledUpstreams": db.row("SELECT COUNT(*) n FROM upstreams WHERE enabled=1")["n"],
        "xrayConfigPath": str(xray.config_path()),
        "publicHost": xray.public_host(),
        "publicPort": xray.public_port(),
    }
