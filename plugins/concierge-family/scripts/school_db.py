#!/usr/bin/env python3
"""
School tracking state store for the `family` subagent and the scheduled
school mail scan.

This is state only — it never talks to Gmail/Calendar itself (Claude does
that through the `google` MCP server). It answers the two questions an
unattended scan needs answered reliably:

  1. "Have I already processed this Gmail message?"      -> messages table
  2. "Did I already create a calendar event / task for
     this school event, and is this new mail a reschedule
     of it?"                                              -> items table

Plus a teacher roster per kid per school year, and a small key/value table
for scan bookkeeping (last scan time).

No third-party dependencies; stdlib sqlite3 only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("CONCIERGE_HOME") or Path.cwd()) / "data" / "school.db"

NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

KIDS = ("rex", "rose", "school", "district")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS messages (
    gmail_id TEXT PRIMARY KEY,
    received TEXT,
    sender TEXT,
    subject TEXT,
    kid TEXT NOT NULL CHECK (kid IN ('rex','rose','school','district','ignored')),
    category TEXT,
    summary TEXT,
    processed_at TEXT NOT NULL DEFAULT ({NOW})
);
CREATE INDEX IF NOT EXISTS IX_messages_received ON messages(received);

-- One row per real-world school thing we put on the user's calendar or task
-- list. kind='event' carries a gcal_id, kind='task' carries a task_id.
-- source_ids is a JSON list of every Gmail message that mentioned it;
-- history is a JSON list of prior dates when a reschedule was applied.
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kid TEXT NOT NULL CHECK (kid IN ('rex','rose','school','district')),
    kind TEXT NOT NULL CHECK (kind IN ('event','task')),
    title TEXT NOT NULL,
    norm_title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT,
    gcal_id TEXT,
    task_id TEXT,
    source_ids TEXT NOT NULL DEFAULT '[]',
    history TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
    created_at TEXT NOT NULL DEFAULT ({NOW}),
    updated_at TEXT NOT NULL DEFAULT ({NOW})
);
CREATE INDEX IF NOT EXISTS IX_items_start ON items(start);

CREATE TABLE IF NOT EXISTS roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kid TEXT NOT NULL CHECK (kid IN ('rex','rose')),
    school_year TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    role TEXT,
    UNIQUE (kid, school_year, name)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT ({NOW})
);
"""

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "at", "in", "on", "is",
    "rex", "rose", "school", "cowan", "grade", "3rd", "5th", "class",
    "new", "date", "update", "updated", "reminder", "rescheduled", "moved",
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def normalize(title: str) -> str:
    title = re.sub(r"^\[[^\]]*\]\s*", "", title.lower())
    words = re.findall(r"[a-z0-9]+", title)
    return " ".join(w for w in words if w not in STOPWORDS)


def similarity(a: str, b: str) -> float:
    # Overlap coefficient, not Jaccard: a follow-up mail usually restates the
    # event name plus extra words ("Field Day - moved to Friday"), which
    # Jaccard would score low.
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def date_part(value: str) -> date:
    return date.fromisoformat(value[:10])


def emit(rows) -> None:
    print(json.dumps([dict(r) for r in rows], indent=1))


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------

def cmd_seen(args) -> None:
    """Print only the Gmail IDs NOT yet processed, one per line.

    --routed-only treats messages the scan looked at but ignored as unseen,
    so the morning rundown can drop school mail without also dropping
    things like a parent's own district work mail that the scan deliberately skipped.
    """
    conn = connect()
    sql = f"SELECT gmail_id FROM messages WHERE gmail_id IN ({','.join('?' * len(args.ids))})"
    if args.routed_only:
        sql += " AND kid != 'ignored'"
    known = {r["gmail_id"] for r in conn.execute(sql, args.ids)}
    for gid in args.ids:
        if gid not in known:
            print(gid)


def cmd_msg_add(args) -> None:
    conn = connect()
    conn.execute(
        """INSERT INTO messages (gmail_id, received, sender, subject, kid, category, summary)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(gmail_id) DO UPDATE SET kid=excluded.kid, category=excluded.category,
               summary=excluded.summary, processed_at=""" + NOW,
        (args.id, args.received, args.sender, args.subject, args.kid, args.category, args.summary),
    )
    conn.commit()
    print(f"recorded {args.id} ({args.kid})")


