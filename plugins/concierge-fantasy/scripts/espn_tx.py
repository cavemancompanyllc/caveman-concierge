"""ESPN fantasy roster reads and writes over ESPN's own league API, no browser.

Uses this plugin's espn_client for cookie auth (ESPN_S2 / ESPN_SWID) and reads the
league from ESPN_LEAGUE_ID (and optionally ESPN_SEASON_ID) in the workspace .env.

Exists because a browser profile periodically loses its ESPN login, which blocks
every UI-driven move; these endpoints only need the league cookies.

Usage (from a workspace; never call the plugin cache path directly):
  python -m caveman_core run concierge-fantasy espn_tx.py roster --team 9
  python -m caveman_core run concierge-fantasy espn_tx.py free-agents --pos D/ST [--week 5] [--limit 15]
  python -m caveman_core run concierge-fantasy espn_tx.py claim --team 9 --add 4870795 [--drop 3116165]
  python -m caveman_core run concierge-fantasy espn_tx.py add   --team 9 --add -16006 [--drop -16012]
  python -m caveman_core run concierge-fantasy espn_tx.py move  --team 9 --player 4596334 --from BN --to RB [--player2 ID --from2 RB --to2 BN]

`claim` = waiver claim (costs priority). `add` = free-agent add (zero cost, only
valid for players with status FREEAGENT). There is no dry-run mode -- ESPN rejects
executionType VALIDATE -- so every write call executes.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling espn_client in this plugin
import espn_client as E  # noqa: E402
import httpx  # noqa: E402

_league = os.environ.get("ESPN_LEAGUE_ID") or getattr(E, "ESPN_LEAGUE_ID", "")
if not _league:
    sys.exit("ESPN_LEAGUE_ID is not set - add it to the workspace .env")
LEAGUE = int(_league)
# NFL seasons are named for the year they start; Jan-Feb games belong to last year's season
_today = date.today()
SEASON = int(os.environ.get("ESPN_SEASON_ID") or (_today.year if _today.month >= 3 else _today.year - 1))
WRITE_URL = ("https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/"
             f"{SEASON}/segments/0/leagues/{LEAGUE}/transactions/")
SLOT = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "D/ST": 16, "K": 17, "BN": 20, "IR": 21, "FLEX": 23}
SLOT_NAME = {v: k for k, v in SLOT.items()}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}


def _headers(extra=None):
    h = {"Cookie": E._cookie_header(), "User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    h.update(extra or {})
    return h


def _current_week():
    return E.espn_get(LEAGUE, SEASON, ["mStatus"])["status"]["latestScoringPeriod"]


def _proj(player, week):
    for s in player.get("stats", []):
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == 1:
            return round(s.get("appliedTotal", 0), 1)
    return None


def cmd_roster(a):
    week = a.week or _current_week()
    d = E.espn_get(LEAGUE, SEASON, ["mRoster", "mTeam"])
    for t in d["teams"]:
        if t["id"] != a.team:
            continue
        print(f"team {t['id']} {t.get('name')} waiverRank={t.get('waiverRank')} week={week}")
        for e in t["roster"]["entries"]:
            p = e["playerPoolEntry"]["player"]
            print(f"  {SLOT_NAME.get(e['lineupSlotId'], e['lineupSlotId']):5} "
                  f"{POS.get(p['defaultPositionId'], p['defaultPositionId']):4} {p['fullName']:26} "
                  f"proTeam={p['proTeamId']:<3} {p.get('injuryStatus', '') or '':15} "
                  f"proj={_proj(p, week)} id={p['id']}")


def cmd_free_agents(a):
    week = a.week or _current_week()
    flt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
                       "filterSlotIds": {"value": [SLOT[a.pos]]}, "limit": 50,
                       "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    url = E.READ_BASE + f"/seasons/{SEASON}/segments/0/leagues/{LEAGUE}"
    r = httpx.get(url, params={"view": "kona_player_info", "scoringPeriodId": week},
                  headers=_headers({"x-fantasy-filter": json.dumps(flt)}), timeout=30)
    r.raise_for_status()
    rows = []
    for p in r.json()["players"]:
        pl = p["player"]
        rows.append((_proj(pl, week) or 0, pl["fullName"], pl["proTeamId"], p.get("status"),
                     pl.get("injuryStatus", "") or "", pl["id"]))
    rows.sort(reverse=True)
    print(f"{a.pos} pool week {week} (status FREEAGENT = zero-cost add; WAIVERS = claim only)")
    for x in rows[:a.limit]:
        print(f"  {x[0]:5} {x[1]:26} proTeam={x[2]:<3} {x[3]:9} {x[4]:14} id={x[5]}")


def _post(team, typ, items):
    body = {"isLeagueManager": False, "teamId": team, "type": typ, "memberId": E.ESPN_SWID,
            "scoringPeriodId": _current_week(), "executionType": "EXECUTE", "items": items}
    if typ == "WAIVER":
        body["bidAmount"] = 0
    r = httpx.post(WRITE_URL, json=body, timeout=30, headers=_headers({
        "Content-Type": "application/json", "Origin": "https://fantasy.espn.com",
        "Referer": "https://fantasy.espn.com/"}))
    try:
        j = r.json()
    except ValueError:
        print(r.status_code, r.text[:500])
        sys.exit(1)
    if isinstance(j, dict):
        j.pop("memberId", None)
    print(r.status_code, j.get("status") if isinstance(j, dict) else "", json.dumps(j)[:800])
    if r.status_code >= 300:
        sys.exit(1)


def _add_items(a):
    items = [{"playerId": a.add, "type": "ADD", "toTeamId": a.team}]
    if a.drop is not None:
        items.append({"playerId": a.drop, "type": "DROP", "fromTeamId": a.team})
    return items


def cmd_claim(a):
    _post(a.team, "WAIVER", _add_items(a))


def cmd_add(a):
    _post(a.team, "FREEAGENT", _add_items(a))


def cmd_move(a):
    items = [{"playerId": a.player, "type": "LINEUP",
              "fromLineupSlotId": SLOT[a.from_], "toLineupSlotId": SLOT[a.to]}]
    if a.player2 is not None:
        items.append({"playerId": a.player2, "type": "LINEUP",
                      "fromLineupSlotId": SLOT[a.from2], "toLineupSlotId": SLOT[a.to2]})
    _post(a.team, "ROSTER", items)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("roster"); s.add_argument("--team", type=int, required=True); s.add_argument("--week", type=int)
    s.set_defaults(fn=cmd_roster)
    s = sub.add_parser("free-agents"); s.add_argument("--pos", choices=list(POS.values()), required=True)
    s.add_argument("--week", type=int); s.add_argument("--limit", type=int, default=15); s.set_defaults(fn=cmd_free_agents)
    for name, fn in (("claim", cmd_claim), ("add", cmd_add)):
        s = sub.add_parser(name); s.add_argument("--team", type=int, required=True)
        s.add_argument("--add", type=int, required=True); s.add_argument("--drop", type=int); s.set_defaults(fn=fn)
    s = sub.add_parser("move"); s.add_argument("--team", type=int, required=True)
    s.add_argument("--player", type=int, required=True)
    s.add_argument("--from", dest="from_", choices=list(SLOT), required=True)
    s.add_argument("--to", choices=list(SLOT), required=True)
    s.add_argument("--player2", type=int); s.add_argument("--from2", choices=list(SLOT)); s.add_argument("--to2", choices=list(SLOT))
    s.set_defaults(fn=cmd_move)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
