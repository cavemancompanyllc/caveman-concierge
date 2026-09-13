#!/usr/bin/env python3
"""
Local Claude Code token-usage estimator.

Parses the JSONL transcripts Claude Code already writes under
~/.claude/projects/**/*.jsonl and sums token counts per day/model, across
every workspace on this machine (usage limits are account-wide, not
per-project).

This is an ESTIMATE, not an authoritative usage figure:
- Anthropic does not expose an API for remaining quota on a Pro/Max
  subscription, so there is no ground truth to check this against.
- Pro/Max rate limits are computed server-side by a weighted formula, not a
  raw token sum, and cache-read tokens are billed far cheaper than fresh
  input tokens. Treat the numbers here as a relative trend, not a percentage
  of any real cap.

No third-party dependencies; stdlib only.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def iter_usage_events(root: Path):
    """Yield (date, model, usage_dict) for every assistant message with usage."""
    for jsonl_path in root.glob("**/*.jsonl"):
        try:
            with jsonl_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "assistant":
                        continue
                    message = obj.get("message") or {}
                    usage = message.get("usage")
                    ts = obj.get("timestamp")
                    if not usage or not ts:
                        continue
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    yield dt.date(), message.get("model", "unknown"), usage
        except OSError:
            continue


def aggregate(root: Path, since: datetime.date):
    by_day = defaultdict(lambda: defaultdict(int))
    by_day_model = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for date, model, usage in iter_usage_events(root):
        if date < since:
            continue
        for key in ("input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens"):
            v = usage.get(key, 0) or 0
            by_day[date][key] += v
            by_day_model[date][model][key] += v
    return by_day, by_day_model


def fmt(n: int) -> str:
    return f"{n:,}"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--range", choices=["today", "week", "month", "all"],
                         default="week", help="how far back to report (default: week)")
    parser.add_argument("--by-model", action="store_true",
                         help="break totals down by model")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    since = {
        "today": today,
        "week": today - timedelta(days=today.weekday()),   # Monday this week
        "month": today.replace(day=1),
        "all": datetime(2000, 1, 1).date(),
    }[args.range]

    if not CLAUDE_PROJECTS_DIR.exists():
        print(f"No Claude Code transcript directory found at {CLAUDE_PROJECTS_DIR}")
        return

    by_day, by_day_model = aggregate(CLAUDE_PROJECTS_DIR, since)

    if not by_day:
        print(f"No usage found for range '{args.range}' (since {since}).")
        return

    print(f"Token usage since {since} (range: {args.range})")
    print(f"Source: {CLAUDE_PROJECTS_DIR} (all workspaces on this machine)\n")

    total = defaultdict(int)
    for date in sorted(by_day):
        d = by_day[date]
        fresh_in = d["input_tokens"] + d["cache_creation_input_tokens"]
        cache_read = d["cache_read_input_tokens"]
        out = d["output_tokens"]
        print(f"  {date}  in(fresh)={fmt(fresh_in):>10}  in(cache-read)={fmt(cache_read):>12}  out={fmt(out):>9}")
        if args.by_model:
            for model, m in sorted(by_day_model[date].items()):
                mf = m["input_tokens"] + m["cache_creation_input_tokens"]
                print(f"      {model:<24} in(fresh)={fmt(mf):>10}  in(cache-read)={fmt(m['cache_read_input_tokens']):>12}  out={fmt(m['output_tokens']):>9}")
        for k, v in d.items():
            total[k] += v

    fresh_total = total["input_tokens"] + total["cache_creation_input_tokens"]
    print(f"\nTOTAL   in(fresh)={fmt(fresh_total)}  in(cache-read)={fmt(total['cache_read_input_tokens'])}  out={fmt(total['output_tokens'])}")
    print("\n(fresh input = tokens actually billed at full rate; cache-read input is\n"
          "heavily discounted. This is a relative trend estimate, not a % of your\n"
          "actual Pro/Max quota - no API exposes that number.)")


if __name__ == "__main__":
    main()
