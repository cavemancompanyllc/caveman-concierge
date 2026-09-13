# <Your Instance Name>

<One or two sentences: what this instance is for, who it's for (you? your
family? your business?), and what makes it distinct from any other
Caveman Concierge instance you run.>

This instance is built on [Caveman Concierge](https://github.com/cavemancompanyllc/caveman-concierge)
— a pick-and-choose plugin store for Claude Code. Capabilities (notes,
email, finance tracking, etc.) come from plugins installed via
`claude plugin install <name>@caveman-concierge`, not from files in this
repo. This repo is deliberately thin: your persona, your facts, your
secrets, your data.

## Who you're talking to

<Name, pronouns if relevant, anything the orchestrator should always know
without being told — e.g. timezone, household members, work context.>

## Orchestrator name

<If you want the orchestrator to go by a name (Jarvis, Friday, whatever)
instead of "the assistant," say so here and ask it to introduce itself
that way.>

## Installed plugins

<Keep this list in sync with `claude plugin list` so a fresh session (or
you, six months from now) knows what's active without running a command.
Delete rows for plugins you don't use.>

| Plugin | What it's for |
|---|---|
| `concierge-core` | Session tracking, token usage |
| `concierge-git` | Git commits/branches, secret scanning |
| | |

## Key rules

- No hardcoded secrets — use `.env` (gitignored) or plugin `userConfig`
- All git operations go through the `git` subagent (from `concierge-git`)
  — it scans for secrets before every commit
- <Add your own instance-specific rules here — what needs your sign-off
  before it happens, what should never be automated, etc.>
