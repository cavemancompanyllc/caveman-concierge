#!/usr/bin/env python3
"""One-time interactive OAuth consent for the `google` MCP server
(concierge-google plugin).

Run this by hand from a real terminal, with cwd set to the instance root
(needs a visible browser for the consent screen - it can't run as an
IDE-spawned MCP subprocess):

    cd <instance root>
    python "<plugin install dir>/scripts/google_auth_setup.py"

Reads GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from .env (or from real
process env vars, e.g. if this instance set them via the plugin's
userConfig), walks the user through the OAuth consent screen for Gmail,
Calendar, and Drive, then caches the resulting credentials (including
refresh token) to data/google_token.json. Re-run any time scopes change or
the cached token is revoked/deleted.

data/google_token.json is equivalent to a password - it grants full
mailbox/calendar/Drive access. It's gitignored; never commit it.

This script ships inside the plugin, not the instance's own repo, so .env
and data/google_token.json can't be found relative to `__file__` - the
instance root resolves via the `CONCIERGE_HOME` env var if set, falling
back to cwd (hence "run with cwd set to the instance root" above).
"""
from __future__ import annotations

import os

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = os.environ.get("CONCIERGE_HOME") or os.getcwd()
ENV_PATH = os.path.join(ROOT, ".env")
TOKEN_PATH = os.path.join(ROOT, "data", "google_token.json")

SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/tasks",
]


def load_env() -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_env()
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise SystemExit(
            "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env.\n"
            "Create an OAuth Desktop app client in Google Cloud Console "
            "(APIs & Services > Credentials) and set both in .env first."
        )

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"Saved credentials to {TOKEN_PATH}")
    print("The google MCP server will pick this up automatically.")


if __name__ == "__main__":
    main()
