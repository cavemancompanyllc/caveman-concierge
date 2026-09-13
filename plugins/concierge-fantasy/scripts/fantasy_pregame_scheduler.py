"""
Fantasy pregame monitor -- weekly scan.

the user gave standing authorization to auto-execute fantasy roster moves
all season, across every team they manage (this example: one ESPN league +
2 Sleeper leagues), as long as every move is logged. Adjust
ESPN_MANAGED_TEAM_IDS / SLEEPER_LEAGUE_IDS in .env for your own setup —
this script has no hardcoded team/league count.

This script is the deterministic half of that system: a weekly scan (meant
to run Tuesday night, after Monday Night Football closes out the prior
week) that finds every NFL kickoff time this week involving one of the user's
rostered STARTERS on any of the 3 teams, and schedules a one-time Windows
Scheduled Task per distinct kickoff to fire a headless Claude Code session
some buffer before kickoff. That headless session (not this script) does
the actual judgment call -- re-check injury/inactive/weather news, decide
whether a swap or waiver move is warranted, execute it, log it, and push a
notification. This script only does the mechanical part: figuring out
WHEN those sessions need to run and registering them.

Usage:
    python scripts/fantasy_pregame_scheduler.py weekly-scan [--buffer-min 60] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(os.environ.get("CONCIERGE_HOME") or Path.cwd())
ENV_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "fantasy.db"
PROMPTS_DIR = ROOT / "data" / "fantasy_pregame_prompts"
RUNNER_PS1 = ROOT / "scripts" / "run_headless_claude.ps1"

READ_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
SLEEPER_BASE = "https://api.sleeper.app/v1"
REQUEST_TIMEOUT = 30.0

# Standard ESPN proTeamId -> NFL team abbreviation map (confirmed live
# 2026-09-08 against known players -- see the ESPN Fantasy API Navigation
# Notes vault page for provenance).
ESPN_PRO_TEAM_MAP = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA",
    27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


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
ESPN_LEAGUE_NAME = os.environ.get("ESPN_LEAGUE_NAME", "ESPN League")
ESPN_TEAM_ID = os.environ.get("ESPN_TEAM_ID", "")
ESPN_SEASON_ID = os.environ.get("ESPN_SEASON_ID", "")
SLEEPER_LEAGUE_IDS = [x.strip() for x in os.environ.get("SLEEPER_LEAGUE_IDS", "").split(",") if x.strip()]


def _espn_managed_teams() -> dict[str, str]:
    """team_id -> display label, for every ESPN team the user manages (owner or
    co-manager) -- ESPN_MANAGED_TEAM_IDS is "id:label,id:label,...". Falls
    back to just ESPN_TEAM_ID (label "the user's team") when unset, so this is
    backward compatible with a .env that predates co-managing other family
    members' teams."""
    raw = os.environ.get("ESPN_MANAGED_TEAM_IDS", "")
    teams: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        team_id, _, label = entry.partition(":")
        teams[team_id.strip()] = label.strip() or team_id.strip()
    if not teams and ESPN_TEAM_ID:
        teams[ESPN_TEAM_ID] = "the user's team"
    return teams


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def espn_cookie_header() -> str:
    if not ESPN_S2 or not ESPN_SWID:
        raise RuntimeError("ESPN_S2 / ESPN_SWID not set in .env")
    return "espn_s2=" + ESPN_S2 + "; SWID=" + ESPN_SWID


