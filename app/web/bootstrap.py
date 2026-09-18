"""One-shot bootstrap for the first HQ web login (ticket-037).

No default credentials exist. Run once on the host; links a web login to
the SUPERADMIN Atlas identity (same subject as Telegram):

    venv\\Scripts\\python -m app.web.bootstrap --username hq --chat-id 12345
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.config.loader import ConfigLoader
    from app.database.manager import DatabaseManager
    from app.web import auth as webauth
    from app.web.db import ensure_web_tables

    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--chat-id", required=True)
    args = ap.parse_args()

    config = ConfigLoader.load()
    if str(args.chat_id) != str(config.super_admin_chat_id):
        print("REFUSED: chat-id must equal SUPERADMIN_CHAT_ID (identity bridge).")
        return 2
    db = DatabaseManager(str(config.database_path))
    ensure_web_tables(db)
    pw = getpass.getpass("Password (10+ chars): ")
    try:
        user = webauth.create_web_user(db, args.username, pw, args.chat_id,
                                       created_by="bootstrap")
    except ValueError as e:
        print(f"REFUSED: {e}")
        return 2
    print(f"Linked web login '{user['username']}' -> Atlas {user['chat_id']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
