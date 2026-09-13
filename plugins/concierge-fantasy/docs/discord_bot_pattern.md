# Discord Bot Pattern

How the FFB Draft Bot works, and how to reuse the same pattern for a
different channel/purpose. First built for a league's Discord server
during a live 2026 draft — see
[ffb_draft_bot_persona.md](ffb_draft_bot_persona.md) for that bot's
specific voice (template — fill in your own league's real details there).

## Why this shape

Goal: a Discord bot that reads a channel and replies, without running a
second billed LLM (no Anthropic API key, no separate OpenAI bill) and
without a standalone always-on process to babysit. Solution: no gateway
bot, no bot-owned brain at all — just a thin REST client, driven by a
live Claude Code session (billed against the Claude Code subscription)
on a polling timer.

```
Discord channel
    |  REST poll (GET /channels/{id}/messages?after=<checkpoint>)
    v
"${CLAUDE_PLUGIN_ROOT}/scripts/discord_ffb.py" poll   -->  new messages as JSON
    |
    v
Claude Code session (this chat) reads persona doc + pulls real data,
composes a reply
    |
    v
"${CLAUDE_PLUGIN_ROOT}/scripts/discord_ffb.py" send "text"  -->  POST back to the channel
```

Nothing runs unattended — the loop only exists while a Claude Code
session is open and ticking. That's a feature here (manual start/stop
for a live draft), not a limitation, but see "Generalizing" below if a
future use case needs always-on behavior instead.

## Pieces

- **`"${CLAUDE_PLUGIN_ROOT}/scripts/discord_ffb.py"`** — REST wrapper, no discord.py, no gateway
  connection. Three commands:
  - `poll [--peek]` — fetch messages since the last checkpoint, advance
    the checkpoint (unless `--peek`). First-ever run seeds the checkpoint
    at "now" instead of dumping channel history.
  - `send "text"` — POST a message.
  - `reset` — clear the checkpoint.
  - Checkpoint lives in `data/discord_ffb_state.json` (gitignored) —
    just `{"last_message_id": ...}`.
- **`docs/ffb_draft_bot_persona.md`** — voice/tone/safety rules, kept
  separate from the script on purpose so the user can tune personality
  without touching code. Read this file before composing any reply.
- **`.env`** — `DISCORD_BOT_TOKEN` (bot's auth) and `DISCORD_FFB_CHANNEL_ID`
  (which channel). Both required; see `.env.example` for how to obtain
  them (Developer Portal app + bot token, MESSAGE CONTENT INTENT enabled,
  invited via OAuth2 URL Generator with `bot` scope + View
  Channels/Send Messages permissions).
- **The loop** — a `CronCreate`/`/loop` job re-running the poll-reply-send
  prompt on an interval (1 minute is the practical floor; cron's minimum
  granularity). Session-only: dies when the Claude Code session closes,
  auto-expires after 7 days regardless. Started/stopped by the user per
  draft, not persistent infrastructure.

## Reply logic (what the loop prompt tells Claude to do each tick)

1. `poll` for new messages.
2. Skip anything `"bot": true` (the bot's own prior sends come back on
   the next poll — never reply to yourself).
3. Skip anything that isn't question-shaped, unless the user explicitly
   asks in this chat for a specific message to get a response.
4. For league/roster/keeper questions: pull real data before answering
   — `data/fantasy.db` via `"${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py"` for anything
   already synced locally (rosters, keeper declarations, etc.), or
   Sleeper's REST API directly for anything live that the local sync
   hasn't caught up to yet (this mattered during the actual draft —
   `draft_picks` stays empty locally unless something is actively
   syncing it, so live pick data was pulled straight from
   `GET https://api.sleeper.app/v1/draft/{draft_id}/picks` instead).
   Never fabricate a league fact if the data isn't available — say so
   in character instead.
5. Apply the persona doc's safety line: never engage straight with
   mean-spirited, graphic, or sexual content — ignore it, play dumb, or
   deflect with a one-word exclamation.
6. Apply any session overrides the user gave live in this chat (e.g. a
   standing joke about one player's picks).
7. `send` the reply.

## Generalizing to a different bot

The script and loop pattern aren't FFB-specific — only the persona doc
and the "pull real data from fantasy.db" step are. To stand up a new
bot for a different channel/purpose:

1. Copy `"${CLAUDE_PLUGIN_ROOT}/scripts/discord_ffb.py"` to a new name (e.g.
   `<purpose>_bot.py`), change the checkpoint filename, and
   parameterize or hardcode the new channel ID / env var names.
2. Write a new persona doc (character, voice, safety line, session
   overrides) — copy `ffb_draft_bot_persona.md`'s structure, it's
   already the reusable skeleton.
3. Same Discord app can usually be reused (same bot token) if the new
   channel is in the same server and the bot's already invited there —
   just point `DISCORD_<PURPOSE>_CHANNEL_ID` at the new channel. A bot
   in a different server needs its own invite (OAuth2 URL) even with
   the same token.
4. Swap step 4 of the reply logic above for whatever data source the
   new purpose needs (or drop it entirely for a pure-banter bot).
5. Start the loop the same way — `/loop` or `CronCreate` on the new
   poll-reply-send prompt, for as long as that session needs to run.

What stays constant across every bot built this way: no gateway
connection, no separate LLM billing, checkpoint-based polling (safe to
mix automatic loop ticks with the user manually asking "check
`<channel>`" — see the state-checkpoint note below), and the
never-reply-to-your-own-messages / never-fabricate-data / persona
safety-line rules.

## Notes from the first real run (2026-09-02)

- **Loop + manual checks don't conflict.** Both just call `poll`, which
  only returns messages since the checkpoint regardless of who
  triggered it — worst case one call returns empty.
- **1-minute cron floor.** Asked for 30s, cron can't go below 1 minute;
  rounded up and said so rather than silently picking a different
  number.
- **Local DB can lag a live event.** `data/fantasy.db`'s `draft_picks`
  table doesn't populate itself just because a draft is happening —
  something has to actively sync it. Mid-draft, that meant querying
  Sleeper's API directly for "what did roster X just pick" rather than
  trusting a stale/empty local table. Worth remembering for any future
  bot that needs live (not last-sync) data.
- **A computer crash killed the loop mid-draft.** The cron job is
  session-only; restarting required re-running `CronCreate` in the new
  session. No data was lost (checkpoint file persisted to disk), just
  the automatic cadence needed a manual restart.
