from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "storage" / "flyvpn_gateway.sqlite3"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def new_token() -> str:
    # ~43 chars, not guessable by brute force.
    return secrets.token_urlsafe(32)


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def rows(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, tuple(params)).fetchall()]


def row(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with conn() as c:
        cur = c.execute(sql, tuple(params))
        return int(cur.lastrowid or 0)


def _columns(c: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db() -> None:
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS upstreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                remark TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                json_text TEXT NOT NULL,
                proxy_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                username TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL UNIQUE,
                uuid TEXT NOT NULL UNIQUE,
                sub_token TEXT NOT NULL UNIQUE,
                upstream_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                traffic_limit_gb INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(upstream_id) REFERENCES upstreams(id)
            );
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                days INTEGER NOT NULL,
                price_rub INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_rub INTEGER NOT NULL DEFAULT 0,
                method TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

        # Soft migrations for already installed versions.
        user_cols = _columns(c, "users")
        if "traffic_limit_gb" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN traffic_limit_gb INTEGER NOT NULL DEFAULT 0")
        if "notes" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN notes TEXT NOT NULL DEFAULT ''")

        count = c.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"]
        if count == 0:
            for name, days, price in [
                ("1 месяц", 30, 199),
                ("3 месяца", 90, 499),
                ("6 месяцев", 180, 899),
                ("1 год", 365, 1599),
            ]:
                c.execute(
                    "INSERT INTO plans(name, days, price_rub, enabled) VALUES(?,?,?,1)",
                    (name, days, price),
                )

        # Upgrade short old tokens to long random subscription secrets.
        for r in c.execute("SELECT id, sub_token FROM users").fetchall():
            tok = str(r["sub_token"] or "")
            if len(tok) < 32:
                c.execute("UPDATE users SET sub_token=? WHERE id=?", (new_token(), r["id"]))


def setting(key: str, default: str = "") -> str:
    r = row("SELECT value FROM settings WHERE key=?", (key,))
    return str(r["value"]) if r else default


def set_setting(key: str, value: str) -> None:
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def make_user(username: str, upstream_id: int | None, days: int = 3, tg_id: int | None = None, notes: str = "") -> int:
    base_uuid = str(uuid.uuid4())
    email = f"flyvpn_{secrets.token_hex(5)}"
    token = new_token()
    expires = iso(utcnow() + timedelta(days=days)) if days > 0 else None
    return execute(
        """INSERT INTO users(tg_id, username, email, uuid, sub_token, upstream_id, active, expires_at, notes, created_at)
             VALUES(?,?,?,?,?,?,1,?,?,?)""",
        (tg_id, username, email, base_uuid, token, upstream_id, expires, notes, iso(utcnow())),
    )


def rotate_sub_token(uid: int) -> str:
    token = new_token()
    execute("UPDATE users SET sub_token=? WHERE id=?", (token, uid))
    return token


def active_users() -> list[dict[str, Any]]:
    now = iso(utcnow())
    return rows(
        """SELECT * FROM users
             WHERE active=1 AND (expires_at IS NULL OR expires_at > ?)
             ORDER BY id DESC""",
        (now,),
    )


def days_left(u: dict[str, Any]) -> int:
    exp = parse_iso(u.get("expires_at"))
    if not exp:
        return 99999
    delta = exp - utcnow()
    return max(0, int(delta.total_seconds() // 86400))


def seed_upstreams_from_dir(path: Path) -> int:
    from app.upstreams import count_proxy_outbounds, remark_from_config
    added = 0
    for fp in sorted(path.glob("*.json")):
        raw = fp.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except Exception:
            continue
        name = fp.stem.replace("_", " ").title()
        remark = remark_from_config(data) or name
        exists = row("SELECT id FROM upstreams WHERE name=?", (name,))
        if exists:
            continue
        execute(
            "INSERT INTO upstreams(name, remark, enabled, json_text, proxy_count, created_at) VALUES(?,?,?,?,?,?)",
            (name, remark, 1, json.dumps(data, ensure_ascii=False, indent=2), count_proxy_outbounds(data), iso(utcnow())),
        )
        added += 1
    return added
