"""Write paperwork_index.py's classifications back to SharePoint as document
library metadata (DocType / Summary / SuggestedFolder columns).

Auth: Azure AD app registration, client-credentials flow, Graph API
permission Sites.Selected (write access granted to the target document site
only, via a one-time manual grant the user ran through Graph Explorer). Never
touches any other SharePoint site even if the tenant grows.

Usage:
    python scripts/paperwork_sync.py resolve        # read-only: verify auth, list drives
    python scripts/paperwork_sync.py setup-columns  # create metadata columns (idempotent)
    python scripts/paperwork_sync.py map-items      # match index.json records to driveItem IDs
    python scripts/paperwork_sync.py push           # write metadata for matched, classified records
"""
from __future__ import annotations

import argparse
import os
import sys

import json

import httpx

from paperwork_index import INDEX_PATH, TAXONOMY

ROOT = os.environ.get("CONCIERGE_HOME") or os.getcwd()
ENV_PATH = os.path.join(ROOT, ".env")

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TOKEN_TIMEOUT = 30.0
GRAPH_TIMEOUT = 30.0

METADATA_COLUMNS = [
    {"name": "DocType", "text": {"allowMultipleLines": False, "maxLength": 255}},
    {"name": "Summary", "text": {"allowMultipleLines": False, "maxLength": 255}},
    {
        "name": "SuggestedFolder",
        "choice": {
            "allowTextEntry": False,
            "choices": TAXONOMY,
            "displayAs": "dropDownMenu",
        },
    },
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


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} not set in .env", file=sys.stderr)
        sys.exit(1)
    return value


def get_token() -> str:
    tenant_id = require_env("AZURE_TENANT_ID")
    client_id = require_env("AZURE_CLIENT_ID")
    client_secret = require_env("AZURE_CLIENT_SECRET")

    resp = httpx.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=TOKEN_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def resolve() -> None:
    site_id = require_env("PAPERWORK_SP_SITE_ID")
    token = get_token()
    print("auth ok, token acquired")

    resp = httpx.get(
        f"{GRAPH_ROOT}/sites/{site_id}/drives",
        headers={"Authorization": f"Bearer {token}"},
        timeout=GRAPH_TIMEOUT,
    )
    resp.raise_for_status()
    drives = resp.json().get("value", [])

    print(f"{len(drives)} drive(s) on site:")
    for d in drives:
        print(f"  name={d['name']!r} id={d['id']} webUrl={d.get('webUrl')}")


def get_list_id(token: str, site_id: str) -> str:
    resp = httpx.get(
        f"{GRAPH_ROOT}/sites/{site_id}/drive/list",
        headers={"Authorization": f"Bearer {token}"},
        timeout=GRAPH_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def setup_columns() -> None:
    site_id = require_env("PAPERWORK_SP_SITE_ID")
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    list_id = get_list_id(token, site_id)

    resp = httpx.get(
        f"{GRAPH_ROOT}/sites/{site_id}/lists/{list_id}/columns",
        headers=headers,
        timeout=GRAPH_TIMEOUT,
    )
    resp.raise_for_status()
    existing = {c["name"] for c in resp.json().get("value", [])}

    for col in METADATA_COLUMNS:
        if col["name"] in existing:
            print(f"skip (exists): {col['name']}")
            continue
        resp = httpx.post(
            f"{GRAPH_ROOT}/sites/{site_id}/lists/{list_id}/columns",
            headers=headers,
            json=col,
            timeout=GRAPH_TIMEOUT,
        )
        if resp.status_code >= 400:
            print(f"FAILED: {col['name']} -> {resp.status_code} {resp.text}", file=sys.stderr)
            continue
        print(f"created: {col['name']}")


def list_drive_items(token: str, site_id: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_ROOT}/sites/{site_id}/drive/root/children?$top=200"
    by_name: dict[str, str] = {}
    while url:
        resp = httpx.get(url, headers=headers, timeout=GRAPH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("value", []):
            if "file" in item:
                by_name[item["name"]] = item["id"]
        url = data.get("@odata.nextLink")
    return by_name


def load_index() -> list[dict]:
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_index(records: list[dict]) -> None:
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


def map_items() -> None:
    site_id = require_env("PAPERWORK_SP_SITE_ID")
    token = get_token()
    by_name = list_drive_items(token, site_id)
    print(f"{len(by_name)} file(s) on SharePoint drive")

    records = load_index()
    matched = unmatched = 0
    for record in records:
        item_id = by_name.get(record["filename"])
        if item_id:
            record["sp_item_id"] = item_id
            matched += 1
        else:
            unmatched += 1
            print(f"  no SharePoint match: {record['filename']}", file=sys.stderr)
    save_index(records)
    print(f"matched {matched}, unmatched {unmatched}")


def push() -> None:
    site_id = require_env("PAPERWORK_SP_SITE_ID")
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    records = load_index()
    pushed = skipped = 0
    for record in records:
        item_id = record.get("sp_item_id")
        if not item_id or not record.get("has_text"):
            skipped += 1
            continue

        fields = {
            "DocType": (record.get("doc_type") or "")[:255],
            "Summary": (record.get("summary") or "")[:255],
            "SuggestedFolder": record.get("suggested_folder") or "Uncategorized",
        }
        resp = httpx.patch(
            f"{GRAPH_ROOT}/sites/{site_id}/drive/items/{item_id}/listItem/fields",
            headers=headers,
            json=fields,
            timeout=GRAPH_TIMEOUT,
        )
        if resp.status_code >= 400:
            print(f"FAILED: {record['filename']} -> {resp.status_code} {resp.text}", file=sys.stderr)
            continue
        print(f"pushed: {record['filename']}")
        pushed += 1

    print(f"\npushed {pushed}, skipped {skipped} (no sp_item_id or no text)")


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("resolve", help="Verify auth, list document library drives (read-only)")
    sub.add_parser("setup-columns", help="Create DocType/Summary/SuggestedFolder columns on the library (idempotent)")
    sub.add_parser("map-items", help="Match local index records to SharePoint driveItem IDs (read-only, updates index.json)")
    sub.add_parser("push", help="Write DocType/Summary/SuggestedFolder metadata to SharePoint for matched, classified records")
    args = parser.parse_args()

    if args.command == "resolve":
        resolve()
    elif args.command == "setup-columns":
        setup_columns()
    elif args.command == "map-items":
        map_items()
    elif args.command == "push":
        push()


if __name__ == "__main__":
    main()