def cmd_msg_list(args) -> None:
    conn = connect()
    sql = "SELECT * FROM messages WHERE kid != 'ignored'"
    params: list = []
    if args.since:
        sql += " AND received >= ?"
        params.append(args.since)
    if args.kid:
        sql += " AND kid = ?"
        params.append(args.kid)
    sql += " ORDER BY received DESC LIMIT ?"
    params.append(args.limit)
    emit(conn.execute(sql, params))


# ---------------------------------------------------------------------------
# items (calendar events + tasks)
# ---------------------------------------------------------------------------

def cmd_item_find(args) -> None:
    """Candidate matches for a possibly-already-tracked event, best first.

    Match = same kid (or school-wide), active, start within --window days of
    the proposed date, and title token-overlap >= --min-score. The caller
    decides whether a candidate is really the same event; this only narrows.
    """
    conn = connect()
    target = normalize(args.title)
    proposed = date_part(args.date)
    lo = (proposed - timedelta(days=args.window)).isoformat()
    hi = (proposed + timedelta(days=args.window)).isoformat() + "~"
    kids = {args.kid, "school"} if args.kid in ("rex", "rose") else {args.kid}
    rows = conn.execute(
        f"""SELECT * FROM items WHERE status='active' AND start BETWEEN ? AND ?
            AND kid IN ({','.join('?' * len(kids))})""",
        [lo, hi, *kids],
    ).fetchall()
    scored = []
    for r in rows:
        score = similarity(target, r["norm_title"])
        if score >= args.min_score:
            d = dict(r)
            d["score"] = round(score, 2)
            d["same_date"] = r["start"][:10] == args.date[:10]
            scored.append(d)
    scored.sort(key=lambda d: d["score"], reverse=True)
    print(json.dumps(scored, indent=1))


