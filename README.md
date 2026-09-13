# Caveman Concierge

A pick-and-choose plugin store for [Claude Code](https://claude.com/claude-code)
subagents and skills — the same personal-orchestrator harness Mike (Caveman
Company LLC) runs day to day, packaged so anyone can clone the parts they want
into their own instance.

Each capability (Obsidian notes, Google Workspace, finance tracking, diagram
generation, document rendering, etc.) is its own plugin. Install only what you
need; leave the rest out.

## Quick start

```bash
claude plugin marketplace add cavemancompany/caveman-concierge
claude plugin install concierge-docs@caveman-concierge
```

Then configure it at install time:

```bash
claude plugin install concierge-docs@caveman-concierge \
  --config d2_binary="C:\Program Files\D2\d2.exe" \
  --config pandoc_binary="C:\Program Files\Pandoc\pandoc.exe"
```

## What's here today

| Plugin | What it does |
|---|---|
| `concierge-docs` | D2 diagram generation + Pandoc document rendering subagents |

More plugins land as the harness is ported over — see `docs/roadmap.md`.

## Status

Early — this repo is being carved out of a personal workspace. Structure and
plugin boundaries will shift for a bit before this is friend-ready.
