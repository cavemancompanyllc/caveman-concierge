# Caveman Concierge

A pick-and-choose plugin store for [Claude Code](https://claude.com/claude-code)
subagents and skills — the same personal-orchestrator harness the author
runs day to day, packaged so anyone can clone the parts they want into
their own instance.

You are not cloning an app. You're adding a marketplace, then installing
only the capabilities you want — an Obsidian-backed memory, Gmail/Calendar,
local-only bank-statement tracking, a Plex/media-stack manager, fantasy
football tools, a document renderer, a diagram generator — each one an
independent plugin with its own config. Leave the rest out.

```bash
claude plugin marketplace add cavemancompanyllc/caveman-concierge
claude plugin install concierge-core@caveman-concierge --scope project
claude plugin install concierge-git@caveman-concierge --scope project
```

Full walkthrough: [docs/getting-started.md](docs/getting-started.md).
Full plugin list + what each one needs: [docs/agent-catalog.md](docs/agent-catalog.md).

## Why this exists

Most "AI assistant" setups are one app, one config, one person. This one
is built to run as several differently-configured instances off one
codebase — a personal instance, a business instance, a kid-safe instance —
without forking anything. An instance is deliberately thin: a `CLAUDE.md`,
a `.env`, some data. Every actual capability — an agent, a skill, an MCP
server — is a plugin, installed independently, versioned independently,
shareable independently.

## Architecture

```
caveman-concierge/              ← this repo — the marketplace itself
├── .claude-plugin/marketplace.json
├── plugins/
│   ├── concierge-core/         ← session tracking, foundation
│   ├── concierge-git/          ← commits + secret scanning
│   ├── concierge-memory-obsidian/
│   ├── concierge-google/
│   ├── concierge-finance/
│   └── ...                     ← one directory per capability
├── instance-template/          ← copy this to start a new instance
└── docs/

your-instance/                  ← a separate, tiny repo (or no repo at all)
├── CLAUDE.md                   ← who you are, what this instance is for
├── .env                        ← secrets (gitignored)
├── .claude/settings.json       ← which plugins are enabled
└── data/                       ← local SQLite stores, caches (gitignored)
```

Add a feature once, here, in the plugin repo. Every instance — yours,
a friend's, a differently-configured one for a different purpose — picks
it up on its next `claude plugin update`. Nothing about an instance needs
to change except which plugins it has installed and how they're
configured.

This structure exists because it had to survive a real migration, not a
green-field design: every plugin in this repo was extracted from a live,
years-running personal Claude Code workspace, one phase at a time,
verifying at each step that porting a script out of the instance repo and
into a plugin's install directory didn't silently break its path
assumptions (see [docs/writing-a-plugin.md](docs/writing-a-plugin.md) for
the specific bug class this caught, repeatedly).

## What's here

See [docs/agent-catalog.md](docs/agent-catalog.md) for the full table —
foundation plugins (session tracking, git), general productivity (notes,
email, local-LLM offloading, diagrams, documents), and personal-domain
plugins (finance, paperwork, fantasy football, family/school tracking,
media server management).

## Writing your own plugin

Want to add a capability, or fork this to build your own marketplace?
See [docs/writing-a-plugin.md](docs/writing-a-plugin.md) — manifest
format, `userConfig`, and the path-resolution pitfall that broke almost
every script during the initial port.

## Status

Actively developed, extracted in phases from a real personal instance.
Structure and plugin boundaries may still shift. Check
[docs/roadmap.md](docs/roadmap.md) for what's done and what's next.

## License

MIT.
