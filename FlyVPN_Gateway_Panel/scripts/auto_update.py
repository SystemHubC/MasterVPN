#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)

from dotenv import load_dotenv  # noqa: E402
from app import db, xray  # noqa: E402
from app.upstreams import normalize_import  # noqa: E402

load_dotenv(BASE_DIR / ".env")
db.init_db()


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": "BlackWing-Gateway-AutoUpdater/4"})
    with urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")


def main() -> int:
    updated = 0
    failed = 0
    for up in db.rows("SELECT * FROM upstreams WHERE source_url IS NOT NULL AND source_url != ''"):
        try:
            raw = fetch(up["source_url"])
            data, remark, cnt, protos = normalize_import(raw)
            db.execute(
                """UPDATE upstreams
                   SET json_text=?, remark=?, proxy_count=?, protocol_summary=?, last_update_at=?, last_error=''
                   WHERE id=?""",
                (json.dumps(data, ensure_ascii=False, indent=2), remark, cnt, protos, db.iso(db.utcnow()), up["id"]),
            )
            updated += 1
        except Exception as e:
            db.execute("UPDATE upstreams SET last_error=? WHERE id=?", (str(e)[:500], up["id"]))
            failed += 1
    path = xray.write_config()
    print(f"BlackWing updater: updated={updated}, failed={failed}, config={path}")
    if db.setting("AUTO_RESTART_XRAY", os.getenv("AUTO_RESTART_XRAY", "1")) == "1":
        ok, out = xray.restart_xray()
        print(f"Xray restart: {ok} {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
