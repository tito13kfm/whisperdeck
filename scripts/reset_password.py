#!/usr/bin/env python
"""Admin CLI: reset a user's password directly against the database.

Bypasses the token flow — requires filesystem access to the database.
Usage:
    python scripts/reset_password.py --username <name> --new-password <pass>
    python scripts/reset_password.py --username <name> --new-password <pass> --data-dir /path/to/data

Environment variables:
    WHISPERDECK_DATA_DIR   Override the data directory (default: ./data)
"""

import argparse
import os
import sys
from pathlib import Path

# Add repo root to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import init_db, User
from services.auth import hash_password, generate_salt


def main():
    parser = argparse.ArgumentParser(description="Reset a WhisperDeck user password")
    parser.add_argument("--username", required=True, help="Username to reset")
    parser.add_argument("--new-password", required=True, help="New password")
    parser.add_argument("--data-dir", default=None, help="Data directory (default: $WHISPERDECK_DATA_DIR or ./data)")
    args = parser.parse_args()

    data_dir = args.data_dir or os.environ.get("WHISPERDECK_DATA_DIR") or "data"
    db_path = os.path.join(data_dir, "whisperdesk.db")

    if not os.path.exists(db_path):
        print(f"[ERROR] Database not found: {db_path}")
        sys.exit(1)

    engine, SessionLocal, _ = init_db(db_path)
    db = SessionLocal()

    try:
        user = db.query(User).filter(User.username == args.username).first()
        if not user:
            print(f"[ERROR] User '{args.username}' not found")
            sys.exit(1)

        salt = generate_salt()
        user.password_salt = salt
        user.password_hash = hash_password(args.new_password, salt)
        user.reset_token = None
        user.reset_token_expires_at = None
        db.commit()
        print(f"[OK] Password reset for '{args.username}'")
    finally:
        db.close()


if __name__ == "__main__":
    main()