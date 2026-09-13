---
name: session-tracker
description: Track work across Claude Code sessions using a local SQLite DB (Project > Effort > Session — like Epic > Feature > Task). Use whenever the user wants to start/resume/end work on a named effort ("let's work on the home assistant setup effort", "what's the status of X", "wrap up this session"), or wants to create a new project/effort. This is the session-continuity system for this workspace — it replaces writing markdown handoff files.
---

# Session Tracker

This workspace tracks ongoing work in a local SQLite DB instead of markdown
handoff files, so a new session can resume an effort without re-reading a pile
of history. All access goes through the bundled `jarvis_db.py` (stdlib
Python, no dependencies) — run it with
`python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" <command>` from the
instance root (the DB file itself lives in the instance, at `data/jarvis.db`
by default — see Storage below).

## Model

```
Project  (Epic)     — a long-running initiative, e.g. "Home Lab"
  Effort (Feature)   — a scoped chunk of work, e.g. "Home Assistant Setup"
    Session (Task)   — one Claude Code conversation working that effort
```

Each effort carries two **living fields** that get overwritten every time a
session ends: `current_state` (where things stand right now) and `next_steps`
(what to do next). Sessions themselves are an append-only log — useful for
digging into *why* a past decision was made, but not needed for day-to-day
resume.

## Starting work on an effort

When the user says something like "let's start a session on X" or "let's pick
back up on X":

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" session start <effort-slug>
```

This prints the project, effort description, `current_state`, `next_steps`,
and the last completed session's summary — read that output, it's the
resume context. It also auto-abandons any stale `in_progress` session left
open from a crashed/forgotten previous session.

If you don't know the effort's slug, list them first:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" effort list
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" status          # active efforts only, at a glance
```

If the effort doesn't exist yet, ask which project it belongs to (or whether
it's a new project) before creating it — don't guess a hierarchy.

## Day-to-day / one-off requests: the "general" bucket

Not everything belongs to a named effort. For genuinely one-shot asks
(a quick question, a one-off script, "explain this") there's nothing to
resume later — don't force a session start/end around those at all.

For small requests that *are* worth remembering later but don't fit an
existing effort, use the special slug `general` in place of an effort slug:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" session start general
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" session end general --summary "..." --current-state "..." --next-steps "..."
```

This resolves to a rolling monthly bucket (`general-2026-07`, project
`general`), auto-created on first use each month — no need to create it
yourself. Monthly, not evergreen: `current_state`/`next_steps` assume one
coherent thread of work, which unrelated one-offs aren't, so those fields
can be terse ("n/a" is fine) — the point is just keeping a scoped log.

## Ending a session

Do this at the natural end of a work session, or when the user asks to wrap
up. Summarize the conversation yourself — don't ask the user to write it.

### Lessons-learned pass (automatic, every session end)

Before running `session end`, self-review this conversation — don't ask, just
do it:

- **What went wrong or caused friction?** Wrong approach tried first, wasted
  round-trips, a wrong assumption, a tool/command that didn't work as
  expected.
- **What worked well and is worth repeating?** An approach, delegation
  pattern, or command sequence the user confirmed or that clearly paid off.
- **Was there a repeatable script/process hiding in this session?** Manual
  steps done more than once, or steps the user would plausibly want again
  later. If yes, don't just note it — write the script into the instance's
  own `scripts/` directory (or propose it first if it's non-trivial or
  touches something risky).

Route what you find:

- **Effort-specific** (only matters for this effort's continuation) →
  fold into `--decisions` or `--blockers` below, not a separate memory.
- **Generalizes beyond this effort** (workspace-wide workflow preference,
  a Claude Code/tool gotcha, a correction/confirmation about how to work) →
  save it as a memory per whatever memory-system rules this instance's
  `CLAUDE.md` defines — same bar as always (surprising, non-obvious, not
  derivable by re-reading code). Don't duplicate an existing memory; update
  it instead.

This is a quick self-check, not a report to hand the user — only surface it
if something's notable enough to flag.

### Uncommitted work check (automatic, every session end)

Run `git status` before closing the session. If anything's uncommitted
(staged or not), flag it in the wrap-up message — don't commit or push it
yourself unless this instance's rules say otherwise. This is a check, not an
action — surface what's dirty, let the user decide (delegate to a `git`
subagent, e.g. `concierge-git`, if installed and they say go).

Then record the session:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" session end <effort-slug> \
  --summary "What got done this session" \
  --current-state "Where things stand now" \
  --next-steps "What to pick up next time" \
  --decisions "Key tradeoffs/decisions made (optional)" \
  --blockers "Anything blocking progress (optional)" \
  --status active   # only if the effort's status changed (planned/active/blocked/done/abandoned)
```