def cmd_item_add(args) -> None:
    if args.kind == "event" and not args.gcal_id:
        sys.exit("kind=event requires --gcal-id")
    if args.kind == "task" and not args.task_id:
        sys.exit("kind=task requires --task-id")
    conn = connect()
    cur = conn.execute(
        """INSERT INTO items (kid, kind, title, norm_title, start, end, gcal_id, task_id, source_ids)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (args.kid, args.kind, args.title, normalize(args.title), args.start, args.end,
         args.gcal_id, args.task_id, json.dumps([args.source] if args.source else [])),
    )
    conn.commit()
    print(f"item #{cur.lastrowid} added")


def cmd_item_update(args) -> None:
    conn = connect()
    row = conn.execute("SELECT * FROM items WHERE id=?", (args.id,)).fetchone()
    if not row:
        sys.exit(f"no item #{args.id}")
    sources = json.loads(row["source_ids"])
    if args.source and args.source not in sources:
        sources.append(args.source)
    history = json.loads(row["history"])
    start, end = row["start"], row["end"]
    if args.start and args.start != row["start"]:
        history.append({"start": row["start"], "end": row["end"],
                        "changed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "source": args.source})
        start, end = args.start, args.end or None
    title = args.title or row["title"]
    status = args.status or row["status"]
    conn.execute(
        f"""UPDATE items SET title=?, norm_title=?, start=?, end=?, source_ids=?, history=?,
               status=?, updated_at={NOW} WHERE id=?""",
        (title, normalize(title), start, end, json.dumps(sources), json.dumps(history),
         status, args.id),
    )
    conn.commit()
    print(f"item #{args.id} updated" + (" (rescheduled)" if start != row["start"] else ""))


def cmd_item_upcoming(args) -> None:
    conn = connect()
    today = (args.from_date or date.today().isoformat())
    until = (date_part(today) + timedelta(days=args.days)).isoformat() + "~"
    sql = "SELECT * FROM items WHERE status='active' AND start BETWEEN ? AND ?"
    params: list = [today, until]
    if args.kid:
        sql += " AND kid IN (?, 'school')"
        params.append(args.kid)
    sql += " ORDER BY start"
    emit(conn.execute(sql, params))


def cmd_item_changed(args) -> None:
    """Items created or rescheduled since a timestamp — feeds the daily brief."""
    conn = connect()
    emit(conn.execute(
        "SELECT * FROM items WHERE updated_at >= ? ORDER BY start", (args.since,)
    ))


# ---------------------------------------------------------------------------
# roster + kv
# ---------------------------------------------------------------------------

def cmd_roster_set(args) -> None:
    conn = connect()
    conn.execute(
        """INSERT INTO roster (kid, school_year, name, email, role) VALUES (?,?,?,?,?)
           ON CONFLICT(kid, school_year, name) DO UPDATE SET
               email=COALESCE(excluded.email, roster.email),
               role=COALESCE(excluded.role, roster.role)""",
        (args.kid, args.year, args.name, args.email, args.role),
    )
    conn.commit()
    print(f"roster: {args.kid} {args.year} {args.name}")


def cmd_roster_list(args) -> None:
    conn = connect()
    sql = "SELECT * FROM roster WHERE 1=1"
    params: list = []
    if args.year:
        sql += " AND school_year=?"
        params.append(args.year)
    if args.kid:
        sql += " AND kid=?"
        params.append(args.kid)
    emit(conn.execute(sql + " ORDER BY school_year DESC, kid, name", params))


def cmd_kv(args) -> None:
    conn = connect()
    if args.value is None:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (args.key,)).fetchone()
        print(row["value"] if row else "")
    else:
        conn.execute(
            f"INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at={NOW}",
            (args.key, args.value),
        )
        conn.commit()
        print(f"{args.key} = {args.value}")


def main() -> None:
    p = argparse.ArgumentParser(description="School tracking state store")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seen", help="Filter Gmail IDs down to the unprocessed ones")
    s.add_argument("ids", nargs="+")
    s.add_argument("--routed-only", action="store_true")
    s.set_defaults(fn=cmd_seen)

    msg = sub.add_parser("msg").add_subparsers(dest="sub", required=True)
    s = msg.add_parser("add")
    s.add_argument("--id", required=True)
    s.add_argument("--received", required=True, help="ISO date/datetime")
    s.add_argument("--sender", required=True)
    s.add_argument("--subject", default="")
    s.add_argument("--kid", required=True, choices=KIDS + ("ignored",))
    s.add_argument("--category", default="")
    s.add_argument("--summary", default="")
    s.set_defaults(fn=cmd_msg_add)
    s = msg.add_parser("list")
    s.add_argument("--since")
    s.add_argument("--kid", choices=KIDS)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_msg_list)

    item = sub.add_parser("item").add_subparsers(dest="sub", required=True)
    s = item.add_parser("find")
    s.add_argument("--kid", required=True, choices=KIDS)
    s.add_argument("--title", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--window", type=int, default=60)
    s.add_argument("--min-score", type=float, default=0.6)
    s.set_defaults(fn=cmd_item_find)
    s = item.add_parser("add")
    s.add_argument("--kid", required=True, choices=KIDS)
    s.add_argument("--kind", required=True, choices=("event", "task"))
    s.add_argument("--title", required=True)
    s.add_argument("--start", required=True)
    s.add_argument("--end")
    s.add_argument("--gcal-id")
    s.add_argument("--task-id")
    s.add_argument("--source", help="Gmail message ID that prompted it")
    s.set_defaults(fn=cmd_item_add)
    s = item.add_parser("update")
    s.add_argument("id", type=int)
    s.add_argument("--title")
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--source")
    s.add_argument("--status", choices=("active", "cancelled"))
    s.set_defaults(fn=cmd_item_update)
    s = item.add_parser("upcoming")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--kid", choices=("rex", "rose"))
    s.add_argument("--from-date")
    s.set_defaults(fn=cmd_item_upcoming)
    s = item.add_parser("changed")
    s.add_argument("--since", required=True, help="ISO UTC timestamp, e.g. 2026-09-12T10:45:00Z")
    s.set_defaults(fn=cmd_item_changed)

    roster = sub.add_parser("roster").add_subparsers(dest="sub", required=True)
    s = roster.add_parser("set")
    s.add_argument("--kid", required=True, choices=("rex", "rose"))
    s.add_argument("--year", required=True, help="e.g. 2026-27")
    s.add_argument("--name", required=True)
    s.add_argument("--email")
    s.add_argument("--role")
    s.set_defaults(fn=cmd_roster_set)
    s = roster.add_parser("list")
    s.add_argument("--year")
    s.add_argument("--kid", choices=("rex", "rose"))
    s.set_defaults(fn=cmd_roster_list)

    s = sub.add_parser("kv", help="Get (no value) or set a bookkeeping key")
    s.add_argument("key")
    s.add_argument("value", nargs="?")
    s.set_defaults(fn=cmd_kv)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
