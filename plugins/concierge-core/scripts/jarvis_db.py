#!/usr/bin/env python3
"""
Work-tracking DB: Project -> Effort -> (Session | Task).

Project  = Epic-equivalent    (a long-running initiative)
Effort   = Feature-equivalent (a scoped chunk of work within a project)
Session  = one Claude Code conversation working an effort (append-only log)
Task     = a next-action within an effort (GTD sense) — can outlive one
           session, so it hangs off the effort, not the session.

`efforts.current_state` / `efforts.next_steps` are living fields, overwritten
on every `session end` — that's the fast path for "pick up where we left off".
`sessions` rows are the append-only history, kept for deeper digging.

No third-party dependencies; stdlib sqlite3 only.

This script ships inside the `concierge-core` plugin, so it does NOT live in
the instance's own repo — it can't locate the DB relative to its own file
path (that would resolve into the plugin's install directory, not the
instance). Instead the DB path resolves, in priority order:

1. `CONCIERGE_DB_PATH` env var — full path to the DB file, for instances
   that want it somewhere other than the default.
2. `CONCIERGE_DATA_DIR` env var — a data directory; DB file is
   `<dir>/jarvis.db`.
3. `<cwd>/data/jarvis.db` — the default. Works unmodified because Claude
   Code's Bash tool runs with cwd set to the instance directory (the
   project root), not the plugin's install directory.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _resolve_db_path() -> Path:
    if env_path := os.environ.get("CONCIERGE_DB_PATH"):
        return Path(env_path)
    data_dir = Path(os.environ.get("CONCIERGE_DATA_DIR", Path.cwd() / "data"))
    return data_dir / "jarvis.db"


DB_PATH = _resolve_db_path()

# ISO 8601 UTC with an explicit Z, e.g. 2026-07-03T14:52:03Z — unlike sqlite's
# bare datetime('now'), this can't be mistaken for local time later.
NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','active','paused','done','archived')),
    created_at TEXT NOT NULL DEFAULT ({NOW}),
    updated_at TEXT NOT NULL DEFAULT ({NOW})
);

CREATE TABLE IF NOT EXISTS efforts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','active','blocked','done','abandoned')),
    current_state TEXT,
    next_steps TEXT,
    created_at TEXT NOT NULL DEFAULT ({NOW}),
    updated_at TEXT NOT NULL DEFAULT ({NOW})
);
CREATE INDEX IF NOT EXISTS IX_efforts_project_id ON efforts(project_id);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    effort_id INTEGER REFERENCES efforts(id),
    decision TEXT NOT NULL,
    rationale TEXT,
    decided_at TEXT NOT NULL DEFAULT ({NOW})
);
CREATE INDEX IF NOT EXISTS IX_decisions_project_id ON decisions(project_id);
CREATE INDEX IF NOT EXISTS IX_decisions_effort_id ON decisions(effort_id);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effort_id INTEGER NOT NULL REFERENCES efforts(id),
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress','completed','abandoned')),
    started_at TEXT NOT NULL DEFAULT ({NOW}),
    ended_at TEXT,
    summary TEXT,
    decisions TEXT,
    blockers TEXT,
    git_refs TEXT
);
CREATE INDEX IF NOT EXISTS IX_sessions_effort_id ON sessions(effort_id);

-- Tasks nest under the effort, not the session: a task is a next-action
-- (GTD sense) that often outlives one sitting, e.g. opened in session 1,
-- closed in session 4. Scoping to session would hide open work from
-- cross-session views. created/completed session links are provenance only.
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effort_id INTEGER NOT NULL REFERENCES efforts(id),
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','done','cancelled')),
    created_session_id INTEGER REFERENCES sessions(id),
    completed_session_id INTEGER REFERENCES sessions(id),
    created_at TEXT NOT NULL DEFAULT ({NOW}),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS IX_tasks_effort_id ON tasks(effort_id);

-- updated_at is maintained by triggers, not application code, so it can
-- never be forgotten in a future UPDATE statement.
CREATE TRIGGER IF NOT EXISTS trg_projects_updated_at
AFTER UPDATE OF slug, name, description, status ON projects
BEGIN
    UPDATE projects SET updated_at = {NOW} WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_efforts_updated_at
AFTER UPDATE OF slug, name, description, status, current_state, next_steps, project_id ON efforts
BEGIN
    UPDATE efforts SET updated_at = {NOW} WHERE id = NEW.id;
END;
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def find_project(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if not row:
        die(f"no project with slug '{slug}'")
    return row


def find_effort(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM efforts WHERE slug = ?", (slug,)).fetchone()
    if not row:
        die(f"no effort with slug '{slug}'")
    return row


GENERIC_PROJECT_SLUG = "general"


def resolve_generic_effort_slug(conn: sqlite3.Connection) -> str:
    """
    `session start general` / `session end general` route here instead of a
    real effort slug. Ensures a 'general' project and a rolling monthly
    effort under it both exist, creating them lazily on first use, and
    returns the current month's effort slug (e.g. 'general-2026-07').

    Monthly, not a single evergreen effort: current_state/next_steps assume
    one coherent thread of work, which unrelated one-offs aren't. Monthly
    keeps them grouped into skimmable chunks without weekly-rotation overhead.
    """
    now = datetime.now(timezone.utc)
    effort_slug = f"{GENERIC_PROJECT_SLUG}-{now:%Y-%m}"

    project = conn.execute(
        "SELECT * FROM projects WHERE slug = ?", (GENERIC_PROJECT_SLUG,)
    ).fetchone()
    if not project:
        conn.execute(
            "INSERT INTO projects (slug, name, description, status) VALUES (?, ?, ?, 'active')",
            (
                GENERIC_PROJECT_SLUG,
                "General",
                "Day-to-day and one-off requests that don't belong to a specific project.",
            ),
        )
        project = conn.execute(
            "SELECT * FROM projects WHERE slug = ?", (GENERIC_PROJECT_SLUG,)
        ).fetchone()

    effort = conn.execute("SELECT * FROM efforts WHERE slug = ?", (effort_slug,)).fetchone()
    if not effort:
        conn.execute(
            "INSERT INTO efforts (project_id, slug, name, description, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (
                project["id"],
                effort_slug,
                f"General - {now:%B %Y}",
                "Miscellaneous one-off requests this month.",
            ),
        )
    conn.commit()
    return effort_slug


# ---------------------------------------------------------------- project --

def cmd_project_add(conn, args):
    conn.execute(
        "INSERT INTO projects (slug, name, description) VALUES (?, ?, ?)",
        (args.slug, args.name, args.description),
    )
    conn.commit()
    print(f"created project '{args.slug}'")


def cmd_project_list(conn, args):
    rows = conn.execute(
        "SELECT * FROM projects ORDER BY status = 'archived', updated_at DESC"
    ).fetchall()
    if not rows:
        print("(no projects yet)")
        return
    for p in rows:
        n_efforts = conn.execute(
            "SELECT COUNT(*) FROM efforts WHERE project_id = ?", (p["id"],)
        ).fetchone()[0]
        print(f"[{p['status']:8}] {p['slug']:24} {p['name']}  ({n_efforts} effort(s))")


def cmd_project_show(conn, args):
    p = find_project(conn, args.slug)
    print(f"# {p['name']}  ({p['slug']})")
    print(f"status: {p['status']}")
    if p["description"]:
        print(f"\n{p['description']}")
    efforts = conn.execute(
        "SELECT * FROM efforts WHERE project_id = ? ORDER BY updated_at DESC",
        (p["id"],),
    ).fetchall()
    print(f"\n## Efforts ({len(efforts)})")
    for e in efforts:
        print(f"  [{e['status']:8}] {e['slug']:24} {e['name']}")


def cmd_project_update(conn, args):
    p = find_project(conn, args.slug)
    fields, values = [], []
    for col, val in (
        ("status", args.status),
        ("description", args.description),
    ):
        if val is not None:
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        die("nothing to update - pass at least one of --status/--description")
    values.append(p["id"])
    conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    print(f"updated project '{args.slug}'")


# ----------------------------------------------------------------- effort --

def cmd_effort_add(conn, args):
    p = find_project(conn, args.project)
    conn.execute(
        "INSERT INTO efforts (project_id, slug, name, description) VALUES (?, ?, ?, ?)",
        (p["id"], args.slug, args.name, args.description),
    )
    conn.commit()
    print(f"created effort '{args.slug}' under project '{p['slug']}'")


def cmd_effort_list(conn, args):
    if args.project:
        p = find_project(conn, args.project)
        rows = conn.execute(
            "SELECT * FROM efforts WHERE project_id = ? ORDER BY updated_at DESC",
            (p["id"],),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM efforts ORDER BY updated_at DESC").fetchall()
    if not rows:
        print("(no efforts yet)")
        return
    for e in rows:
        print(f"[{e['status']:9}] {e['slug']:24} {e['name']}")


def cmd_effort_show(conn, args):
    _print_effort_context(conn, find_effort(conn, args.slug))


def cmd_effort_update(conn, args):
    e = find_effort(conn, args.slug)
    fields, values = [], []
    for col, val in (
        ("status", args.status),
        ("current_state", args.current_state),
        ("next_steps", args.next_steps),
        ("description", args.description),
    ):
        if val is not None:
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        die("nothing to update - pass at least one of --status/--current-state/--next-steps/--description")
    values.append(e["id"])
    conn.execute(f"UPDATE efforts SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    print(f"updated effort '{args.slug}'")


def _print_effort_context(conn, e: sqlite3.Row):
    p = conn.execute("SELECT * FROM projects WHERE id = ?", (e["project_id"],)).fetchone()
    print(f"# {e['name']}  ({e['slug']})")
    print(f"project: {p['name']} ({p['slug']})")
    print(f"status: {e['status']}")
    if e["description"]:
        print(f"\n{e['description']}")
    print(f"\n## Current state\n{e['current_state'] or '(none recorded yet)'}")
    print(f"\n## Next steps\n{e['next_steps'] or '(none recorded yet)'}")
    last = conn.execute(
        "SELECT * FROM sessions WHERE effort_id = ? AND status = 'completed' "
        "ORDER BY ended_at DESC LIMIT 1",
        (e["id"],),
    ).fetchone()
    if last:
        print(f"\n## Last session ({last['ended_at']})\n{last['summary'] or '(no summary recorded)'}")
    n_sessions = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE effort_id = ?", (e["id"],)
    ).fetchone()[0]
    print(f"\n({n_sessions} session(s) total - use `session list --effort {e['slug']}` for full history)")
    open_tasks = conn.execute(
        "SELECT id, description FROM tasks WHERE effort_id = ? AND status = 'open' ORDER BY created_at",
        (e["id"],),
    ).fetchall()
    if open_tasks:
        print(f"\n## Open tasks ({len(open_tasks)})")
        for t in open_tasks:
            print(f"  #{t['id']:<4} {t['description']}")


# --------------------------------------------------------------- decision --

def cmd_decision_add(conn, args):
    p = find_project(conn, args.project)
    effort_id = None
    if args.effort:
        effort_id = find_effort(conn, args.effort)["id"]
    cur = conn.execute(
        "INSERT INTO decisions (project_id, effort_id, decision, rationale) VALUES (?, ?, ?, ?)",
        (p["id"], effort_id, args.decision, args.rationale),
    )
    conn.commit()
    print(f"logged decision #{cur.lastrowid} for project '{p['slug']}'")


def cmd_decision_list(conn, args):
    query = (
        "SELECT d.*, p.slug AS project_slug, e.slug AS effort_slug FROM decisions d "
        "JOIN projects p ON p.id = d.project_id "
        "LEFT JOIN efforts e ON e.id = d.effort_id WHERE 1=1"
    )
    params: list = []
    if args.project:
        query += " AND p.slug = ?"
        params.append(args.project)
    if args.effort:
        query += " AND e.slug = ?"
        params.append(args.effort)
    query += " ORDER BY d.decided_at DESC"
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("(no decisions logged yet)")
        return
    for d in rows:
        scope = f"{d['project_slug']}/{d['effort_slug']}" if d["effort_slug"] else d["project_slug"]
        print(f"#{d['id']:<4} [{d['decided_at']}] ({scope}) {d['decision']}")
        if d["rationale"]:
            print(f"       why: {d['rationale']}")


def cmd_decision_show(conn, args):
    row = conn.execute(
        "SELECT d.*, p.slug AS project_slug, e.slug AS effort_slug FROM decisions d "
        "JOIN projects p ON p.id = d.project_id "
        "LEFT JOIN efforts e ON e.id = d.effort_id WHERE d.id = ?",
        (args.id,),
    ).fetchone()
    if not row:
        die(f"no decision #{args.id}")
    scope = f"{row['project_slug']}/{row['effort_slug']}" if row["effort_slug"] else row["project_slug"]
    print(f"# Decision #{row['id']}  ({scope})")
    print(f"decided_at: {row['decided_at']}")
    print(f"\n{row['decision']}")
    if row["rationale"]:
        print(f"\nWhy: {row['rationale']}")


# ---------------------------------------------------------------- session --

def cmd_session_start(conn, args):
    if args.effort == GENERIC_PROJECT_SLUG:
        args.effort = resolve_generic_effort_slug(conn)
    e = find_effort(conn, args.effort)

    stale = conn.execute(
        "SELECT id FROM sessions WHERE effort_id = ? AND status = 'in_progress'",
        (e["id"],),
    ).fetchall()
    for s in stale:
        conn.execute(
            f"UPDATE sessions SET status = 'abandoned', ended_at = {NOW} WHERE id = ?",
            (s["id"],),
        )
    if stale:
        print(f"(marked {len(stale)} stale in-progress session(s) as abandoned)\n")

    if e["status"] == "planned":
        conn.execute("UPDATE efforts SET status = 'active' WHERE id = ?", (e["id"],))

    cur = conn.execute("INSERT INTO sessions (effort_id) VALUES (?)", (e["id"],))
    conn.commit()
    print(f"started session #{cur.lastrowid} for effort '{e['slug']}'\n")
    _print_effort_context(conn, find_effort(conn, args.effort))


def cmd_session_end(conn, args):
    if args.effort == GENERIC_PROJECT_SLUG:
        args.effort = resolve_generic_effort_slug(conn)
    e = find_effort(conn, args.effort)
    s = conn.execute(
        "SELECT * FROM sessions WHERE effort_id = ? AND status = 'in_progress' "
        "ORDER BY started_at DESC LIMIT 1",
        (e["id"],),
    ).fetchone()
    if not s:
        die(f"no in-progress session for effort '{e['slug']}' - did you run `session start` first?")

    conn.execute(
        f"UPDATE sessions SET status = 'completed', ended_at = {NOW}, "
        "summary = ?, decisions = ?, blockers = ?, git_refs = ? WHERE id = ?",
        (args.summary, args.decisions, args.blockers, args.git_refs, s["id"]),
    )

    fields = ["current_state = ?", "next_steps = ?"]
    values = [args.current_state, args.next_steps]
    if args.status is not None:
        fields.append("status = ?")
        values.append(args.status)
    values.append(e["id"])
    conn.execute(f"UPDATE efforts SET {', '.join(fields)} WHERE id = ?", values)

    conn.commit()
    print(f"closed session #{s['id']} for effort '{e['slug']}'")


def cmd_session_list(conn, args):
    if args.effort:
        e = find_effort(conn, args.effort)
        rows = conn.execute(
            "SELECT * FROM sessions WHERE effort_id = ? ORDER BY started_at DESC",
            (e["id"],),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 50").fetchall()
    if not rows:
        print("(no sessions yet)")
        return
    for s in rows:
        print(f"#{s['id']:<4} [{s['status']:11}] started {s['started_at']}  ended {s['ended_at'] or '-'}")
        if s["summary"]:
            print(f"       {s['summary']}")


# ------------------------------------------------------------------ task --

def find_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        die(f"no task #{task_id}")
    return row


def cmd_task_add(conn, args):
    if args.effort == GENERIC_PROJECT_SLUG:
        args.effort = resolve_generic_effort_slug(conn)
    e = find_effort(conn, args.effort)
    cur = conn.execute(
        "INSERT INTO tasks (effort_id, description, created_session_id) VALUES (?, ?, ?)",
        (e["id"], args.description, args.session_id),
    )
    conn.commit()
    print(f"added task #{cur.lastrowid} to effort '{e['slug']}'")


def cmd_task_list(conn, args):
    query = (
        "SELECT t.*, e.slug AS effort_slug FROM tasks t "
        "JOIN efforts e ON e.id = t.effort_id WHERE 1=1"
    )
    params: list = []
    if args.effort:
        query += " AND e.slug = ?"
        params.append(args.effort)
    if args.status:
        query += " AND t.status = ?"
        params.append(args.status)
    elif not args.all:
        query += " AND t.status = 'open'"
    query += " ORDER BY t.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("(no tasks)")
        return
    for t in rows:
        print(f"#{t['id']:<4} [{t['status']:9}] ({t['effort_slug']}) {t['description']}")


def cmd_task_done(conn, args):
    t = find_task(conn, args.id)
    conn.execute(
        f"UPDATE tasks SET status = 'done', completed_at = {NOW}, completed_session_id = ? WHERE id = ?",
        (args.session_id, t["id"]),
    )
    conn.commit()
    print(f"closed task #{t['id']}")


def cmd_task_cancel(conn, args):
    t = find_task(conn, args.id)
    conn.execute(
        f"UPDATE tasks SET status = 'cancelled', completed_at = {NOW}, completed_session_id = ? WHERE id = ?",
        (args.session_id, t["id"]),
    )
    conn.commit()
    print(f"cancelled task #{t['id']}")


def cmd_task_show(conn, args):
    t = find_task(conn, args.id)
    e = conn.execute("SELECT * FROM efforts WHERE id = ?", (t["effort_id"],)).fetchone()
    print(f"# Task #{t['id']}  ({e['slug']})")
    print(f"status: {t['status']}")
    print(f"created: {t['created_at']}")
    if t["completed_at"]:
        print(f"completed: {t['completed_at']}")
    print(f"\n{t['description']}")


# ------------------------------------------------------------------ misc --

def cmd_status(conn, args):
    print("## Active efforts\n")
    rows = conn.execute(
        "SELECT e.*, p.name AS project_name FROM efforts e "
        "JOIN projects p ON p.id = e.project_id "
        "WHERE e.status IN ('active','blocked') ORDER BY e.updated_at DESC"
    ).fetchall()
    if not rows:
        print("(none)")
    for e in rows:
        open_session = conn.execute(
            "SELECT id FROM sessions WHERE effort_id = ? AND status = 'in_progress'",
            (e["id"],),
        ).fetchone()
        marker = " [session open]" if open_session else ""
        print(f"[{e['status']:8}] {e['project_name']} / {e['name']} ({e['slug']}){marker}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Jarvis project/effort/session tracker")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show active efforts at a glance").set_defaults(func=cmd_status)

    pj = sub.add_parser("project", help="Manage projects").add_subparsers(dest="subcommand", required=True)
    a = pj.add_parser("add"); a.add_argument("slug"); a.add_argument("name"); a.add_argument("--description"); a.set_defaults(func=cmd_project_add)
    a = pj.add_parser("list"); a.set_defaults(func=cmd_project_list)
    a = pj.add_parser("show"); a.add_argument("slug"); a.set_defaults(func=cmd_project_show)
    a = pj.add_parser("update")
    a.add_argument("slug")
    a.add_argument("--status", choices=["planned", "active", "blocked", "done", "abandoned"])
    a.add_argument("--description")
    a.set_defaults(func=cmd_project_update)

    ef = sub.add_parser("effort", help="Manage efforts").add_subparsers(dest="subcommand", required=True)
    a = ef.add_parser("add"); a.add_argument("project"); a.add_argument("slug"); a.add_argument("name"); a.add_argument("--description"); a.set_defaults(func=cmd_effort_add)
    a = ef.add_parser("list"); a.add_argument("--project"); a.set_defaults(func=cmd_effort_list)
    a = ef.add_parser("show"); a.add_argument("slug"); a.set_defaults(func=cmd_effort_show)
    a = ef.add_parser("update")
    a.add_argument("slug")
    a.add_argument("--status", choices=["planned", "active", "blocked", "done", "abandoned"])
    a.add_argument("--current-state")
    a.add_argument("--next-steps")
    a.add_argument("--description")
    a.set_defaults(func=cmd_effort_update)

    dc = sub.add_parser("decision", help="Log/query decisions").add_subparsers(dest="subcommand", required=True)
    a = dc.add_parser("add")
    a.add_argument("project")
    a.add_argument("--effort", help="Effort slug, if this decision is scoped to one")
    a.add_argument("--decision", required=True, help="What was decided")
    a.add_argument("--rationale", help="Why — the tradeoff or reason that drove it")
    a.set_defaults(func=cmd_decision_add)
    a = dc.add_parser("list")
    a.add_argument("--project"); a.add_argument("--effort")
    a.set_defaults(func=cmd_decision_list)
    a = dc.add_parser("show"); a.add_argument("id", type=int); a.set_defaults(func=cmd_decision_show)

    tk = sub.add_parser("task", help="Manage tasks").add_subparsers(dest="subcommand", required=True)
    a = tk.add_parser("add")
    a.add_argument("effort", help="Effort slug, or 'general' for this month's day-to-day bucket")
    a.add_argument("description")
    a.add_argument("--session-id", type=int, help="Session this task was opened during")
    a.set_defaults(func=cmd_task_add)
    a = tk.add_parser("list")
    a.add_argument("--effort")
    a.add_argument("--status", choices=["open", "done", "cancelled"])
    a.add_argument("--all", action="store_true", help="Show all statuses (default: open only)")
    a.set_defaults(func=cmd_task_list)
    a = tk.add_parser("done")
    a.add_argument("id", type=int)
    a.add_argument("--session-id", type=int, help="Session this task was closed during")
    a.set_defaults(func=cmd_task_done)
    a = tk.add_parser("cancel")
    a.add_argument("id", type=int)
    a.add_argument("--session-id", type=int)
    a.set_defaults(func=cmd_task_cancel)
    a = tk.add_parser("show"); a.add_argument("id", type=int); a.set_defaults(func=cmd_task_show)

    se = sub.add_parser("session", help="Manage sessions").add_subparsers(dest="subcommand", required=True)
    a = se.add_parser("start")
    a.add_argument("effort", help="Effort slug, or 'general' for this month's day-to-day bucket (auto-created)")
    a.set_defaults(func=cmd_session_start)
    a = se.add_parser("end")
    a.add_argument("effort", help="Effort slug, or 'general' for this month's day-to-day bucket")
    a.add_argument("--summary", required=True, help="What got done this session")
    a.add_argument("--current-state", required=True, help="Living snapshot of where the effort stands now")
    a.add_argument("--next-steps", required=True, help="What to pick up next time")
    a.add_argument("--decisions", help="Key decisions/tradeoffs made this session")
    a.add_argument("--blockers", help="Anything blocking progress")
    a.add_argument("--git-refs", help="Branch/commit refs touched this session")
    a.add_argument("--status", choices=["planned", "active", "blocked", "done", "abandoned"], help="New effort status, if it changed")
    a.set_defaults(func=cmd_session_end)
    a = se.add_parser("list"); a.add_argument("--effort"); a.set_defaults(func=cmd_session_list)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    conn = get_conn()
    try:
        args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
