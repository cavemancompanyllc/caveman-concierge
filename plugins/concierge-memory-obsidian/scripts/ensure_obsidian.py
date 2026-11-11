#!/usr/bin/env python3
"""Make sure this workspace's Obsidian Local REST API is reachable, opening the
vault in Obsidian if it isn't.

Meant as the prep step before an unattended run that writes to the vault, e.g. in
workspace.yaml:

    headless:
      before: python -m caveman_core run concierge-memory-obsidian ensure_obsidian.py

Reads OBSIDIAN_HOST / OBSIDIAN_PORT from the workspace .env (defaults 127.0.0.1 /
27124) and the vault name from workspace.yaml plugins.concierge-memory-obsidian.vault_name
(else the folder name of vault_path). Opening uses the obsidian:// URI handler,
which starts the app if needed and opens that vault's window, loading its REST API
plugin. Exit 0 = API up; 1 = still unreachable after --timeout seconds.
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from caveman_core import env as cc_env
from caveman_core import paths


def api_up(url: str) -> bool:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # the plugin serves a self-signed certificate
    try:
        with urllib.request.urlopen(url, timeout=3, context=ctx) as r:
            return b'"OK"' in r.read().replace(b" ", b"")
    except OSError:
        return False


def open_vault(name: str) -> None:
    uri = "obsidian://open?vault=" + urllib.parse.quote(name)
    if sys.platform == "win32":
        os.startfile(uri)  # noqa: S606 - URI handler, no shell
    elif sys.platform == "darwin":
        os.spawnlp(os.P_NOWAIT, "open", "open", uri)
    else:
        os.spawnlp(os.P_NOWAIT, "xdg-open", "xdg-open", uri)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=int, default=60)
    a = ap.parse_args()

    ws = paths.resolve()
    cc_env.load(ws.root)
    host = os.environ.get("OBSIDIAN_HOST") or "127.0.0.1"
    port = os.environ.get("OBSIDIAN_PORT") or "27124"
    url = f"https://{host}:{port}/"
    if api_up(url):
        print("obsidian: API already up")
        return 0

    name = ws.setting("concierge-memory-obsidian", "vault_name")
    if not name:
        vault_path = ws.setting("concierge-memory-obsidian", "vault_path")
        name = Path(vault_path).name if vault_path else None
    if not name:
        print("obsidian: API down and no vault_name/vault_path in workspace.yaml - can't open it")
        return 1
    print(f"obsidian: API down, opening vault '{name}'")
    open_vault(name)
    deadline = time.monotonic() + a.timeout
    while time.monotonic() < deadline:
        time.sleep(2)
        if api_up(url):
            print("obsidian: API up")
            return 0
    print(f"obsidian: API still unreachable after {a.timeout}s (is the Local REST API plugin enabled?)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
