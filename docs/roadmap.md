# Roadmap

Phased port of the Jarvis personal workspace into standalone, installable
plugins under this marketplace. See conversation history / vault notes for
full rationale — short version:

- **Phase 0** (this): repo + marketplace skeleton, port `concierge-docs`
  (lowest-risk, no personal data, no MCP server) to prove the loop end to end.
- **Phase 1**: `concierge-core` (session-tracker skill, `jarvis_db.py`,
  usage report) + `concierge-git`. Prove instance-relative data paths work
  from inside a plugin.
- **Phase 2**: MCP-bearing plugins — `concierge-memory-obsidian`,
  `concierge-local-llm`, `concierge-google`. Prove `.env`/cwd assumptions for
  MCP servers launched from a plugin.
- **Phase 3**: remaining personal plugins (`concierge-finance`,
  `concierge-paperwork`, `concierge-fantasy`, `concierge-family`,
  `concierge-media`). Personal instance (`D:\Jarvis`) becomes fully
  plugin-driven.
- **Phase 4**: scrub for secrets/personal references, finish docs, publish
  public.
- **Phase 5**: private marketplace + business instance
  (`concierge-m365`, `concierge-biz-finance`) under Caveman Company LLC.
- **Phase 6**: kids instance, YouTube demo.
