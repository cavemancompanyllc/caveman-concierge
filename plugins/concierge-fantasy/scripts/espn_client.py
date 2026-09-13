"""
ESPN Fantasy Football client -- read-only.

Covers an ESPN league (ESPN_LEAGUE_ID / ESPN_LEAGUE_NAME in .env), usable
alongside Sleeper leagues. New external integration -- per this workspace's
own CLAUDE.md conventions, a new integration like this typically needs
explicit sign-off before being treated as a permanent part of the toolchain.

ESPN has no public/documented fantasy API. This uses the same
undocumented-but-widely-used v3 endpoint the ESPN Fantasy web app itself
calls, authenticated via the espn_s2/SWID cookies of a logged-in session
(read from .env -- ESPN_S2, ESPN_SWID). Two things learned by hitting it
live (2026-09-08) that are not obvious from any doc:

- The read host is lm-api-reads.fantasy.espn.com, not fantasy.espn.com --
  the latter silently 200s with an HTML page (not JSON, not a 4xx) when
  hit with the same path/cookies, which looks like a working request
  until content-type is checked. Always use lm-api-reads for GETs.
- SWID must be sent with its literal surrounding curly braces
  (e.g. {F7362126-...}), matching exactly what ESPN's own cookie value
  looks like.

No POST/PUT/DELETE calls exist in this module and none should ever be
added here -- this league is read-only tracking, same DraftKings-style
"never write back to the vendor" boundary as the rest of this project.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import httpx

ROOT = Path(os.environ.get("CONCIERGE_HOME") or Path.cwd())
ENV_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "fantasy.db"

READ_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
REQUEST_TIMEOUT = 30.0


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

ESPN_S2 = os.environ.get("ESPN_S2", "")
ESPN_SWID = os.environ.get("ESPN_SWID", "")
ESPN_LEAGUE_ID = os.environ.get("ESPN_LEAGUE_ID", "")
ESPN_TEAM_ID = os.environ.get("ESPN_TEAM_ID", "")
ESPN_SEASON_ID = os.environ.get("ESPN_SEASON_ID", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS espn_leagues (
    league_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    name TEXT,
    size INTEGER,
    scoring_settings TEXT,
    roster_settings TEXT,
    draft_settings TEXT,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (league_id, season)
);

CREATE TABLE IF NOT EXISTS espn_rosters (
    league_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    owner_swid TEXT,
    location TEXT,
    nickname TEXT,
    abbrev TEXT,
    player_ids TEXT,
    wins INTEGER,
    losses INTEGER,
    ties INTEGER,
    points_for REAL,
    points_against REAL,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (league_id, season, team_id)
);

CREATE TABLE IF NOT EXISTS espn_draft_picks (
    league_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    overall_pick_number INTEGER NOT NULL,
    round_id INTEGER,
    round_pick_number INTEGER,
    team_id INTEGER,
    player_id INTEGER,
    bid_amount INTEGER,
    keeper INTEGER,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (league_id, season, overall_pick_number)
);
"""


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def _cookie_header() -> str:
    if not ESPN_S2 or not ESPN_SWID:
        raise RuntimeError("ESPN_S2 / ESPN_SWID not set in .env")
    return "espn_s2=" + ESPN_S2 + "; SWID=" + ESPN_SWID