`--summary`, `--current-state`, and `--next-steps` are required — they're
what makes the next session's resume actually work. Write `current-state` and
`next-steps` as if briefing someone with zero memory of this conversation.

## Tasks

Tasks are next-actions (GTD sense) scoped to an **effort**, not a session — a
task often gets opened in one session and closed several sessions later, so
tracking it per-session would hide open work from a "what's left" view.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" task add <effort-slug> "<description>"
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" task list                    # open tasks, all efforts
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" task list --effort <slug>    # open tasks for one effort
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" task list --all               # every status
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" task done <id>
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" task cancel <id>
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" task show <id>
```

Add a task whenever a concrete to-do surfaces mid-conversation that isn't
worth doing right now (or belongs to someone else, or needs info you don't
have yet). `effort show`/`session start` already print an effort's open
tasks as part of its resume context — no need to also restate them in
`next_steps`.

### Notes-vault mirror (optional)

If a notes-vault subagent is installed for this instance (e.g. `obsidian`
from `concierge-memory-obsidian`), mirror tasks there so they're browsable
outside Claude Code. Skip this section entirely if no such subagent is
installed — the DB alone is a complete system on its own.

Whenever a task is added, completed, or cancelled, delegate to that subagent
to keep it in sync:

- One note per task at `Tasks/<effort-slug>-<task-id>.md`:

  ```markdown
  ---
  status: <open|done|cancelled>
  effort: <effort-slug>
  created: <task created_at, date only>
  completed: <task completed_at, date only, or blank>
  ---

  # <task description>
  ```

- A `Tasks.base` file (vault root) provides the browsable view — table
  grouped/filterable by `status` and `effort`. Created once; if it doesn't
  exist yet, use an Obsidian-Bases-authoring skill if one is installed to
  generate correct syntax, then have the vault subagent write it.

Don't mirror tasks under the `general` bucket's monthly efforts into
separate per-month noise — same note path pattern works fine since each
task note is already scoped by its own task id.

## Creating projects and efforts

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" project add <slug> "<Name>" --description "..."
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" effort add <project-slug> <effort-slug> "<Name>" --description "..."
```

Slugs are kebab-case and globally unique for efforts (so `session start` can
resolve one by slug alone without needing the project too).

## Other commands

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" project list
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" project show <slug>
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" project update <slug> --status active   # triggers the notes-vault mirror, see below
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" effort show <slug>          # same context as `session start`, read-only
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" effort update <slug> --status blocked
python "${CLAUDE_PLUGIN_ROOT}/scripts/jarvis_db.py" session list --effort <slug>  # full history for an effort
```

## Storage

DB file lives at `data/jarvis.db` relative to the instance root (not the
plugin), gitignored — transactional/runtime state stays out of git; only the
schema, in `jarvis_db.py` itself, is version-controlled (inside this
plugin). It's created automatically on first run. Override the location with
the `CONCIERGE_DATA_DIR` or `CONCIERGE_DB_PATH` environment variables if
this instance needs the DB somewhere else.

## Notes-vault project dashboard (optional)

If a notes-vault subagent is installed, the DB stays the tactical layer
(efforts, sessions, next-steps); mirror only the **project** level, at a
glance, into the vault so the user can browse it outside Claude Code —
don't mirror effort or session detail there, that duplication would drift.

Whenever a project is created via `project add`, or a project's `status`
changes via `project update`/otherwise, delegate to the vault subagent to
create/update a note at `Projects/<project-slug>.md`:

```markdown
---
status: <status>
started: <project created_at, date only>
---

# <Project Name>

<Project description>
```

Keep it to that — name, status, start date, one-paragraph description. Don't
create a project note for the `general` bucket; it isn't a real project.
