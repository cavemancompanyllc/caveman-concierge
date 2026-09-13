# Getting started

## 1. Prerequisites

- [Claude Code](https://claude.com/claude-code) installed and authenticated.
- Python 3.11+ on PATH (most plugins here are Python scripts — stdlib
  only for the foundation plugins, a few third-party packages for others;
  each plugin's `requirements.txt`, if it has one, lists what it needs).
- Anything a specific plugin needs beyond that — see
  [agent-catalog.md](agent-catalog.md) before installing it.

## 2. Set up an instance

An instance is just a directory: your `CLAUDE.md`, your `.env`, your data.
All actual capability comes from plugins, installed at the instance level.

```bash
mkdir my-concierge && cd my-concierge
git init   # optional but recommended — see "One repo or many?" below
```

Copy [`instance-template/`](../instance-template/) from this repo into
your new directory (or just its `CLAUDE.md`, `.env.example`, and
`.claude/settings.json` if you'd rather build up from nothing) and follow
its own README for the fill-in-the-blanks steps.

## 3. Add the marketplace and install plugins

```bash
claude plugin marketplace add cavemancompanyllc/caveman-concierge
claude plugin install concierge-core@caveman-concierge --scope project
claude plugin install concierge-git@caveman-concierge --scope project
```

`--scope project` writes to this instance's own `.claude/settings.json`
(safe to commit — it just lists which plugins are enabled, not their
config) so the choice travels with the repo. Add more plugins the same
way; see [agent-catalog.md](agent-catalog.md) for the full list and what
each one needs.

Plugins that need configuration (a vault path, an API key, a host) take
it via `--config`:

```bash
claude plugin install concierge-memory-obsidian@caveman-concierge --scope project \
  --config vault_path="/path/to/vault" \
  --config api_key="..."
```

Run `claude plugin details <name>@caveman-concierge` to see a plugin's
exact `userConfig` options before installing, or `claude plugin list` any
time to see what's currently enabled in this instance.

## 4. Start using it

Open a Claude Code session in your instance directory. Installed agents
are available by name (mention them, or let the orchestrator delegate to
them automatically when a request matches their description). Installed
skills activate automatically when relevant, or via `/plugin-name:skill-name`.

## One repo, or many?

If you're setting up more than one instance (personal + a kids' instance,
say, or a work instance with different plugins), don't fork this repo —
each instance is its own tiny repo (or no repo at all, if you don't need
version history for it), pointed at this same marketplace. Add features
here, in the shared marketplace repo, and every instance picks them up on
its next `claude plugin update`. See the main
[README](../README.md#architecture) for the full reasoning.

## Updating

```bash
claude plugin marketplace update caveman-concierge
claude plugin update concierge-core@caveman-concierge   # or any specific plugin
```

## Troubleshooting

- **Never remove a marketplace registration you have plugins installed
  from, even to re-add it under the same name.** `claude plugin
  marketplace remove <name>` wipes `enabledPlugins` *and every plugin's
  `userConfig` values* for that marketplace — re-adding it (even pointed
  at the exact same source) does not restore either; you have to
  reinstall each plugin with its `--config` values again from scratch.
  If you need to change a marketplace's source (e.g. switching from a
  local dev path to the real GitHub URL), do it by editing
  `extraKnownMarketplaces` in `settings.json` directly, or just accept
  the reinstall cost and keep a record of your `--config` values
  somewhere before you do it.
- **"N userConfig options not yet set"** after install — the plugin has
  required config you didn't pass. Re-run install with `--config`, or
  uninstall/reinstall (Claude Code applies `--config` cleanly on a fresh
  install; re-running install on an already-installed plugin without
  `--config` won't retroactively apply new values).
- **A newly-installed MCP-server plugin's tools don't show up** — MCP
  server changes need `/reload-plugins` or a session restart to take
  effect; this is a Claude Code behavior, not specific to this
  marketplace.
- **An agent you removed a local `.claude/agents/<name>.md` copy of, in
  favor of the plugin version, doesn't reappear immediately** — same
  cause as above; restart the session.
- **A script errors with a missing package** — check that plugin's
  `requirements.txt` (if present) and `pip install -r` it. There's no
  automatic dependency installer yet (tracked as a future improvement).
