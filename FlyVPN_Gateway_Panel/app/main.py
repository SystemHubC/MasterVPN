from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Response, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.upstreams import normalize_import
from app import xray

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

db.init_db()
db.seed_upstreams_from_dir(BASE_DIR / "data" / "upstreams")
for k in ["PUBLIC_HOST", "PUBLIC_PORT", "XRAY_CONFIG_PATH", "XRAY_SERVICE_NAME"]:
    if os.getenv(k) and not db.setting(k):
        db.set_setting(k, os.getenv(k, ""))

app = FastAPI(title="FlyVPN Gateway Panel")
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


def ctx(request: Request, **extra: Any) -> dict[str, Any]:
    f = request.session.pop("flash", None)
    base = {"request": request, "flash": f, "setting": db.setting, "days_left": db.days_left}
    base.update(extra)
    return base


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return tpl.TemplateResponse("login.html", ctx(request))


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
    return tpl.TemplateResponse("dashboard.html", ctx(request, stats=stats, recent_users=db.rows("SELECT * FROM users ORDER BY id DESC LIMIT 8"), upstreams=db.rows("SELECT * FROM upstreams ORDER BY id DESC LIMIT 8")))


@app.get("/users", response_class=HTMLResponse)
def users(request: Request):
    require_login(request)
    return tpl.TemplateResponse("users.html", ctx(request, users=db.rows("SELECT u.*, up.remark upstream_remark FROM users u LEFT JOIN upstreams up ON up.id=u.upstream_id ORDER BY u.id DESC"), upstreams=db.rows("SELECT * FROM upstreams WHERE enabled=1 ORDER BY remark")))


@app.post("/users/create")
def users_create(request: Request, username: str = Form(""), tg_id: str = Form(""), upstream_id: str = Form(""), days: int = Form(3), notes: str = Form("")):
    require_login(request)
    tid = int(tg_id) if tg_id.strip().isdigit() else None
    upid = int(upstream_id) if upstream_id.strip().isdigit() else None
    db.make_user(username=username or "client", tg_id=tid, upstream_id=upid, days=days, notes=notes)
    xray.write_config()
    flash(request, "Пользователь создан, Xray config пересобран")
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
    flash(request, "Пользователь обновлён, Xray config пересобран")
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{uid}/delete")
def users_delete(request: Request, uid: int):
    require_login(request)
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    xray.write_config()
    flash(request, "Пользователь удалён")
    return RedirectResponse("/users", status_code=303)


@app.get("/users/{uid}/sub", response_class=PlainTextResponse)
def user_sub_admin(request: Request, uid: int):
    require_login(request)
    u = db.row("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404)
    return xray.vless_link(u)


@app.get("/upstreams", response_class=HTMLResponse)
def upstreams(request: Request):
    require_login(request)
    return tpl.TemplateResponse("upstreams.html", ctx(request, upstreams=db.rows("SELECT * FROM upstreams ORDER BY id DESC")))


@app.post("/upstreams/import")
async def upstream_import(request: Request, name: str = Form(""), json_text: str = Form(""), file: UploadFile | None = File(None)):
    require_login(request)
    raw = json_text.strip()
    if file and file.filename:
        raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        data, remark, cnt = normalize_import(raw)
    except Exception as e:
        flash(request, f"Ошибка импорта: {e}", "bad")
        return RedirectResponse("/upstreams", status_code=303)
    nm = name.strip() or remark
    db.execute("INSERT INTO upstreams(name, remark, enabled, json_text, proxy_count, created_at) VALUES(?,?,?,?,?,?)", (nm, remark, 1, json.dumps(data, ensure_ascii=False, indent=2), cnt, db.iso(db.utcnow())))
    xray.write_config()
    flash(request, f"Импортировано: {remark}, proxy outbounds: {cnt}")
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
    flash(request, "Конфиг удалён")
    return RedirectResponse("/upstreams", status_code=303)


@app.get("/xray", response_class=HTMLResponse)
def xray_page(request: Request):
    require_login(request)
    cfg = xray.build_config()
    return tpl.TemplateResponse("xray.html", ctx(request, cfg=json.dumps(cfg, ensure_ascii=False, indent=2)))


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
    flash(request, ("✅ Xray config валиден" if ok else "❌ Ошибка Xray: ") + out[:1000], "ok" if ok else "bad")
    return RedirectResponse("/xray", status_code=303)


@app.post("/xray/restart")
def xray_restart(request: Request):
    require_login(request)
    xray.write_config()
    ok, out = xray.restart_xray()
    flash(request, ("✅ Xray перезапущен" if ok else "❌ Не удалось перезапустить Xray: ") + out[:1000], "ok" if ok else "bad")
    return RedirectResponse("/xray", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_get(request: Request):
    require_login(request)
    return tpl.TemplateResponse("settings.html", ctx(request))


@app.post("/settings")
def settings_post(request: Request, PUBLIC_HOST: str = Form(...), PUBLIC_PORT: str = Form(...), XRAY_CONFIG_PATH: str = Form(...), XRAY_SERVICE_NAME: str = Form(...)):
    require_login(request)
    for k, v in {"PUBLIC_HOST": PUBLIC_HOST, "PUBLIC_PORT": PUBLIC_PORT, "XRAY_CONFIG_PATH": XRAY_CONFIG_PATH, "XRAY_SERVICE_NAME": XRAY_SERVICE_NAME}.items():
        db.set_setting(k, v.strip())
    xray.write_config()
    flash(request, "Настройки сохранены")
    return RedirectResponse("/settings", status_code=303)


@app.get("/sub/{token}", response_class=PlainTextResponse)
def subscription(token: str):
    u = db.row("SELECT * FROM users WHERE sub_token=?", (token,))
    if not u or not u.get("active"):
        raise HTTPException(404)
    exp = db.parse_iso(u.get("expires_at"))
    if exp and exp <= db.utcnow():
        raise HTTPException(403, "subscription expired")
    up = db.row("SELECT * FROM upstreams WHERE id=?", (u.get("upstream_id"),)) if u.get("upstream_id") else None
    label = f"FlyVPN-{up['remark'] if up else 'Gateway'}"
    return xray.vless_link(u, label)


@app.get("/api/sub/{token}")
def subscription_json(token: str):
    u = db.row("SELECT * FROM users WHERE sub_token=?", (token,))
    if not u:
        return JSONResponse({"isFound": False, "links": []}, status_code=404)
    link = xray.vless_link(u)
    return {"isFound": True, "user": {"username": u["username"], "email": u["email"], "expiresAt": u["expires_at"], "isActive": bool(u["active"]), "daysLeft": db.days_left(u)}, "links": [link], "subscriptionUrl": f"/sub/{token}"}
