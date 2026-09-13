# Roadmap

Phased port of the Jarvis personal workspace into standalone, installable
plugins under this marketplace. See conversation history / vault notes for
full rationale — short version:

- **Phase 0** ✅: repo + marketplace skeleton, port `concierge-docs`
  (lowest-risk, no personal data, no MCP server) to prove the loop end to end.
- **Phase 1** ✅: `concierge-core` (session-tracker skill, `jarvis_db.py`,
  usage report) + `concierge-git`. Proved instance-relative data paths work
  from inside a plugin — found and fixed a real `__file__`-based path bug.
- **Phase 2** ✅: MCP-bearing plugins — `concierge-memory-obsidian`,
  `concierge-local-llm`, `concierge-google`. Proved `.env`/token-path
  resolution for MCP servers launched from a plugin via a `CONCIERGE_HOME`
  env var each plugin's `.mcp.json` sets to `${CLAUDE_PROJECT_DIR}`.
- **Phase 3** ✅: remaining personal plugins — `concierge-finance`,
  `concierge-paperwork`, `concierge-fantasy`, `concierge-family`,
  `concierge-media`. Personal instance (`D:\Jarvis`) is now fully
  plugin-driven except `research.md` (not yet ported — low priority, no
  personal-data coupling to worry about).
  - Same `ROOT`-path bug found and fixed across all 9 backing scripts.
  - Scrubbed real personal identifiers beyond the expected: a Discord
    username + Sleeper owner_id (`ffb_draft_bot_persona.md` → template),
    a hardcoded ESPN league name in `fantasy_pregame_scheduler.py` →
    `ESPN_LEAGUE_NAME` env var.
  - Design fix: `school-mail`'s real `routing.md` moved from "next to the
    skill" (broken once the skill is plugin-hosted/read-only) to
    `config/school-mail-routing.md` at the instance root, matching the
    `config/media*.json` convention.
  - Known limitation, documented not fixed: `concierge-family`'s
    `school_db.py` hardcodes two kid slugs (`rex`/`rose`) as literal SQL
    `CHECK` constraint values — a schema migration, not a text edit, out
    of scope while porting a live instance's existing data. See
    `plugins/concierge-family/README.md`.
  - Deliberately NOT ported (instance-specific automation, not reusable
    plugin material): `chase_deal_scan.ps1`/`.txt` + `csr_benefits.json`
    (one hardcoded credit card's Task Scheduler job); `run_headless_claude.ps1`
    / `_recurring.ps1` (invoked directly by Windows Task Scheduler, which
    never injects `CONCIERGE_HOME` — these need a permanent path outside
    any plugin's versioned cache dir).
- **Phase 4** (next): scrub for any remaining secrets/personal references,
  finish docs (getting-started, writing-a-plugin, agent catalog), write a
  README landing page, publish public.
- **Phase 5**: private marketplace + business instance
  (`concierge-m365`, `concierge-biz-finance`) under Caveman Company LLC.
- **Phase 6**: kids instance, YouTube demo.
