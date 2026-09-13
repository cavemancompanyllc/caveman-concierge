# Agent catalog

Every plugin currently in the `caveman-concierge` marketplace, what it
does, what it needs, and whether it needs anything beyond `pip install`.

Install any of these with:
```bash
claude plugin install <name>@caveman-concierge --scope project
```
Add `--config KEY=VALUE` for any `userConfig` option a plugin declares —
run `claude plugin details <name>@caveman-concierge` to see its exact
options, or check the plugin's own description below.

## Foundation

| Plugin | What it does | Needs |
|---|---|---|
| `concierge-core` | Session-continuity tracker (Project → Effort → Session in local SQLite) + a local Claude Code token-usage report. Most other plugins here assume this is installed. | Nothing — stdlib Python only. |
| `concierge-git` | Git commit/branch/merge subagent with a mandatory pre-commit secret scan. Can optionally manage a second, independently-versioned repo (e.g. a notes vault) via `userConfig`. | Nothing — Bash + git. |

## Productivity

| Plugin | What it does | Needs |
|---|---|---|
| `concierge-docs` | D2 diagram generation + Pandoc document rendering (PDF/DOCX/HTML/etc.) subagents. | [D2 CLI](https://d2lang.com), [Pandoc](https://pandoc.org) installed and on PATH (or pass their paths via `userConfig`). |
| `concierge-memory-obsidian` | Reads/writes an Obsidian vault via the Obsidian Local REST API plugin over MCP. | Obsidian with the [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) community plugin enabled; its API key via `userConfig`. |
| `concierge-local-llm` | Exposes a local Ollama model as an MCP tool, for bounded low-stakes text work (summarizing, boilerplate, classification) — keeps that traffic off Claude's API. | [Ollama](https://ollama.com) running locally with at least one model pulled. |
| `concierge-google` | Gmail, Calendar, Drive, and Tasks via OAuth2. Interactive one-time auth setup is separate from the MCP server. | A Google Cloud OAuth Desktop-app client (Client ID/Secret via `userConfig`); run the plugin's `google_auth_setup.py` once from a terminal with a browser. |

## Personal

| Plugin | What it does | Needs |
|---|---|---|
| `concierge-finance` | Parses Chase/BoA CSV/OFX/QFX statement exports, categorizes transactions via a local Ollama model, answers spending/trend questions. Bank data never leaves the machine. | `concierge-local-llm`'s Ollama dependency; manual CSV/OFX exports dropped in `data/finance/inbox/`. |
| `concierge-paperwork` | Finds and suggests filing for personal documents (leases, tax returns, IDs) synced from a cloud drive. Search-only, extracts/classifies locally. | A local synced folder (`PAPERWORK_SOURCE_DIR`); `docling` (pip) + the `pdftotext` binary (poppler-utils, not pip) on PATH; Ollama. |
| `concierge-fantasy` | Tracks Sleeper leagues and (read-only) DraftKings/ESPN fantasy football: rosters, stats, lineup optimization, VBD draft board, live draft-day signals. | Sleeper league ID(s) (no auth needed); optionally ESPN cookies + league ID, a Discord bot token for the optional draft-chat bot. |
| `concierge-family` | Processes kids' school email into a notes vault, calendar events, and Google Tasks. **Ships a template only** (`routing.example.md`) — see the plugin's own README for a known schema limitation before using it with your own kids. | `concierge-google` (required), `concierge-memory-obsidian` (optional); your own filled-in routing config at `config/school-mail-routing.md`. |
| `concierge-media` | Manages a Plex server and its acquisition stack (qBittorrent, Prowlarr, Radarr, Sonarr, Jellyseerr) over HTTP — search/grab/download, automation oversight, family request triage. | That stack already running somewhere reachable over HTTP; `yt-dlp` + `ffmpeg` for the web-video-download path. |

## Picking a starting set

- **Just want the session-continuity + git workflow?** `concierge-core` + `concierge-git`.
- **Personal daily-driver like the reference instance?** Add `concierge-memory-obsidian`, `concierge-local-llm`, `concierge-google`.
- **Specific domain plugins** (finance, fantasy, paperwork, family, media) are independent of each other — install only the ones that match things you actually track.

None of the personal-domain plugins depend on each other's data, but
`concierge-finance` and `concierge-paperwork` both lean on
`concierge-local-llm` being installed for their local-model calls (the
scripts talk to Ollama's REST API directly, so strictly the plugin isn't a
hard dependency — just make sure Ollama itself is running).