def espn_get(league_id, season, views):
    """GET the league object with one or more view= params. Always hits
    lm-api-reads (see module docstring) with the cookie auth header."""
    url = READ_BASE + "/seasons/" + str(season) + "/segments/0/leagues/" + str(league_id)
    params = [("view", v) for v in views]
    resp = httpx.get(
        url,
        params=params,
        headers={
            "Cookie": _cookie_header(),
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "")
    if "json" not in ct:
        raise RuntimeError(
            "ESPN returned non-JSON content-type " + repr(ct) + " -- likely an "
            "auth/host problem (check ESPN_S2/ESPN_SWID are current and "
            "READ_BASE is lm-api-reads, not fantasy.espn.com)"
        )
    return resp.json()


def get_draft_detail(league_id, season) -> dict:
    """draftDetail block: drafted (bool), inProgress (bool), and a picks[]
    array pre-sized to the full draft (playerId == -1 for not-yet-made
    picks). Same shape draft-live's Sleeper poller consumes -- diff the
    picks[] array on repeated calls (playerId != -1) for pick-by-pick
    signal during a live draft."""
    data = espn_get(league_id, season, ["mDraftDetail"])
    return data.get("draftDetail", {})


def get_teams(league_id, season) -> list:
    data = espn_get(league_id, season, ["mTeam"])
    return data.get("teams", [])


def get_settings(league_id, season) -> dict:
    data = espn_get(league_id, season, ["mSettings"])
    return data.get("settings", {})


def get_status(league_id, season) -> dict:
    data = espn_get(league_id, season, ["mStatus"])
    return data.get("status", {})


def get_rosters(league_id, season) -> list:
    """mRoster view -- each team's current entries (rostered players)."""
    data = espn_get(league_id, season, ["mRoster", "mTeam"])
    return data.get("teams", [])


def _json(obj):
    return json.dumps(obj) if obj is not None else None


def sync_league(league_id=None, season=None) -> None:
    """Pull league settings + team rosters into espn_leagues/espn_rosters.
    Safe to re-run; upserts. Mirrors sync-sleeper's shape/intent for this
    league but is not yet wired into fantasy_index.py's CLI."""
    league_id = league_id or ESPN_LEAGUE_ID
    season = season or ESPN_SEASON_ID
    if not league_id or not season:
        raise RuntimeError("ESPN_LEAGUE_ID / ESPN_SEASON_ID not set in .env")

    settings = get_settings(league_id, season)
    teams = get_rosters(league_id, season)

    conn = get_db()
    conn.execute(
        "INSERT INTO espn_leagues (league_id, season, name, size, scoring_settings, "
        "roster_settings, draft_settings, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
        "ON CONFLICT (league_id, season) DO UPDATE SET "
        "name=excluded.name, size=excluded.size, "
        "scoring_settings=excluded.scoring_settings, "
        "roster_settings=excluded.roster_settings, "
        "draft_settings=excluded.draft_settings, synced_at=excluded.synced_at",
        (
            str(league_id),
            int(season),
            settings.get("name"),
            settings.get("size"),
            _json(settings.get("scoringSettings")),
            _json(settings.get("rosterSettings")),
            _json(settings.get("draftSettings")),
        ),
    )

    for t in teams:
        owners = t.get("owners") or []
        entries = (t.get("roster") or {}).get("entries") or []
        player_ids = [e.get("playerId") for e in entries if e.get("playerId")]
        record = (t.get("record") or {}).get("overall") or {}
        conn.execute(
            "INSERT INTO espn_rosters (league_id, season, team_id, owner_swid, "
            "location, nickname, abbrev, player_ids, wins, losses, ties, "
            "points_for, points_against, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT (league_id, season, team_id) DO UPDATE SET "
            "owner_swid=excluded.owner_swid, location=excluded.location, "
            "nickname=excluded.nickname, abbrev=excluded.abbrev, "
            "player_ids=excluded.player_ids, wins=excluded.wins, "
            "losses=excluded.losses, ties=excluded.ties, "
            "points_for=excluded.points_for, points_against=excluded.points_against, "
            "synced_at=excluded.synced_at",
            (
                str(league_id),
                int(season),
                t.get("id"),
                (owners[0] if owners else None),
                t.get("location"),
                t.get("nickname"),
                t.get("abbrev"),
                _json(player_ids),
                record.get("wins"),
                record.get("losses"),
                record.get("ties"),
                record.get("pointsFor"),
                record.get("pointsAgainst"),
            ),
        )
    conn.commit()
    conn.close()


def sync_draft_picks(league_id=None, season=None) -> int:
    """Upsert the full picks[] array (including not-yet-made slots) into
    espn_draft_picks. Returns count of picks actually made (player_id != -1)."""
    league_id = league_id or ESPN_LEAGUE_ID
    season = season or ESPN_SEASON_ID
    dd = get_draft_detail(league_id, season)
    picks = dd.get("picks", [])
    conn = get_db()
    made = 0
    for p in picks:
        pid = p.get("playerId", -1)
        if pid != -1:
            made += 1
        conn.execute(
            "INSERT INTO espn_draft_picks (league_id, season, overall_pick_number, "
            "round_id, round_pick_number, team_id, player_id, bid_amount, keeper, "
            "synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT (league_id, season, overall_pick_number) DO UPDATE SET "
            "round_id=excluded.round_id, round_pick_number=excluded.round_pick_number, "
            "team_id=excluded.team_id, player_id=excluded.player_id, "
            "bid_amount=excluded.bid_amount, keeper=excluded.keeper, "
            "synced_at=excluded.synced_at",
            (
                str(league_id),
                int(season),
                p.get("overallPickNumber"),
                p.get("roundId"),
                p.get("roundPickNumber"),
                p.get("teamId"),
                pid,
                p.get("bidAmount"),
                1 if p.get("keeper") else 0,
            ),
        )
    conn.commit()
    conn.close()
    return made


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ESPN fantasy read-only client (manual test/sync entrypoint)")
    parser.add_argument("action", choices=["draft-status", "sync-league", "poll-draft"])
    args = parser.parse_args()

    if args.action == "draft-status":
        dd = get_draft_detail(ESPN_LEAGUE_ID, ESPN_SEASON_ID)
        made = sum(1 for p in dd.get("picks", []) if p.get("playerId", -1) != -1)
        total = len(dd.get("picks", []))
        print("drafted=" + str(dd.get("drafted")) + " inProgress=" + str(dd.get("inProgress")) +
              " picks_made=" + str(made) + "/" + str(total))
    elif args.action == "sync-league":
        sync_league()
        print("espn_leagues / espn_rosters synced.")
    elif args.action == "poll-draft":
        made = sync_draft_picks()
        print("espn_draft_picks upserted. picks_made=" + str(made))
