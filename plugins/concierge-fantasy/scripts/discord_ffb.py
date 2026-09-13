"""
Discord poll/send helper for the #ffb live-draft bot.

No gateway connection, no discord.py — plain REST polling against the
Discord bot API. Designed to be shelled out to from a Claude Code session
(via /loop) during a live draft: poll for new messages, generate a reply
in this session (billed against the Claude Code subscription, not a
separate API key), then send it back. See docs/ffb_draft_bot_persona.md
for the voice/tone to use when composing replies, and
data/fantasy.db (scripts/fantasy_index.py) for league data.

Usage:
    python scripts/discord_ffb.py poll              # new messages since last checkpoint, as JSON lines
    python scripts/discord_ffb.py poll --peek        # same, but doesn't advance the checkpoint
    python scripts/discord_ffb.py send "message text"
    python scripts/discord_ffb.py reset              # clear checkpoint (re-poll from now)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(os.environ.get("CONCIERGE_HOME") or Path.cwd())
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "data" / "discord_ffb_state.json"

DISCORD_API = "https://discord.com/api/v10"


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("DISCORD_FFB_CHANNEL_ID", "")


def _headers() -> dict:
    if not BOT_TOKEN:
        print("DISCORD_BOT_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)
    return {"Authorization": f"Bot {BOT_TOKEN}"}


def _require_channel() -> str:
    if not CHANNEL_ID:
        print("DISCORD_FFB_CHANNEL_ID not set in .env", file=sys.stderr)
        sys.exit(1)
    return CHANNEL_ID


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_message_id": None}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def poll(peek: bool = False) -> list[dict]:
    channel_id = _require_channel()
    state = _load_state()

    if not state.get("last_message_id"):
        # First run: seed the checkpoint at "now" instead of dumping the
        # whole channel history as if it just happened.
        resp = httpx.get(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers=_headers(),
            params={"limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        latest = resp.json()
        if latest and not peek:
            state["last_message_id"] = latest[0]["id"]
            _save_state(state)
        return []

    params = {"limit": 50, "after": state["last_message_id"]}
    resp = httpx.get(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=_headers(),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    messages = resp.json()  # Discord returns newest-first
    messages.sort(key=lambda m: int(m["id"]))  # oldest-first for reading order

    if messages and not peek:
        state["last_message_id"] = messages[-1]["id"]
        _save_state(state)

    return [
        {
            "id": m["id"],
            "author": m["author"]["username"],
            "bot": m["author"].get("bot", False),
            "content": m["content"],
        }
        for m in messages
    ]


def send(content: str) -> None:
    channel_id = _require_channel()
    resp = httpx.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=_headers(),
        json={"content": content},
        timeout=15,
    )
    resp.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    poll_p = sub.add_parser("poll", help="Fetch new messages since last checkpoint")
    poll_p.add_argument("--peek", action="store_true", help="Don't advance the checkpoint")

    send_p = sub.add_parser("send", help="Post a message to the channel")
    send_p.add_argument("text")

    sub.add_parser("reset", help="Clear the checkpoint (next poll starts from now)")

    args = parser.parse_args()

    if args.command == "poll":
        for msg in poll(peek=args.peek):
            print(json.dumps(msg))
    elif args.command == "send":
        send(args.text)
    elif args.command == "reset":
        _save_state({"last_message_id": None})
        print("checkpoint cleared")


if __name__ == "__main__":
    main()
