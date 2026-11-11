# Writing a plugin for this marketplace

## Layout

```
plugins/concierge-<name>/
├── .claude-plugin/
│   └── plugin.json          # required — the manifest
├── agents/                  # optional — subagent .md files
├── skills/                  # optional — SKILL.md directories
├── commands/                # optional — flat .md skill files
├── scripts/                 # optional — backing Python/shell scripts
├── .mcp.json                # optional — if this plugin runs an MCP server
├── requires.json             # CLIs and env vars it needs (checked by `cavecore doctor`)
├── rules/session.md          # optional — always-on rules, loaded at session start
├── schema/*.sql              # optional — ledger tables (CREATE TABLE IF NOT EXISTS)
├── requirements.txt          # optional — pip deps, if any
└── README.md                 # optional but recommended if there's a
                               # known limitation or non-obvious setup step
```

Everything except `.claude-plugin/plugin.json` lives at the plugin root,
never inside `.claude-plugin/`.

## The manifest

```json
{
  "$schema": "https://anthropic.com/claude-code/plugin.schema.json",
  "name": "concierge-<name>",
  "version": "0.1.0",
  "description": "One sentence: what it does, what it needs.",
  "author": { "name": "...", "email": "..." },
  "homepage": "https://github.com/cavemancompanyllc/caveman-concierge",
  "license": "MIT",
  "keywords": ["..."],
  "dependencies": ["caveman-core@caveman-core"]
}
```

Every plugin here depends on `caveman-core` (a separate marketplace; this
repo's `marketplace.json` allows it via `allowCrossMarketplaceDependenciesOn`).
Put required CLIs and env vars in `requires.json` beside the manifest, not in
`plugin.json`, which `claude plugin validate --strict` keeps to known fields:

```json
{ "cli": ["python", "ollama"], "env": ["MY_API_KEY"] }
```

If your plugin needs per-instance configuration (a path, an API key, a
host), declare it as `userConfig` rather than hardcoding a default or
requiring `.env`:

```json
"userConfig": {
  "vault_path": {
    "type": "directory",
    "title": "Vault path",
    "description": "...",
    "required": true
  },
  "api_key": {
    "type": "string",
    "title": "API key",
    "description": "...",
    "sensitive": true
  }
}
```

`sensitive: true` routes the value to the OS keychain / `.credentials.json`
instead of plaintext `settings.json`. Every `userConfig` field needs both
`title` and `description` — validation fails otherwise.

Reference config values in your `.mcp.json`, hooks, or agent/skill markdown
with `${user_config.key_name}`. `${CLAUDE_PLUGIN_ROOT}` resolves to this
plugin's own install directory — use it for any path into your own
`scripts/`.

## The one rule that matters most: don't resolve paths off `__file__`

A script in this repo does **not** live in the instance repo once
installed — it lives in `~/.claude/plugins/cache/...`. Every plugin ported
into this marketplace so far had at least one script that used to do:

```python
ROOT = Path(__file__).resolve().parent.parent   # WRONG once this is a plugin
```

That resolves into the plugin's own cache directory, not the user's
instance — silently breaking `.env` loading, DB paths, token caches,
anything instance-relative. The fix used throughout this repo is Caveman
Core's resolver and `.env` loader:

```python
from caveman_core import env as cc_env
from caveman_core.paths import workspace_root

ROOT = workspace_root()   # CAVEMAN_HOME, legacy CONCIERGE_HOME, workspace.yaml walk-up, else cwd
cc_env.load(ROOT)         # <instance>/.env; never overrides variables already set
```

Local-LLM calls go through `caveman_core.llm.chat(...)` rather than a
hand-rolled HTTP call.

- For a script invoked via the **Bash tool** (an agent runs
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"`): `cwd` is already the
  instance root — Claude Code's Bash tool runs there, and the
  `workspace.yaml` walk-up (or the cwd fallback) finds it.
- For a script that's an **MCP server** launched via `.mcp.json`: cwd is
  *not* guaranteed to be the instance root. Set `CAVEMAN_HOME`
  explicitly in your `.mcp.json`:
  ```json
  "env": { "CAVEMAN_HOME": "${CLAUDE_PROJECT_DIR}" }
  ```
- For a script invoked by something **outside Claude Code entirely** (a
  Windows Scheduled Task, cron, a systemd timer) — neither mechanism
  applies, and the plugin's own folder changes with every version. Schedule
  `cavecore headless --workspace <instance> --prompt-file <file>` instead:
  `cavecore` is installed once per machine, so its path never moves.

Test both paths before you consider a script "ported": run it with cwd set
to a real instance directory and confirm it finds real data, and check
what happens with `Path(__file__)` from inside the plugin's actual
install location (not your working checkout) to make sure you didn't
leave a stray `__file__`-relative path for a sibling data file.

## Real personal/instance data never ships in the plugin

If a script or skill needs data that's specific to one person's setup
(a routing table naming real family members, real league names, a
persona doc with real Discord usernames), it goes through the
`X.example.md`/`X.example.json` pattern used by `concierge-family`
(`routing.example.md`) and `concierge-media`-style instances
(`config/media.example.json`): the plugin ships the template, the real
file is instance-local and gitignored, never committed anywhere in this
repo. Scrub thoroughly — a full-text search for names/emails/IDs before
committing is worth the extra minute; several were missed on the first
pass while porting the fantasy and family plugins here.

## Testing locally

```bash
claude --plugin-dir ./plugins/concierge-<name>
```

Or point a real instance's marketplace at your local checkout instead of
GitHub while developing:

```bash
claude plugin marketplace add /path/to/your/caveman-concierge/checkout
claude plugin install concierge-<name>@caveman-concierge --scope project
```

Validate before committing:

```bash
claude plugin validate .
```

## Adding it to the marketplace

Add an entry to `.claude-plugin/marketplace.json` at the repo root:

```json
{
  "name": "concierge-<name>",
  "source": "./plugins/concierge-<name>",
  "description": "...",
  "version": "0.1.0",
  "category": "productivity"
}
```

Run `claude plugin validate .` at the repo root (not just inside the
plugin directory) to confirm the marketplace manifest itself is still
valid, then open a PR.