def get_pro_team_schedules(season: str) -> dict[str, datetime]:
    """team abbrev -> this week's kickoff datetime (UTC). Season-level
    endpoint, no league_id in the path."""
    url = READ_BASE + "/seasons/" + str(season)
    resp = httpx.get(
        url,
        params=[("view", "proTeamSchedules")],
        headers={
            "Cookie": espn_cookie_header(),
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    now = datetime.now(timezone.utc)
    out: dict[str, datetime] = {}
    for team in data.get("settings", {}).get("proTeams", []):
        team_id = team.get("id")
        abbrev = ESPN_PRO_TEAM_MAP.get(team_id)
        if not abbrev:
            continue
        bye = team.get("byeWeek")
        games = []
        for week_games in (team.get("proGamesByScoringPeriod") or {}).values():
            games.extend(week_games)
        # Find the next game whose date is in the future and within 8 days --
        # that's "this week's" game regardless of exact scoring-period math.
        candidates = []
        for g in games:
            ts = g.get("date")
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            if now <= dt <= now + timedelta(days=8):
                candidates.append(dt)
        if candidates:
            out[abbrev] = min(candidates)
    return out


ESPN_POSITION_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
BENCH_SLOTS = {20, 21}  # BENCH, IR


def get_espn_roster_full() -> list[dict]:
    """Every rostered player on every ESPN team the user manages (see
    _espn_managed_teams -- their own team plus any others they co-manage in
    the same league), bench included -- each
    a dict with name/team/position/league_label/is_starter. One league API
    call returns every team's roster already, so covering multiple managed
    teams costs nothing extra -- just don't filter down to a single team_id.
    Confirmed live 2026-09-09: defaultPositionId 1=QB,2=RB,3=WR,4=TE,5=K,16=D/ST."""
    managed = _espn_managed_teams()
    if not (ESPN_LEAGUE_ID and managed and ESPN_SEASON_ID):
        return []
    url = READ_BASE + "/seasons/" + str(ESPN_SEASON_ID) + "/segments/0/leagues/" + str(ESPN_LEAGUE_ID)
    resp = httpx.get(
        url,
        params=[("view", "mRoster"), ("view", "mTeam")],
        headers={
            "Cookie": espn_cookie_header(),
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for team in data.get("teams", []):
        team_id = str(team.get("id"))
        if team_id not in managed:
            continue
        label = managed[team_id]
        for entry in (team.get("roster") or {}).get("entries") or []:
            player = ((entry.get("playerPoolEntry") or {}).get("player") or {})
            pro_team = ESPN_PRO_TEAM_MAP.get(player.get("proTeamId"))
            position = ESPN_POSITION_MAP.get(player.get("defaultPositionId"))
            if player.get("fullName") and pro_team:
                out.append({
                    "name": player["fullName"],
                    "team": pro_team,
                    "position": position,
                    "league": f"ESPN: {ESPN_LEAGUE_NAME} ({label})",
                    "is_starter": entry.get("lineupSlotId") not in BENCH_SLOTS,
                })
    return out


def get_espn_starters() -> list[str]:
    """Player full names currently in a starting (non-bench, non-IR) slot
    across every ESPN team the user manages."""
    return [(p["name"], p["team"], p["league"]) for p in get_espn_roster_full() if p["is_starter"]]


def get_sleeper_roster_full() -> list[dict]:
    """Every rostered player (starters + bench) across both Sleeper
    leagues, for the user's own roster in each -- each a dict with
    name/team/position/league_label/is_starter."""
    if not SLEEPER_LEAGUE_IDS:
        return []
    conn = get_db()
    out = []
    for league_id in SLEEPER_LEAGUE_IDS:
        league = conn.execute(
            "SELECT name FROM sleeper_leagues WHERE league_id = ?", (league_id,)
        ).fetchone()
        league_name = league["name"] if league else league_id
        mike_user_id = os.environ.get("SLEEPER_USER_ID", "")
        rows = conn.execute(
            "SELECT roster_id, owner_id, owner_name, player_ids, starters FROM sleeper_rosters WHERE league_id = ?",
            (league_id,),
        ).fetchall()
        target = None
        if mike_user_id:
            target = next((r for r in rows if r["owner_id"] == mike_user_id), None)
        if target is None:
            print(f"WARNING: could not identify the user's roster in Sleeper league {league_id} "
                  f"({league_name}) -- set SLEEPER_USER_ID in .env. Skipping this league.",
                  file=sys.stderr)
            continue
        starter_ids = set(json.loads(target["starters"] or "[]"))
        all_ids = json.loads(target["player_ids"] or "[]")
        for pid in all_ids:
            if not pid or pid == "0":
                continue
            prow = conn.execute(
                "SELECT full_name, team, position FROM players WHERE player_id = ?", (pid,)
            ).fetchone()
            if prow and prow["full_name"] and prow["team"]:
                out.append({
                    "name": prow["full_name"],
                    "team": prow["team"],
                    "position": prow["position"],
                    "league": f"Sleeper: {league_name}",
                    "is_starter": pid in starter_ids,
                })
    conn.close()
    return out


def get_sleeper_starters() -> list[tuple[str, str, str]]:
    """(player_name, nfl_team_abbrev, league_label) for every starter slot."""
    return [(p["name"], p["team"], p["league"]) for p in get_sleeper_roster_full() if p["is_starter"]]


def build_slots(buffer_min: int) -> dict[datetime, list[dict]]:
    """kickoff-minus-buffer trigger time -> list of player dicts (each with
    name/team/position/league/is_starter, plus later_bench_alternatives --
    same-position bench players on the same roster whose OWN kickoff is
    later this week. That list matters because individual-game locktimes
    mean a starter's slot locks at THAT starter's kickoff regardless of
    what a later-playing bench alternative does afterward -- so the real
    decision deadline for 'should I start the bench guy instead' is the
    CURRENTLY-STARTING player's kickoff, not the bench player's. See
    Claude memory: this exact scenario (Mahomes benched behind Dart,
    Dart's Sunday kickoff being the real deadline for a Monday-playing
    Mahomes decision) is why this field exists, 2026-09-09."""
    season = ESPN_SEASON_ID or str(datetime.now().year)
    team_kickoffs = get_pro_team_schedules(season)
    full_roster = get_espn_roster_full() + get_sleeper_roster_full()

    def kickoff_of(p):
        return team_kickoffs.get(p["team"])

    slots: dict[datetime, list[dict]] = {}
    for p in full_roster:
        if not p["is_starter"]:
            continue
        kickoff = kickoff_of(p)
        if not kickoff:
            continue
        later_bench = [
            b for b in full_roster
            if not b["is_starter"] and b["league"] == p["league"] and b["position"] == p["position"]
            and kickoff_of(b) and kickoff_of(b) > kickoff
        ]
        entry = dict(p)
        entry["later_bench_alternatives"] = later_bench
        trigger = kickoff - timedelta(minutes=buffer_min)
        slots.setdefault(trigger, []).append(entry)
    return slots


def make_prompt(trigger_time: datetime, players: list[dict]) -> str:
    lines = "\n".join(f"- {p['name']} ({p['team']}) -- {p['league']}" for p in players)
    lock_warnings = []
    for p in players:
        later = p.get("later_bench_alternatives") or []
        if later:
            names = ", ".join(f"{b['name']} ({b['team']}, plays later this week)" for b in later)
            lock_warnings.append(
                f"- {p['name']}'s slot ({p['league']}) locks at THIS kickoff. Same-position bench "
                f"option(s) playing later this week: {names}. If one of them is clearly the better "
                f"play, this is the LAST chance to swap before the slot locks with {p['name']} in it "
                f"for the week -- a later-playing bench player going off tomorrow won't help if this "
                f"decision isn't made now."
            )
    lock_section = ""
    if lock_warnings:
        lock_section = "\n\nLOCK-DEADLINE WARNINGS (check these before anything else):\n" + "\n".join(lock_warnings)
    return f"""You are running as a scheduled, unattended pregame check for the user's fantasy football rosters. This fires automatically {trigger_time.isoformat()} (UTC), about to precede kickoff for a game involving these rostered STARTERS:

{lines}{lock_section}

the user has given standing authorization to auto-execute fantasy roster moves this season across every team they manage -- do NOT ask for approval, just act and log.

Do the following:
1. For each player above, check current injury/inactive status and any breaking news (ESPN's live player card / injury API for the ESPN player, Sleeper's players/injuries data or a web search for the Sleeper players). Check weather conditions for outdoor-stadium games if relevant to kicking/passing-heavy players.
2. Decide if anything found is actionable -- a player has been ruled OUT/Doubtful, is inactive, or conditions clearly warrant a swap (e.g. severe wind for a kicker). A bare "Questionable" tag alone, unchanged from earlier in the week, is NOT actionable on its own -- that's normal noise (see reference_espn_roster_move_ui and this week's Week 1 vault notes for how that was handled earlier this week).
3. If action is warranted: pick the best available bench-eligible replacement on the SAME team's roster (same platform), execute the swap. Check Fantasy Football/Weekly Lineup Strategy.md in the vault first (ask the obsidian subagent to read it) for which bench slot to prefer when more than one replacement is plausible -- e.g. a later-kickoff option over an early one, per that page's swing-spot rules. For ESPN, use Playwright browser automation against fantasy.espn.com following the pattern documented in Claude's memory reference_espn_roster_move_ui.md (use the players/add Claim/Drop or lineup-edit flow, verify the staged state before confirming -- do not trust the toolbar "IR" bulk panel blindly). For Sleeper, use Playwright browser automation against sleeper.com following the pattern in Claude's memory reference_sleeper_roster_move_ui.md (PLAYERS tab, locate the row via browser_evaluate since the add button has no accessible name, pick the drop target in the resulting dialog, confirm) -- confirmed working for bench add/drop as of 2026-09-09; a starter-slot swap specifically hasn't been tested yet, so verify the result against the team page or league chat before trusting it.
4. Log exactly what you found and did (or didn't do and why) to the relevant team's Obsidian note under Fantasy Football/ -- each player line above is tagged with its league/team, e.g. "ESPN: <League Name> (<Team Label> (the user))" vs "...(<Other Team Name> (<family member>))" -- match that to the right page: for ESPN, log to Fantasy Football/<League Name>/Week N.md (shared weekly activity log for every co-managed team in that league, clearly labeling which team each entry is about) AND note anything actionable on a co-managed team is still the user's call to execute as their co-manager, same standing authorization, but flag in the log entry that it's that person's team, not the user's own. For Sleeper, use or create an equivalent page structure under Fantasy Football/ for that league.
5. Send a PushNotification with a one-line summary of what happened (or that nothing was actionable, only if you made ANY roster change -- do not notify for a "checked, nothing to do" outcome unless something borderline was worth a human look).

Keep this efficient -- this is a scheduled unattended run. Be decisive per the standing authorization, but log your reasoning."""


def schedule_windows_task(name: str, run_at: datetime, prompt_path: Path, dry_run: bool) -> None:
    local_dt = run_at.astimezone()  # local tz for schtasks
    date_str = local_dt.strftime("%m/%d/%Y")
    time_str = local_dt.strftime("%H:%M")
    cmd = [
        "schtasks", "/create", "/tn", name, "/sc", "once",
        "/sd", date_str, "/st", time_str, "/f",
        "/tr", f'powershell -NoProfile -ExecutionPolicy Bypass -File "{RUNNER_PS1}" -PromptFile "{prompt_path}"',
    ]
    print(f"{'[DRY RUN] Would schedule' if dry_run else 'Scheduling'}: {name} at {local_dt.isoformat()}")
    if not dry_run:
        subprocess.run(cmd, check=True)


def cmd_weekly_scan(buffer_min: int, dry_run: bool) -> None:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    slots = build_slots(buffer_min)
    now = datetime.now(timezone.utc)
    if not slots:
        print("No rostered starters found in any upcoming kickoff window -- nothing scheduled.")
        return
    for trigger_time, players in sorted(slots.items()):
        if trigger_time <= now:
            print(f"SKIP (already past): {trigger_time.isoformat()} -- {len(players)} player(s)")
            continue
        prompt_text = make_prompt(trigger_time, players)
        slug = trigger_time.strftime("%Y%m%dT%H%M")
        prompt_path = PROMPTS_DIR / f"{slug}.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        task_name = f"Jarvis-Fantasy-Pregame-{slug}"
        schedule_windows_task(task_name, trigger_time, prompt_path, dry_run)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("weekly-scan")
    scan.add_argument("--buffer-min", type=int, default=60)
    scan.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.command == "weekly-scan":
        cmd_weekly_scan(args.buffer_min, args.dry_run)


if __name__ == "__main__":
    main()
