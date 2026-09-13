"""Pull Sleeper league data (and later DK salaries/stats/projections) into a
local SQLite store for the user's fantasy football agent.

Sleeper's API is fully open (no auth, no key) — see docs.sleeper.com.
DraftKings is read-only in this tool by design: salary/slate data only,
never lineup submission (DK's Terms of Use prohibit automated lineup entry).

Usage:
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-players [--force]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-sleeper [--league-id ID] [--week N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-stats [--season N] [--week N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" project --week N [--season N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-odds --week N [--season N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-weather --week N [--season N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" optimize-sleeper --league-id ID --week N [--season N] [--roster-id N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" report --week N [--season N] [--league-id ID] [--model TAG] [--force]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-dk --file PATH --season N --week N
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" optimize-dk --season N --week N [--contest-type cash|gpp] [--stack|--no-stack]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-draft-history --league-id ID --season N [--roster-id N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-rankings --season N [--force]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" sync-weekly-rankings --season N --week N [--force]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" blend-projections --season N --week N [--force]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" build-draft-pool --league-id ID --season N [--prior-season N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" draft-live --league-id ID [--roster-id N] [--poll-seconds N] [--watch]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" draft-strategy --league-id ID --season N [--model TAG] [--force]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" dst-schedule-strength --season N [--as-of-week N] [--window N] [--strength-season N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" upside-targets --league-id ID --season N [--prior-season N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" roster-grade --league-id ID --roster-id N [--season N]
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" roster-grade --league-id ID --draft-id ID --my-slot N [--season N]  # mock draftboard
    python "${CLAUDE_PLUGIN_ROOT}/scripts/fantasy_index.py" export-xlsx --league-id ID --season N [--out PATH]

Phase 1 scope: Sleeper rosters/matchups/transactions/players.
Phase 2 scope: sync-stats (nflreadpy weekly stats + snap counts + official
injury reports) and project (in-house rolling-average heuristic). No ESPN
hidden-API call — nflreadpy's load_injuries already sources the official
weekly injury report, which is more stable than reverse-engineering ESPN's
undocumented endpoint for the same data; revisit only if live game-day
status (not just the weekly report) turns out to matter.
Phase 3 scope: optimize-sleeper (LP-based start/sit via PuLP, maximizing
projected points against each league's actual roster_positions slots) and
report (local Ollama write-up of the optimizer output + injury context,
printed as markdown for the obsidian subagent to file — this script never
touches the vault itself).
Phase 4 scope: sync-dk (parses a DK Classic salary CSV the user exports by hand
from the DraftKings site — no DK API call of any kind, DK's Terms of Use
prohibit automated access) and optimize-dk (hand-rolled PuLP ILP: maximize
projected points under the $50,000 salary cap and 9-slot Classic roster,
same pattern as optimize-sleeper's LP but with a salary constraint added and
an optional QB+same-team-WR/TE stacking constraint for GPP play). Considered
the `pydfs-lineup-optimizer` package first but it hard-pins PuLP==2.4,
conflicting with the pulp>=3.0 already used by optimize-sleeper — hand-rolling
avoided the dependency fight and kept both optimizers on the same PuLP
version. Analysis only — never submits, edits, or withdraws a DK lineup.
Phase 5 scope: live draft assistance. sync-rankings pulls FantasyPros
dynasty-overall consensus ECR (via nflreadpy's load_ff_rankings, cross-
walked to Sleeper player_id via load_ff_playerids — both already-used
nflreadpy tables, no new dependency). build-draft-pool turns that ECR into
a value-over-replacement (VBD) board using this league's own real
scoring_settings and roster_positions (previously synced but never read
back — see score_stats below), excluding declared keepers. draft-live
polls Sleeper's own /draft/{id}/picks on an interval, tracks every
roster's filled slots (not just the user's), and surfaces need-weighted
recommendations + positional-run signals when it's his turn — never an
auto-decided pick. draft-strategy narrates the computed tiers via local
Ollama (same narrate-don't-decide contract as `report`) for the obsidian
subagent to file pre-draft.
Phase 6 (deferred, not built): score_stats() is generic enough to replace
optimize-sleeper/optimize-dk's hardcoded K_FG_POINTS/_def_points-style
approximations with each league's real scoring_settings too, but that
touches in-season code paths with cached projections/reports rows — a
separate, riskier change than draft prep. Revisit only if the user asks.
Phase 7 scope: deeper draft-day research + a paper backup, all local-data-
only. dst-schedule-strength ranks DST by upcoming opposing-offense strength
(via nflreadpy's schedule + a weekly_stats-derived offense proxy), not raw
season-long defensive quality, and build-draft-pool applies it as a
secondary DST nudge (same pattern as the league scoring factor). compute_risk_score
(called from build-draft-pool, stored on draft_pool.risk_score/risk_note)
scores QB/RB/WR/TE 0-100 on volatility/injury-report frequency/snap-share
consistency/depth-chart competition, purely from already-synced local data —
draft-live's _print_recommendation uses it to prefer a safer option while a
starter need is still open, and only rewards a risky upside swing once that
need is already covered. upside-targets flags a local-data-derived mid/late-
round handcuff/rookie-role candidate pool (depth_chart_order + prior-season
injury history/age of the starter ahead) — explicitly not a stand-in for
real scouting/consensus content, which needs the research subagent's web
access this script doesn't have. _print_recommendation also now applies a
soft late-round value nudge toward a QB once a roster is down to its last
few picks with only one QB rostered (the "always draft 2 QBs" rule was
previously fantasy.md documentation only — decision #6 flagged the plain
need-boost as too weak to enforce it; this replaced an earlier hard
scarcity-driven override once real 2025-season data showed both leagues'
waiver wire never actually ran dry on streamable QBs, so the nudge is
framed as late-round value, not scarcity). export-xlsx dumps build-draft-
pool's board to a formatted, tier-color-banded .xlsx as the user's offline
draft-day backup. roster-grade is a separate, general-purpose, any-time
roster evaluator (usable on any roster in a league, not just the user's own,
for head-to-head prep) -- deliberately does NOT require build-draft-pool
to have been run, since it is an in-season tool, not a draft-day one. It
reuses _replacement_ranks' dedicated-slots-then-FLEX-pool walk against a
single roster's own player pool for ECR-based per-position letter grades,
then flags bench drop candidates and unrostered-in-this-league pickup
suggestions from the same local weekly_stats snap-share-trend/ECR signals
-- degrades gracefully (says so, does not fabricate) when rankings or
weekly_stats aren't synced yet for the target season.
Phase 8 scope: weekly matchup research, beyond the draft-day-only dynasty
ECR and the in-house heuristic alone. sync-weekly-rankings pulls two more
independent signals every week: FantasyPros' per-position weekly consensus
ECR (nflreadpy, same dependency as sync-rankings) and Sleeper's own weekly
projections (native player_id, no crosswalk, and the one source with real
numbers for a player project's rolling average can't yet see). ESPN's
weekly rankings/projections were considered too -- skipped for now since
espn_client has no projection endpoint yet (would need reverse-engineering
ESPN's kona_player_info filter headers, a separate change) and DraftSharks
is mostly paywalled; FantasyPros' consensus already blends 100+ analysts,
ESPN's included. blend-projections folds both into the same projections
row optimize-sleeper/report already read (simple-averages the point-valued
signals, attaches FantasyPros' rank as blend_note context since a rank
alone isn't a point value) -- run after project and sync-weekly-rankings
each week, before optimize-sleeper/report.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx
import nflreadpy as nfl
import pulp

ROOT = Path(os.environ.get("CONCIERGE_HOME") or Path.cwd())
ENV_PATH = ROOT / ".env"


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

SLEEPER_LEAGUE_IDS = [x.strip() for x in os.environ.get("SLEEPER_LEAGUE_IDS", "").split(",") if x.strip()]

DATA_DIR = ROOT / "data" / "fantasy"
DB_PATH = ROOT / "data" / "fantasy.db"
PLAYERS_CACHE_PATH = DATA_DIR / "players.json"

SLEEPER_BASE = "https://api.sleeper.app/v1"
REQUEST_TIMEOUT = 30.0
PLAYERS_CACHE_MAX_AGE = 24 * 3600  # Sleeper: don't pull the player map more than once/day

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    full_name TEXT,
    position TEXT,
    team TEXT,
    status TEXT,
    espn_id TEXT,
    injury_status TEXT,
    injury_body_part TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS sleeper_leagues (
    league_id TEXT PRIMARY KEY,
    name TEXT,
    season TEXT,
    roster_positions TEXT,
    scoring_settings TEXT,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS sleeper_rosters (
    league_id TEXT NOT NULL REFERENCES sleeper_leagues(league_id),
    roster_id INTEGER NOT NULL,
    owner_id TEXT,
    owner_name TEXT,
    player_ids TEXT,
    starters TEXT,
    wins INTEGER,
    losses INTEGER,
    ties INTEGER,
    fpts REAL,
    fpts_against REAL,
    PRIMARY KEY (league_id, roster_id)
);

CREATE TABLE IF NOT EXISTS sleeper_matchups (
    league_id TEXT NOT NULL,
    week INTEGER NOT NULL,
    roster_id INTEGER NOT NULL,
    matchup_id INTEGER,
    points REAL,
    starters TEXT,
    PRIMARY KEY (league_id, week, roster_id)
);

CREATE TABLE IF NOT EXISTS sleeper_transactions (
    league_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    type TEXT,
    week INTEGER,
    status TEXT,
    data TEXT,
    created TEXT,
    PRIMARY KEY (league_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS weekly_stats (
    player_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team TEXT,
    opponent_team TEXT,
    position TEXT,
    snap_pct REAL,
    targets REAL,
    carries REAL,
    receptions REAL,
    fantasy_points REAL,
    fantasy_points_ppr REAL,
    stats TEXT,
    PRIMARY KEY (player_id, season, week)
);

CREATE TABLE IF NOT EXISTS injuries (
    player_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    report_status TEXT,
    practice_status TEXT,
    injury TEXT,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (player_id, season, week)
);

CREATE TABLE IF NOT EXISTS projections (
    player_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    proj_points REAL,
    method TEXT,
    generated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (player_id, season, week)
);

CREATE TABLE IF NOT EXISTS reports (
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    league_id TEXT NOT NULL,
    content TEXT,
    generated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (season, week, league_id)
);

CREATE TABLE IF NOT EXISTS draft_history (
    league_id TEXT NOT NULL,
    roster_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    player_id TEXT,
    position TEXT,
    nfl_team TEXT,
    draft_round INTEGER,
    origin TEXT,
    keeper_eligible INTEGER,
    keeper_cost_round INTEGER,
    ineligible_reason TEXT,
    season INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (league_id, roster_id, player_name, season)
);

CREATE TABLE IF NOT EXISTS dk_salaries (
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    dk_player_id TEXT NOT NULL,
    name TEXT,
    position TEXT,
    roster_position TEXT,
    team TEXT,
    salary INTEGER,
    game_info TEXT,
    avg_points_per_game REAL,
    sleeper_id TEXT,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (season, week, dk_player_id)
);

CREATE TABLE IF NOT EXISTS dk_lineups (
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    contest_type TEXT NOT NULL,
    lineup_json TEXT,
    total_salary INTEGER,
    total_proj_points REAL,
    generated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (season, week, contest_type)
);

CREATE TABLE IF NOT EXISTS keeper_declarations (
    league_id TEXT NOT NULL,
    roster_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    player_id TEXT,
    keeper_cost_round INTEGER,
    season INTEGER NOT NULL,
    declared_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    source TEXT,
    PRIMARY KEY (league_id, roster_id, player_name, season)
);

CREATE TABLE IF NOT EXISTS rankings (
    season INTEGER NOT NULL,
    player_id TEXT,               -- sleeper player_id; NULL when unmatched (see cmd_sync_rankings)
    fp_id INTEGER NOT NULL,        -- FantasyPros id, kept for crosswalk audit
    full_name TEXT,
    position TEXT,
    team TEXT,
    ecr REAL,
    ecr_sd REAL,
    ecr_best INTEGER,
    ecr_worst INTEGER,
    bye INTEGER,
    ecr_type TEXT NOT NULL,        -- 'do' (dynasty-overall) today; column kept for 'ro' (redraft) later
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (season, fp_id, ecr_type)
);

CREATE TABLE IF NOT EXISTS draft_pool (
    league_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    position TEXT,
    ecr REAL,
    pos_rank INTEGER,
    replacement_rank INTEGER,      -- the replacement-level pos_rank used for this position, this league
    est_points REAL,               -- value_from_ecr(ecr), NOT projected fantasy points (see cmd_build_draft_pool)
    adj_value REAL,
    tier INTEGER,
    risk_score REAL,               -- 0-100, higher = riskier; see compute_risk_score. NULL for DST/K (not scored).
    risk_note TEXT,                -- short human-readable summary of which risk factors drove the score
    drafted INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (league_id, season, player_id)
);

CREATE TABLE IF NOT EXISTS draft_picks (
    draft_id TEXT NOT NULL,
    league_id TEXT NOT NULL,
    pick_no INTEGER NOT NULL,      -- Sleeper's true monotonic pick key, not player_id (traded picks etc.)
    round INTEGER,
    roster_id INTEGER,
    player_id TEXT,
    picked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (draft_id, pick_no)
);

CREATE TABLE IF NOT EXISTS dst_schedule_strength (
    team TEXT NOT NULL,
    season INTEGER NOT NULL,       -- season being drafted/streamed for (the schedule season)
    as_of_week INTEGER NOT NULL,   -- first week of the upcoming window being ranked
    window_weeks INTEGER NOT NULL, -- how many upcoming weeks were averaged
    avg_opp_off_ppg REAL,          -- avg upcoming opponent offensive strength (see _team_offense_strength)
    schedule_rank INTEGER,         -- 1 = easiest upcoming stretch of opposing offenses
    games INTEGER,                 -- games actually in the window (can be < window_weeks on a bye)
    opponents TEXT,                -- json list like ["wk1:KC", "wk2:DEN", ...]
    strength_season INTEGER,       -- season whose weekly_stats fed the opponent-offense proxy
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (team, season, as_of_week, window_weeks)
);

CREATE TABLE IF NOT EXISTS upside_targets (
    league_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    full_name TEXT,
    position TEXT,
    team TEXT,
    category TEXT,                 -- 'rookie-role' | 'injury-prone-starter-ahead' | 'aging-starter-ahead'
    reason TEXT,                   -- human-readable why
    signal_json TEXT,              -- structured signals behind the flag (depth_chart_order, ecr, etc.)
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (league_id, season, player_id)
);

CREATE TABLE IF NOT EXISTS game_odds (
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    home_team TEXT NOT NULL,       -- nflverse team_abbr
    away_team TEXT NOT NULL,
    commence_time TEXT,
    spread_home REAL,              -- home team's spread (negative = home favored), averaged across books
    total REAL,                    -- game total, averaged across books
    home_implied_total REAL,       -- (total - spread_home) / 2
    away_implied_total REAL,       -- (total + spread_home) / 2
    bookmaker_count INTEGER,       -- how many books' lines were averaged
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (season, week, home_team, away_team)
);

CREATE TABLE IF NOT EXISTS game_weather (
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team TEXT NOT NULL,            -- home team (venue) for this game
    is_dome INTEGER NOT NULL DEFAULT 0,  -- from nflreadpy schedule's roof field (dome/closed = 1)
    temp_f REAL,
    wind_mph REAL,
    precip_pct REAL,
    short_forecast TEXT,
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (season, week, team)
);

CREATE TABLE IF NOT EXISTS weekly_rankings (
    player_id TEXT,                -- sleeper player_id crosswalk; NULL when a fantasypros row has no match
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    source TEXT NOT NULL,          -- 'fantasypros' (weekly per-position ECR) or 'sleeper' (weekly proj)
    source_id TEXT NOT NULL,       -- the source's own stable id (fp_id, or sleeper player_id) -- always
                                    -- present even when player_id crosswalk fails, so re-syncing upserts
                                    -- cleanly instead of accumulating duplicate unmatched rows
    full_name TEXT,
    position TEXT,
    team TEXT,
    pos_rank INTEGER,              -- rank within position, this source (fantasypros only)
    ecr REAL,                      -- consensus expert rank, fractional (fantasypros only)
    proj_points REAL,              -- projected PPR points, this source (sleeper only)
    synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (season, week, source, source_id)
);
"""

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_REPORT_MODEL = "qwen3-coder:30b"

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
NWS_USER_AGENT = "Jarvis-Fantasy (personal use, contact: admin@cavemancompany.co)"

# Home stadium lat/lon per nflverse team_abbr, for NWS API grid lookups. Static
# (venues change rarely) -- dome/outdoors comes from nflreadpy's schedule
# 'roof' field instead of being hardcoded here, since that auto-updates.
STADIUM_COORDS: dict[str, tuple[float, float]] = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4008), "BAL": (39.2780, -76.6227),
    "BUF": (42.7738, -78.7870), "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167),
    "CIN": (39.0954, -84.5160), "CLE": (41.5061, -81.6995), "DAL": (32.7473, -97.0945),
    "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3239, -81.6373),
    "KC": (39.0489, -94.4839), "LV": (36.0909, -115.1833), "LAC": (33.9535, -118.3392),
    "LAR": (33.9535, -118.3392), "MIA": (25.9580, -80.2389), "MIN": (44.9736, -93.2575),
    "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158),
    "SEA": (47.5952, -122.3316), "SF": (37.4032, -121.9698), "TB": (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713), "WAS": (38.9078, -76.8645),
}

# nflreadpy's fantasy_points/fantasy_points_ppr columns don't score kicking
# or team defense at all (fg_made/pat_made are present as raw stats but
# excluded from the fantasy total; team defenses have no per-player row).
# These are a standard-scoring approximation applied uniformly across both
# leagues — same level of approximation the heuristic already accepts for
# skill positions (nflreadpy's fantasy_points_ppr is generic PPR, not each
# league's exact scoring_settings either). Values mirror Huddle Buddies'
# actual Sleeper scoring_settings, which are fairly standard defaults.
K_FG_POINTS = {"fg_made_0_19": 3, "fg_made_20_29": 3, "fg_made_30_39": 3,
               "fg_made_40_49": 4, "fg_made_50_59": 5, "fg_made_60_": 5}
K_FG_MISS_POINTS = {"fg_missed_0_19": -1, "fg_missed_20_29": -1, "fg_missed_30_39": -1}
K_PAT_MADE, K_PAT_MISS = 1, -1

DEF_SACK, DEF_INT, DEF_FUM_REC, DEF_TD, DEF_SAFETY = 1, 2, 2, 6, 2
DEF_PTS_ALLOWED_TIERS = [  # (max points allowed inclusive, fantasy points)
    (0, 5), (6, 4), (13, 3), (20, 1), (27, 0), (34, -1), (999, -3),
]
# nflreadpy uses "LA" for the Rams; Sleeper's team-defense player_id is "LAR".
TEAM_CODE_FIX = {"LA": "LAR"}


def _kicker_points(r: dict) -> float:
    pts = 0.0
    for col, val in K_FG_POINTS.items():
        pts += (r.get(col) or 0) * val
    for col, val in K_FG_MISS_POINTS.items():
        pts += (r.get(col) or 0) * val
    pts += (r.get("pat_made") or 0) * K_PAT_MADE
    pts += (r.get("pat_missed") or 0) * K_PAT_MISS
    return pts


def _def_points(r: dict, points_allowed: int | None) -> float:
    pts = 0.0
    pts += (r.get("def_sacks") or 0) * DEF_SACK
    pts += (r.get("def_interceptions") or 0) * DEF_INT
    pts += (r.get("fumble_recovery_opp") or 0) * DEF_FUM_REC
    pts += (r.get("def_tds") or 0) * DEF_TD
    pts += (r.get("fumble_recovery_tds") or 0) * DEF_TD
    pts += (r.get("special_teams_tds") or 0) * DEF_TD
    pts += (r.get("def_safeties") or 0) * DEF_SAFETY
    if points_allowed is not None:
        for cap, tier_pts in DEF_PTS_ALLOWED_TIERS:
            if points_allowed <= cap:
                pts += tier_pts
                break
    return pts


# Sleeper scoring_settings keys that map onto a per-unit nflreadpy stat
# column (points per unit, e.g. pass_yd=0.04 -> 0.04 pts/passing yard).
# TD-distance bonus keys (rec_td_40p/50p, rush_td_40p/50p, pass_td_40p/50p)
# are deliberately NOT included — nflreadpy's weekly aggregates have no
# per-play TD length, so those categories can't be scored from this data;
# same class of approximation already accepted for K/DEF above.
STAT_KEY_MAP = {
    "pass_yd": "passing_yards", "pass_td": "passing_tds", "pass_int": "passing_interceptions",
    "pass_2pt": "passing_2pt_conversions",
    "rush_yd": "rushing_yards", "rush_td": "rushing_tds", "rush_2pt": "rushing_2pt_conversions",
    "rec": "receptions", "rec_yd": "receiving_yards", "rec_td": "receiving_tds",
    "rec_2pt": "receiving_2pt_conversions",
    "fum": "fumbles_total", "fum_lost": "fumbles_lost_total",
}
# Threshold bonuses: sleeper_key -> (nflreadpy yardage column, threshold).
# Each fires independently per Sleeper's own convention — e.g. 425 passing
# yards earns BOTH bonus_pass_yd_300 and bonus_pass_yd_400, not just the
# higher one.
BONUS_YD_KEYS = {
    "bonus_pass_yd_300": ("passing_yards", 300), "bonus_pass_yd_400": ("passing_yards", 400),
    "bonus_rush_yd_100": ("rushing_yards", 100), "bonus_rush_yd_200": ("rushing_yards", 200),
    "bonus_rec_yd_100": ("receiving_yards", 100), "bonus_rec_yd_200": ("receiving_yards", 200),
}


def score_stats(stats: dict, scoring_settings: dict) -> float:
    """Score one player-week's raw nflreadpy stat components under a
    league's actual Sleeper scoring_settings, instead of nflreadpy's
    generic fantasy_points_ppr column. Skill positions only (QB/RB/WR/TE)
    — K/DEF already have their own scoring functions above (_kicker_points/
    _def_points), reused as-is rather than duplicated here."""
    pts = 0.0
    for sleeper_key, stat_col in STAT_KEY_MAP.items():
        weight = scoring_settings.get(sleeper_key)
        if weight:
            pts += (stats.get(stat_col) or 0) * weight
    for sleeper_key, (stat_col, threshold) in BONUS_YD_KEYS.items():
        weight = scoring_settings.get(sleeper_key)
        if weight and (stats.get(stat_col) or 0) >= threshold:
            pts += weight
    return pts


def _migrate(conn: sqlite3.Connection) -> None:
    """SCHEMA's CREATE TABLE IF NOT EXISTS only helps brand-new tables —
    columns added to an already-existing table (risk_score/risk_note on
    draft_pool) need an explicit ALTER TABLE, checked once per connection."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(draft_pool)")}
    if "risk_score" not in cols:
        conn.execute("ALTER TABLE draft_pool ADD COLUMN risk_score REAL")
    if "risk_note" not in cols:
        conn.execute("ALTER TABLE draft_pool ADD COLUMN risk_note TEXT")
    player_cols = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
    if "injury_status" not in player_cols:
        conn.execute("ALTER TABLE players ADD COLUMN injury_status TEXT")
    if "injury_body_part" not in player_cols:
        conn.execute("ALTER TABLE players ADD COLUMN injury_body_part TEXT")
    proj_cols = {row[1] for row in conn.execute("PRAGMA table_info(projections)")}
    if "blend_note" not in proj_cols:
        conn.execute("ALTER TABLE projections ADD COLUMN blend_note TEXT")
    conn.commit()


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def sleeper_get(path: str, bust_cache: bool = False) -> dict | list:
    """bust_cache: append a cache-busting query param. Sleeper's public API sits
    behind Cloudflare with cache-control: public, s-maxage=60-300,
    stale-while-revalidate=180-300 on GET endpoints (verified live against real
    response headers) -- a shared edge cache is fully entitled to serve a
    same-URL GET as a HIT for up to several minutes, which draft-live's poll_once
    hit hard during a live mock draft (repeated single-shot polls returned the
    same stale pick count for tens of seconds, not resolving on retry). A random
    query param changes the cache key and forces cf-cache-status: MISS (verified
    live), so poll_once's two live-data calls (draft object, picks) use this --
    every other sleeper_get caller (rosters, players, etc.) leaves it off since
    a short cache is harmless/desirable there and cuts needless load on
    Sleeper's API."""
    url = f"{SLEEPER_BASE}{path}"
    if bust_cache:
        sep = '&' if '?' in url else '?'
        url = f"{url}{sep}_={int(time.time() * 1000)}"
    resp = httpx.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def current_week() -> int:
    state = sleeper_get("/state/nfl")
    return int(state.get("week") or 1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_sync_players(force: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not force and PLAYERS_CACHE_PATH.exists():
        age = time.time() - PLAYERS_CACHE_PATH.stat().st_mtime
        if age < PLAYERS_CACHE_MAX_AGE:
            print(f"players.json is {age / 3600:.1f}h old (< 24h) — skipping fetch, "
                  f"loading from cache. Use --force to refetch.")
            players = json.loads(PLAYERS_CACHE_PATH.read_text(encoding="utf-8"))
            _upsert_players(players)
            return

    print("fetching Sleeper player map (~5MB, this is slow)...")
    players = sleeper_get("/players/nfl")
    PLAYERS_CACHE_PATH.write_text(json.dumps(players), encoding="utf-8")
    _upsert_players(players)


def _upsert_players(players: dict) -> None:
    conn = get_db()
    n = 0
    for player_id, p in players.items():
        if not isinstance(p, dict):
            continue
        conn.execute(
            "INSERT INTO players (player_id, full_name, position, team, status, espn_id, "
            "injury_status, injury_body_part, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(player_id) DO UPDATE SET "
            "full_name=excluded.full_name, position=excluded.position, team=excluded.team, "
            "status=excluded.status, espn_id=excluded.espn_id, "
            "injury_status=excluded.injury_status, injury_body_part=excluded.injury_body_part, "
            "updated_at=excluded.updated_at",
            (
                player_id,
                p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                p.get("position"),
                p.get("team"),
                p.get("status"),
                p.get("espn_id"),
                p.get("injury_status"),
                p.get("injury_body_part"),
            ),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"upserted {n} players into data/fantasy.db")


def cmd_sync_sleeper(league_ids: list[str], week: int | None) -> None:
    if not league_ids:
        print("No league IDs given and SLEEPER_LEAGUE_IDS is not set in .env. Nothing to sync.")
        return

    if week is None:
        week = current_week()

    conn = get_db()
    for league_id in league_ids:
        league = sleeper_get(f"/league/{league_id}")
        conn.execute(
            "INSERT INTO sleeper_leagues (league_id, name, season, roster_positions, scoring_settings, synced_at) "
            "VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(league_id) DO UPDATE SET "
            "name=excluded.name, season=excluded.season, roster_positions=excluded.roster_positions, "
            "scoring_settings=excluded.scoring_settings, synced_at=excluded.synced_at",
            (
                league_id,
                league.get("name"),
                league.get("season"),
                json.dumps(league.get("roster_positions")),
                json.dumps(league.get("scoring_settings")),
            ),
        )

        users = sleeper_get(f"/league/{league_id}/users")
        owner_names = {u["user_id"]: (u.get("display_name") or u.get("username")) for u in users}

        rosters = sleeper_get(f"/league/{league_id}/rosters")
        for r in rosters:
            settings = r.get("settings") or {}
            conn.execute(
                "INSERT INTO sleeper_rosters (league_id, roster_id, owner_id, owner_name, player_ids, "
                "starters, wins, losses, ties, fpts, fpts_against) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(league_id, roster_id) DO UPDATE SET "
                "owner_id=excluded.owner_id, owner_name=excluded.owner_name, player_ids=excluded.player_ids, "
                "starters=excluded.starters, wins=excluded.wins, losses=excluded.losses, ties=excluded.ties, "
                "fpts=excluded.fpts, fpts_against=excluded.fpts_against",
                (
                    league_id,
                    r.get("roster_id"),
                    r.get("owner_id"),
                    owner_names.get(r.get("owner_id"), ""),
                    json.dumps(r.get("players")),
                    json.dumps(r.get("starters")),
                    settings.get("wins"),
                    settings.get("losses"),
                    settings.get("ties"),
                    settings.get("fpts"),
                    settings.get("fpts_against"),
                ),
            )

        matchups = sleeper_get(f"/league/{league_id}/matchups/{week}")
        for m in matchups:
            conn.execute(
                "INSERT INTO sleeper_matchups (league_id, week, roster_id, matchup_id, points, starters) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(league_id, week, roster_id) DO UPDATE SET "
                "matchup_id=excluded.matchup_id, points=excluded.points, starters=excluded.starters",
                (
                    league_id,
                    week,
                    m.get("roster_id"),
                    m.get("matchup_id"),
                    m.get("points"),
                    json.dumps(m.get("starters")),
                ),
            )

        transactions = sleeper_get(f"/league/{league_id}/transactions/{week}")
        for t in transactions:
            conn.execute(
                "INSERT INTO sleeper_transactions (league_id, transaction_id, type, week, status, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(league_id, transaction_id) DO UPDATE SET "
                "type=excluded.type, status=excluded.status, data=excluded.data, created=excluded.created",
                (
                    league_id,
                    t.get("transaction_id"),
                    t.get("type"),
                    week,
                    t.get("status"),
                    json.dumps(t),
                    str(t.get("created")),
                ),
            )

        conn.commit()
        print(f"league {league_id} ({league.get('name')}): {len(rosters)} rosters, "
              f"{len(matchups)} matchup entries, {len(transactions)} transactions (week {week})")

    conn.close()


def _id_crosswalk() -> tuple[dict[str, str], dict[str, str]]:
    """gsis_id -> sleeper_id and pfr_id -> sleeper_id maps, via nflreadpy's
    ff_playerids table (built for exactly this cross-source join)."""
    ids = nfl.load_ff_playerids().to_dicts()
    gsis_to_sleeper = {r["gsis_id"]: r["sleeper_id"] for r in ids if r.get("gsis_id") and r.get("sleeper_id")}
    pfr_to_sleeper = {r["pfr_id"]: r["sleeper_id"] for r in ids if r.get("pfr_id") and r.get("sleeper_id")}
    return gsis_to_sleeper, pfr_to_sleeper


def _fp_sleeper_crosswalk() -> dict[int, str]:
    """FantasyPros id -> Sleeper player_id, via the same ff_playerids table
    _id_crosswalk uses (different id pair). DST rows aren't in ff_playerids
    at all — those are matched separately by team code in cmd_sync_rankings
    itself, same convention as sync-dk's DST handling."""
    ids = nfl.load_ff_playerids().to_dicts()
    return {r["fantasypros_id"]: r["sleeper_id"] for r in ids if r.get("fantasypros_id") and r.get("sleeper_id")}


def cmd_sync_stats(season: int, week: int | None) -> None:
    gsis_to_sleeper, pfr_to_sleeper = _id_crosswalk()

    print(f"fetching player stats for season {season}...")
    stats = nfl.load_player_stats(seasons=season, summary_level="week").to_dicts()
    if week is not None:
        stats = [r for r in stats if r.get("week") == week]

    print(f"fetching snap counts for season {season}...")
    snaps = nfl.load_snap_counts(seasons=season).to_dicts()
    snap_pct_by_key: dict[tuple[str, int], float] = {}
    for r in snaps:
        sleeper_id = pfr_to_sleeper.get(r.get("pfr_player_id"))
        if not sleeper_id:
            continue
        snap_pct_by_key[(sleeper_id, r.get("week"))] = r.get("offense_pct")

    conn = get_db()
    n, skipped = 0, 0
    for r in stats:
        sleeper_id = gsis_to_sleeper.get(r.get("player_id"))
        if not sleeper_id:
            skipped += 1
            continue
        if r.get("position") == "K":
            k_pts = _kicker_points(r)
            r = {**r, "fantasy_points": k_pts, "fantasy_points_ppr": k_pts}
        conn.execute(
            "INSERT INTO weekly_stats (player_id, season, week, team, opponent_team, position, "
            "snap_pct, targets, carries, receptions, fantasy_points, fantasy_points_ppr, stats) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player_id, season, week) DO UPDATE SET "
            "team=excluded.team, opponent_team=excluded.opponent_team, position=excluded.position, "
            "snap_pct=excluded.snap_pct, targets=excluded.targets, carries=excluded.carries, "
            "receptions=excluded.receptions, fantasy_points=excluded.fantasy_points, "
            "fantasy_points_ppr=excluded.fantasy_points_ppr, stats=excluded.stats",
            (
                sleeper_id, season, r.get("week"), r.get("team"), r.get("opponent_team"), r.get("position"),
                snap_pct_by_key.get((sleeper_id, r.get("week"))),
                r.get("targets"), r.get("carries"), r.get("receptions"),
                r.get("fantasy_points"), r.get("fantasy_points_ppr"),
                json.dumps(r, default=str),
            ),
        )
        n += 1
    conn.commit()
    print(f"upserted {n} weekly_stats rows ({skipped} skipped — no gsis_id -> sleeper_id crosswalk match)")

    print(f"fetching injury reports for season {season}...")
    injuries = nfl.load_injuries(seasons=season).to_dicts()
    if week is not None:
        injuries = [r for r in injuries if r.get("week") == week]
    n_inj, skipped_inj = 0, 0
    for r in injuries:
        sleeper_id = gsis_to_sleeper.get(r.get("gsis_id"))
        if not sleeper_id:
            skipped_inj += 1
            continue
        conn.execute(
            "INSERT INTO injuries (player_id, season, week, report_status, practice_status, injury) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player_id, season, week) DO UPDATE SET "
            "report_status=excluded.report_status, practice_status=excluded.practice_status, "
            "injury=excluded.injury, synced_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            (
                sleeper_id, season, r.get("week"), r.get("report_status"), r.get("practice_status"),
                r.get("report_primary_injury"),
            ),
        )
        n_inj += 1
    conn.commit()
    print(f"upserted {n_inj} injury rows ({skipped_inj} skipped — no crosswalk match)")

    print(f"fetching team defense stats for season {season}...")
    team_stats = nfl.load_team_stats(seasons=season).to_dicts()
    if week is not None:
        team_stats = [r for r in team_stats if r.get("week") == week]

    all_games = nfl.load_schedules(seasons=season)
    schedule = all_games.to_dicts()
    points_allowed_by_team_week: dict[tuple[str, int], int] = {}
    for g in schedule:
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        home = TEAM_CODE_FIX.get(g["home_team"], g["home_team"])
        away = TEAM_CODE_FIX.get(g["away_team"], g["away_team"])
        points_allowed_by_team_week[(home, g["week"])] = g["away_score"]
        points_allowed_by_team_week[(away, g["week"])] = g["home_score"]

    n_def = 0
    for r in team_stats:
        team = TEAM_CODE_FIX.get(r.get("team"), r.get("team"))
        wk = r.get("week")
        allowed = points_allowed_by_team_week.get((team, wk))
        pts = _def_points(r, allowed)
        conn.execute(
            "INSERT INTO weekly_stats (player_id, season, week, team, opponent_team, position, "
            "snap_pct, targets, carries, receptions, fantasy_points, fantasy_points_ppr, stats) "
            "VALUES (?, ?, ?, ?, ?, 'DEF', NULL, NULL, NULL, NULL, ?, ?, ?) "
            "ON CONFLICT(player_id, season, week) DO UPDATE SET "
            "team=excluded.team, opponent_team=excluded.opponent_team, position=excluded.position, "
            "fantasy_points=excluded.fantasy_points, fantasy_points_ppr=excluded.fantasy_points_ppr, "
            "stats=excluded.stats",
            (
                team, season, wk, team, r.get("opponent_team"),
                pts, pts, json.dumps({**r, "points_allowed": allowed}, default=str),
            ),
        )
        n_def += 1
    conn.commit()
    conn.close()
    print(f"upserted {n_def} DEF weekly_stats rows (custom-scored: nflreadpy has no fantasy_points for team defense)")


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def cmd_project(season: int, week: int) -> None:
    """In-house heuristic, not a professional projection system:
    proj = recency-weighted avg of a player's last up to 3 games' PPR points,
    adjusted by (a) how many PPR points the week's opponent has allowed to
    that position vs. league average, (b) recent snap-share trend vs.
    season-to-date, and (c) the team's Vegas implied total for that week vs.
    the week's own average implied total, if sync-odds has been run for this
    week (skipped entirely, no fabricated 1.0 baked in as a fake signal, when
    no game_odds row exists for a team). Players with zero prior games in the
    season get no projection row — never fabricate a number with no
    underlying data.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    prior_rows = conn.execute(
        "SELECT * FROM weekly_stats WHERE season = ? AND week < ? ORDER BY week",
        (season, week),
    ).fetchall()
    if not prior_rows:
        print(f"no weekly_stats for season {season} before week {week} — run sync-stats first")
        conn.close()
        return

    by_player: dict[str, list[sqlite3.Row]] = {}
    for r in prior_rows:
        by_player.setdefault(r["player_id"], []).append(r)

    # league avg PPR allowed per position, and per (opponent, position)
    pos_totals: dict[str, list[float]] = {}
    opp_pos_totals: dict[tuple[str, str], list[float]] = {}
    for r in prior_rows:
        pos, opp, pts = r["position"], r["opponent_team"], r["fantasy_points_ppr"]
        if not pos or pts is None:
            continue
        pos_totals.setdefault(pos, []).append(pts)
        if opp:
            opp_pos_totals.setdefault((opp, pos), []).append(pts)
    league_avg_by_pos = {p: sum(v) / len(v) for p, v in pos_totals.items() if v}

    all_games = nfl.load_schedules(seasons=season)
    schedule = all_games.filter(all_games["week"] == week).to_dicts()
    opponent_for_team: dict[str, str] = {}
    for g in schedule:
        opponent_for_team[g["home_team"]] = g["away_team"]
        opponent_for_team[g["away_team"]] = g["home_team"]

    odds_rows = conn.execute(
        "SELECT home_team, away_team, home_implied_total, away_implied_total "
        "FROM game_odds WHERE season = ? AND week = ?",
        (season, week),
    ).fetchall()
    implied_total_for_team: dict[str, float] = {}
    for r in odds_rows:
        if r["home_implied_total"] is not None:
            implied_total_for_team[r["home_team"]] = r["home_implied_total"]
        if r["away_implied_total"] is not None:
            implied_total_for_team[r["away_team"]] = r["away_implied_total"]
    week_avg_implied = (
        sum(implied_total_for_team.values()) / len(implied_total_for_team)
        if implied_total_for_team else None
    )

    conn2 = get_db()
    n = 0
    for player_id, games in by_player.items():
        games_sorted = sorted(games, key=lambda r: r["week"])
        recent = [g for g in games_sorted if g["fantasy_points_ppr"] is not None][-3:]
        if not recent:
            continue
        weights = [0.2, 0.3, 0.5][-len(recent):]
        weights = [w / sum(weights) for w in weights]
        base = sum(w * g["fantasy_points_ppr"] for w, g in zip(weights, recent))

        pos = games_sorted[-1]["position"]
        team = games_sorted[-1]["team"]
        opp = opponent_for_team.get(team)
        matchup_mult = 1.0
        if opp and pos in league_avg_by_pos and league_avg_by_pos[pos]:
            allowed = opp_pos_totals.get((opp, pos))
            if allowed and len(allowed) >= 2:
                matchup_mult = _clip(sum(allowed) / len(allowed) / league_avg_by_pos[pos], 0.75, 1.25)

        snap_mult = 1.0
        snaps_all = [g["snap_pct"] for g in games_sorted if g["snap_pct"] is not None]
        snaps_recent = [g["snap_pct"] for g in recent if g["snap_pct"] is not None]
        if len(snaps_all) >= 2 and snaps_recent:
            season_avg_snap = sum(snaps_all) / len(snaps_all)
            recent_avg_snap = sum(snaps_recent) / len(snaps_recent)
            if season_avg_snap:
                snap_mult = _clip(recent_avg_snap / season_avg_snap, 0.85, 1.15)

        odds_mult = 1.0
        if week_avg_implied and team in implied_total_for_team:
            odds_mult = _clip(implied_total_for_team[team] / week_avg_implied, 0.85, 1.15)

        proj = base * matchup_mult * snap_mult * odds_mult
        conn2.execute(
            "INSERT INTO projections (player_id, season, week, proj_points, method, generated_at) "
            "VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(player_id, season, week) DO UPDATE SET "
            "proj_points=excluded.proj_points, method=excluded.method, generated_at=excluded.generated_at",
            (player_id, season, week, round(proj, 2), "heuristic-v1"),
        )
        n += 1
    conn2.commit()
    conn2.close()
    conn.close()
    print(f"wrote {n} projections for season {season} week {week} (method heuristic-v1)")


def _team_name_crosswalk() -> dict[str, str]:
    """Odds API full team names ('Kansas City Chiefs') -> nflverse team_abbr
    ('KC'). nflreadpy's own team_name column matches Odds API's naming
    exactly, so no hand-maintained mapping is needed."""
    teams = nfl.load_teams().select(["team_abbr", "team_name"]).to_dicts()
    return {t["team_name"]: t["team_abbr"] for t in teams}


def cmd_sync_odds(season: int, week: int) -> None:
    """Pull NFL spreads/totals from The Odds API (requires ODDS_API_KEY in
    .env -- paid tier, api.the-odds-api.com), average across whatever
    bookmakers are returned, and derive each team's implied total:
    home_implied = (total - spread_home) / 2, away_implied = (total +
    spread_home) / 2 (spread_home negative = home favored). The Odds API has
    no season/week concept of its own -- games are matched to this
    season/week by home/away team pair against nflreadpy's own schedule, so
    only games nflreadpy already has scheduled for this week get a row.
    """
    if not ODDS_API_KEY:
        print("ODDS_API_KEY not set in .env -- sign up at the-odds-api.com and add it first. Skipping.")
        return

    all_games = nfl.load_schedules(seasons=season)
    schedule = all_games.filter(all_games["week"] == week).to_dicts()
    week_pairs = {(g["home_team"], g["away_team"]) for g in schedule}
    if not week_pairs:
        print(f"no schedule rows for season {season} week {week} -- nothing to match odds against")
        return

    name_to_abbr = _team_name_crosswalk()

    resp = httpx.get(
        ODDS_API_BASE,
        params={"apiKey": ODDS_API_KEY, "regions": "us", "markets": "spreads,totals", "oddsFormat": "american"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    events = resp.json()

    conn = get_db()
    n = 0
    for ev in events:
        home_abbr = name_to_abbr.get(ev.get("home_team", ""))
        away_abbr = name_to_abbr.get(ev.get("away_team", ""))
        if not home_abbr or not away_abbr or (home_abbr, away_abbr) not in week_pairs:
            continue

        spreads, totals = [], []
        for bk in ev.get("bookmakers", []):
            for market in bk.get("markets", []):
                if market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == ev["home_team"] and outcome.get("point") is not None:
                            spreads.append(outcome["point"])
                elif market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over" and outcome.get("point") is not None:
                            totals.append(outcome["point"])
        if not spreads or not totals:
            continue

        spread_home = sum(spreads) / len(spreads)
        total = sum(totals) / len(totals)
        home_implied = (total - spread_home) / 2
        away_implied = (total + spread_home) / 2

        conn.execute(
            "INSERT INTO game_odds (season, week, home_team, away_team, commence_time, spread_home, total, "
            "home_implied_total, away_implied_total, bookmaker_count, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(season, week, home_team, away_team) DO UPDATE SET "
            "commence_time=excluded.commence_time, spread_home=excluded.spread_home, total=excluded.total, "
            "home_implied_total=excluded.home_implied_total, away_implied_total=excluded.away_implied_total, "
            "bookmaker_count=excluded.bookmaker_count, synced_at=excluded.synced_at",
            (season, week, home_abbr, away_abbr, ev.get("commence_time"), round(spread_home, 1), round(total, 1),
             round(home_implied, 1), round(away_implied, 1), len(ev.get("bookmakers", []))),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"wrote {n} game_odds rows for season {season} week {week}")


def cmd_sync_weather(season: int, week: int) -> None:
    """Pull forecast conditions for each outdoor/open-roof game this week via
    the National Weather Service API (free, no key, US-only -- see
    api.weather.gov). Dome/closed-roof games get a row with is_dome=1 and no
    external call (weather is irrelevant there). NWS forecasts only cover
    roughly the next 7 days -- a game too far out gets no row rather than a
    fabricated forecast; re-run sync-weather closer to kickoff.
    """
    all_games = nfl.load_schedules(seasons=season)
    schedule = all_games.filter(all_games["week"] == week).to_dicts()
    if not schedule:
        print(f"no schedule rows for season {season} week {week}")
        return

    conn = get_db()
    n_weather, n_dome, n_skipped = 0, 0, 0
    headers = {"User-Agent": NWS_USER_AGENT}
    for g in schedule:
        team = g["home_team"]
        roof = (g.get("roof") or "").lower()
        is_dome = 1 if roof in ("dome", "closed") else 0

        if is_dome:
            conn.execute(
                "INSERT INTO game_weather (season, week, team, is_dome, synced_at) "
                "VALUES (?, ?, ?, 1, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(season, week, team) DO UPDATE SET is_dome=1, synced_at=excluded.synced_at",
                (season, week, team),
            )
            n_dome += 1
            continue

        coords = STADIUM_COORDS.get(team)
        if not coords:
            n_skipped += 1
            continue
        lat, lon = coords

        try:
            points_resp = httpx.get(f"https://api.weather.gov/points/{lat},{lon}", headers=headers, timeout=REQUEST_TIMEOUT)
            points_resp.raise_for_status()
            forecast_url = points_resp.json()["properties"]["forecast"]
            forecast_resp = httpx.get(forecast_url, headers=headers, timeout=REQUEST_TIMEOUT)
            forecast_resp.raise_for_status()
            periods = forecast_resp.json()["properties"]["periods"]
        except httpx.HTTPError:
            n_skipped += 1
            continue

        commence = g.get("gameday")
        period = next(
            (p for p in periods if not commence or p["startTime"][:10] == commence),
            periods[0] if periods else None,
        )
        if not period:
            n_skipped += 1
            continue

        wind_str = period.get("windSpeed", "")
        wind_mph = None
        m = re.search(r"(\d+)", wind_str or "")
        if m:
            wind_mph = float(m.group(1))

        conn.execute(
            "INSERT INTO game_weather (season, week, team, is_dome, temp_f, wind_mph, precip_pct, "
            "short_forecast, synced_at) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(season, week, team) DO UPDATE SET is_dome=0, temp_f=excluded.temp_f, "
            "wind_mph=excluded.wind_mph, precip_pct=excluded.precip_pct, "
            "short_forecast=excluded.short_forecast, synced_at=excluded.synced_at",
            (season, week, team, period.get("temperature"), wind_mph,
             period.get("probabilityOfPrecipitation", {}).get("value"), period.get("shortForecast")),
        )
        n_weather += 1
    conn.commit()
    conn.close()
    print(f"wrote {n_weather} outdoor forecasts, {n_dome} dome rows, skipped {n_skipped} "
          f"(no coords or forecast too far out) for season {season} week {week}")


def cmd_sync_rankings(season: int, force: bool) -> None:
    """Pull FantasyPros dynasty-overall consensus ECR via nflreadpy's
    load_ff_rankings — the one ecr_type/page_type combo that's a single
    cross-position ranked list (every other ecr_type is a position-only
    page whose rank resets per position), which is what a draft board
    needs. Crosswalked to Sleeper player_id via load_ff_playerids; IDP
    (DL/LB/DB, not used in these leagues) is skipped outright, DST is
    matched by team code same as sync-dk."""
    conn = get_db()
    if not force:
        row = conn.execute(
            "SELECT synced_at FROM rankings WHERE season = ? AND ecr_type = 'do' "
            "ORDER BY synced_at DESC LIMIT 1", (season,),
        ).fetchone()
        if row and row[0].startswith(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")):
            print(f"rankings for season {season} already synced today ({row[0]}) — use --force to refetch")
            conn.close()
            return

    print(f"fetching FantasyPros dynasty-overall ECR for season {season}...")
    df = nfl.load_ff_rankings()
    rows = df.filter((df["ecr_type"] == "do") & (df["page_type"] == "dynasty-overall")).to_dicts()

    fp_to_sleeper = _fp_sleeper_crosswalk()
    n, skipped_idp, skipped_unmatched = 0, 0, 0
    for r in rows:
        pos = r.get("pos")
        if pos in ("DL", "LB", "DB"):
            skipped_idp += 1
            continue
        if pos == "DST":
            sleeper_id = TEAM_CODE_FIX.get(r.get("team"), r.get("team")) or None
        else:
            sleeper_id = fp_to_sleeper.get(r.get("id"))
        if not sleeper_id:
            skipped_unmatched += 1
        conn.execute(
            "INSERT INTO rankings (season, player_id, fp_id, full_name, position, team, "
            "ecr, ecr_sd, ecr_best, ecr_worst, bye, ecr_type, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'do', strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(season, fp_id, ecr_type) DO UPDATE SET "
            "player_id=excluded.player_id, full_name=excluded.full_name, position=excluded.position, "
            "team=excluded.team, ecr=excluded.ecr, ecr_sd=excluded.ecr_sd, ecr_best=excluded.ecr_best, "
            "ecr_worst=excluded.ecr_worst, bye=excluded.bye, synced_at=excluded.synced_at",
            (season, sleeper_id, r.get("id"), r.get("player"), pos, r.get("team"),
             r.get("ecr"), r.get("sd"), r.get("best"), r.get("worst"), r.get("bye")),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"upserted {n} rankings rows for season {season} ({skipped_idp} IDP skipped, "
          f"{skipped_unmatched} no sleeper_id crosswalk match)")


FP_WEEKLY_PAGE_TYPES = {
    "QB": "weekly-qb", "RB": "weekly-rb", "WR": "weekly-wr", "TE": "weekly-te",
    "K": "weekly-k", "DST": "weekly-dst",
}
SLEEPER_PROJECTION_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def cmd_sync_weekly_rankings(season: int, week: int, force: bool) -> None:
    """Two independent weekly signals, on top of the in-house heuristic
    (project) and rankings' season-long dynasty ECR: FantasyPros' per-
    position weekly consensus ECR (via nflreadpy's load_ff_rankings, same
    dependency sync-rankings already uses -- ecr_type 'wp', one page per
    position rather than the single 'do' cross-position page sync-rankings
    pulls, since a per-position rank is what a start/sit call actually
    needs) and Sleeper's own weekly projections (native player_id, no
    crosswalk needed, and it's the one source that already has real
    numbers for a rookie/new-starter with no prior-week history for
    project's rolling average to work from). Neither call is week-tagged
    at the source -- both are a live snapshot of "the upcoming week" -- so
    the week is stamped from the --week argument, same convention sync-
    odds/sync-weather already use for their own snapshot-style sources.
    Feeds blend-projections; run that after this to fold both into the
    projections table the optimizer actually reads."""
    conn = get_db()
    if not force:
        row = conn.execute(
            "SELECT synced_at FROM weekly_rankings WHERE season = ? AND week = ? "
            "ORDER BY synced_at DESC LIMIT 1", (season, week),
        ).fetchone()
        if row and row[0].startswith(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")):
            print(f"weekly rankings for season {season} week {week} already synced today ({row[0]}) "
                  "— use --force to refetch")
            conn.close()
            return

    fp_to_sleeper = _fp_sleeper_crosswalk()
    print(f"fetching FantasyPros weekly ECR for season {season} week {week}...")
    df = nfl.load_ff_rankings()
    n_fp, skipped_unmatched = 0, 0
    for pos, page_type in FP_WEEKLY_PAGE_TYPES.items():
        rows = df.filter((df["ecr_type"] == "wp") & (df["page_type"] == page_type)).to_dicts()
        for i, r in enumerate(sorted(rows, key=lambda r: r["ecr"] or 999), start=1):
            if pos == "DST":
                sleeper_id = TEAM_CODE_FIX.get(r.get("team"), r.get("team")) or None
            else:
                sleeper_id = fp_to_sleeper.get(r.get("id"))
            if not sleeper_id:
                skipped_unmatched += 1
            conn.execute(
                "INSERT INTO weekly_rankings (player_id, season, week, source, source_id, full_name, "
                "position, team, pos_rank, ecr, synced_at) "
                "VALUES (?, ?, ?, 'fantasypros', ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(season, week, source, source_id) DO UPDATE SET "
                "player_id=excluded.player_id, full_name=excluded.full_name, position=excluded.position, "
                "team=excluded.team, pos_rank=excluded.pos_rank, ecr=excluded.ecr, synced_at=excluded.synced_at",
                (sleeper_id, season, week, str(r.get("id")), r.get("player"), pos, r.get("team"),
                 i, r.get("ecr")),
            )
            n_fp += 1
    conn.commit()
    print(f"  upserted {n_fp} FantasyPros weekly ECR rows ({skipped_unmatched} no sleeper_id crosswalk match)")

    print(f"fetching Sleeper weekly projections for season {season} week {week}...")
    n_sleeper = 0
    for pos in SLEEPER_PROJECTION_POSITIONS:
        resp = httpx.get(
            f"https://api.sleeper.app/projections/nfl/{season}/{week}",
            params={"season_type": "regular", "position[]": pos},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        for r in resp.json():
            pid = r.get("player_id")
            stats = r.get("stats") or {}
            proj = stats.get("pts_ppr")
            if not pid or proj is None:
                continue
            player = r.get("player") or {}
            name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            conn.execute(
                "INSERT INTO weekly_rankings (player_id, season, week, source, source_id, full_name, "
                "position, team, proj_points, synced_at) "
                "VALUES (?, ?, ?, 'sleeper', ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(season, week, source, source_id) DO UPDATE SET "
                "player_id=excluded.player_id, full_name=excluded.full_name, position=excluded.position, "
                "team=excluded.team, proj_points=excluded.proj_points, synced_at=excluded.synced_at",
                (pid, season, week, pid, name, pos, r.get("team"), proj),
            )
            n_sleeper += 1
    conn.commit()
    conn.close()
    print(f"  upserted {n_sleeper} Sleeper weekly projection rows")


def cmd_blend_projections(season: int, week: int, force: bool) -> None:
    """Folds weekly-rankings' two extra signals into the same projections
    row optimize-sleeper already reads, instead of adding a parallel table
    nothing else looks at. Point-valued signals (project's in-house
    heuristic, Sleeper's weekly projection) are simple-averaged into
    proj_points; FantasyPros' weekly ECR is rank-only (no points), so it's
    attached as blend_note context for the report/optimizer output to
    surface, not folded into the number itself. A player with no prior-
    week history has no heuristic row at all (see project's docstring) --
    Sleeper's projection alone fills that gap, which is real synced data,
    not a fabricated number.

    Idempotency: proj_points gets overwritten in place (same single-row-
    per-week design projections already uses for every other source), so
    re-running this after project has already been re-run for the week is
    fine, but running it twice in a row without a fresh project first
    would average an already-blended number back in. Guarded the same way
    sync-rankings/sync-weekly-rankings guard re-fetching: refuses (once,
    table-wide) unless --force."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    if not force:
        row = conn.execute(
            "SELECT method FROM projections WHERE season = ? AND week = ? LIMIT 1", (season, week),
        ).fetchone()
        if row and row["method"] == "blended-v1":
            print(f"projections for season {season} week {week} already blended — "
                  "run project again first, or pass --force to reblend on top of the current value")
            conn.close()
            return

    heuristic = {r["player_id"]: r["proj_points"] for r in conn.execute(
        "SELECT player_id, proj_points FROM projections WHERE season = ? AND week = ?", (season, week),
    ).fetchall()}
    sleeper_proj = {r["player_id"]: r["proj_points"] for r in conn.execute(
        "SELECT player_id, proj_points FROM weekly_rankings WHERE season = ? AND week = ? "
        "AND source = 'sleeper' AND player_id IS NOT NULL", (season, week),
    ).fetchall()}
    fp_rank = {r["player_id"]: (r["position"], r["pos_rank"]) for r in conn.execute(
        "SELECT player_id, position, pos_rank FROM weekly_rankings WHERE season = ? AND week = ? "
        "AND source = 'fantasypros' AND player_id IS NOT NULL", (season, week),
    ).fetchall()}

    n = 0
    for player_id in set(heuristic) | set(sleeper_proj):
        values, used = [], []
        if heuristic.get(player_id) is not None:
            values.append(heuristic[player_id])
            used.append("heuristic")
        if sleeper_proj.get(player_id) is not None:
            values.append(sleeper_proj[player_id])
            used.append("sleeper")
        if not values:
            continue
        blended = round(sum(values) / len(values), 2)
        note_parts = ["+".join(used)]
        if player_id in fp_rank:
            pos, rank = fp_rank[player_id]
            if pos and rank:
                note_parts.append(f"FP {pos}{rank}")
        blend_note = " / ".join(note_parts)
        conn.execute(
            "INSERT INTO projections (player_id, season, week, proj_points, method, blend_note, generated_at) "
            "VALUES (?, ?, ?, ?, 'blended-v1', ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(player_id, season, week) DO UPDATE SET "
            "proj_points=excluded.proj_points, method=excluded.method, blend_note=excluded.blend_note, "
            "generated_at=excluded.generated_at",
            (player_id, season, week, blended, blend_note),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"blended {n} projections for season {season} week {week} (method blended-v1)")


def _slot_eligible(position: str | None, slot: str) -> bool:
    if not position:
        return False
    if slot == "FLEX":
        return position in ("RB", "WR", "TE")
    return position == slot


def compute_sleeper_lineup(conn: sqlite3.Connection, league_id: str, week: int, season: int,
                            roster_id: int | None = None) -> dict:
    """Best starting lineup for one roster via a PuLP LP: maximize total
    projected points subject to each league's actual roster_positions slots
    and position eligibility. Returns starters/bench/unfilled — never
    mutates the DB, callers decide what to do with the result."""
    league_row = conn.execute(
        "SELECT roster_positions, name FROM sleeper_leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    if not league_row:
        raise ValueError(f"no sleeper_leagues row for {league_id} — run sync-sleeper first")
    positions = json.loads(league_row[0])
    league_name = league_row[1]
    start_slots = [p for p in positions if p != "BN"]

    if roster_id is None:
        user_id = os.environ.get("SLEEPER_USER_ID")
        if not user_id:
            raise ValueError("no --roster-id given and SLEEPER_USER_ID is not set in .env")
        r = conn.execute(
            "SELECT roster_id, player_ids FROM sleeper_rosters WHERE league_id = ? AND owner_id = ?",
            (league_id, user_id),
        ).fetchone()
        if not r:
            raise ValueError(f"no roster in league {league_id} owned by SLEEPER_USER_ID {user_id}")
        roster_id, player_ids_json = r
    else:
        r = conn.execute(
            "SELECT player_ids FROM sleeper_rosters WHERE league_id = ? AND roster_id = ?",
            (league_id, roster_id),
        ).fetchone()
        if not r:
            raise ValueError(f"no roster_id {roster_id} in league {league_id}")
        player_ids_json = r[0]

    player_ids = json.loads(player_ids_json) or []
    ruled_out = {"Out", "IR", "Suspended"}
    players = []
    for pid in player_ids:
        prow = conn.execute(
            "SELECT full_name, position, injury_status FROM players WHERE player_id = ?", (pid,)
        ).fetchone()
        proj_row = conn.execute(
            "SELECT proj_points, blend_note FROM projections WHERE player_id = ? AND season = ? AND week = ?",
            (pid, season, week),
        ).fetchone()
        inj_row = conn.execute(
            "SELECT report_status FROM injuries WHERE player_id = ? AND season = ? AND week = ?",
            (pid, season, week),
        ).fetchone()
        # Two independent injury sources, neither reliable alone: nflverse's
        # weekly practice-report status (report_status) is often NULL for a
        # post-surgery/long-term-IR player since that news never generates a
        # normal Wed/Thu/Fri practice designation, while Sleeper's own
        # injury_status is refreshed from team news but only as fresh as the
        # last sync-players run. Take whichever one actually flags the player.
        sleeper_status = prow[2] if prow else None
        report_status = inj_row[0] if inj_row else None
        injury_status = sleeper_status if sleeper_status in ruled_out else report_status
        players.append({
            "player_id": pid,
            "name": prow[0] if prow else pid,
            "position": prow[1] if prow else None,
            "proj_points": proj_row[0] if proj_row else None,
            "blend_note": proj_row[1] if proj_row else None,
            "injury_status": injury_status,
        })

    prob = pulp.LpProblem("sleeper_lineup", pulp.LpMaximize)
    x = {}
    for i, p in enumerate(players):
        if p["proj_points"] is None:
            continue  # no data to start them on — never fabricate a number to justify a start
        if p["injury_status"] in ruled_out:
            continue  # a rolling-average projection has no way to reflect "not playing"
        for j, slot in enumerate(start_slots):
            if _slot_eligible(p["position"], slot):
                x[(i, j)] = pulp.LpVariable(f"x_{i}_{j}", cat="Binary")

    prob += pulp.lpSum(var * players[i]["proj_points"] for (i, j), var in x.items())
    for i in range(len(players)):
        prob += pulp.lpSum(var for (pi, pj), var in x.items() if pi == i) <= 1
    for j in range(len(start_slots)):
        prob += pulp.lpSum(var for (pi, pj), var in x.items() if pj == j) <= 1
    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    slot_to_player = {}
    for (i, j), var in x.items():
        if var.value() == 1:
            slot_to_player[j] = i

    starters = []
    for j, slot in enumerate(start_slots):
        starters.append({"slot": slot, "player": players[slot_to_player[j]] if j in slot_to_player else None})
    started_idx = set(slot_to_player.values())
    bench = [p for i, p in enumerate(players) if i not in started_idx]

    return {
        "league_id": league_id, "league_name": league_name, "roster_id": roster_id,
        "week": week, "season": season, "starters": starters, "bench": bench,
    }


def cmd_optimize_sleeper(league_id: str, week: int, season: int, roster_id: int | None) -> None:
    conn = get_db()
    lineup = compute_sleeper_lineup(conn, league_id, week, season, roster_id)
    conn.close()
    print(f"{lineup['league_name']} — roster {lineup['roster_id']}, week {week} ({season})")
    for s in lineup["starters"]:
        if s["player"]:
            p = s["player"]
            inj = f" [{p['injury_status']}]" if p["injury_status"] else ""
            note = f" ({p['blend_note']})" if p.get("blend_note") else ""
            print(f"  {s['slot']:6} {p['name']:25} {p['proj_points']:.1f}{inj}{note}")
        else:
            print(f"  {s['slot']:6} UNFILLED (no eligible/projected player)")
    print("  BENCH:")
    for p in lineup["bench"]:
        proj = f"{p['proj_points']:.1f}" if p["proj_points"] is not None else "no proj"
        print(f"    {p['name']:25} {p['position'] or '?':4} {proj}")


def _ollama_chat(system: str, user: str, model: str) -> str:
    resp = httpx.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "")


REPORT_SYSTEM = (
    "You write a terse weekly fantasy football report from a JSON optimizer result "
    "for one league. The JSON's 'starters' list (one entry per roster slot, with a "
    "'player' object or null) and 'bench' list are the FINAL, ALREADY-DECIDED lineup "
    "— an LP solver chose it to maximize total proj_points under each slot's position "
    "eligibility. Your job is only to NARRATE and EXPLAIN that decision, never to "
    "propose a different one: for each starter, one line on why they're in over the "
    "closest eligible bench player at the same position (name them only if such a "
    "bench player exists); flag any starter or bench player with a non-null "
    "injury_status. Never call a bench-list player a 'start' recommendation and never "
    "call a starters-list player a 'sit' recommendation — they are already placed. "
    "Output markdown: a short header, then one bullet per starting slot. Do NOT "
    "invent any player, stat, or projection not present in the JSON. If proj_points "
    "is null for a player, say data is missing rather than guessing a number."
)


def cmd_report(week: int, season: int, league_ids: list[str], model: str, force: bool) -> None:
    conn = get_db()
    for league_id in league_ids:
        existing = conn.execute(
            "SELECT content FROM reports WHERE season = ? AND week = ? AND league_id = ?",
            (season, week, league_id),
        ).fetchone()
        if existing and not force:
            print(f"--- league {league_id}, week {week} ({season}) [cached, use --force to regenerate] ---")
            print(existing[0])
            continue

        lineup = compute_sleeper_lineup(conn, league_id, week, season)
        payload = json.dumps(lineup, default=str)
        content = _ollama_chat(REPORT_SYSTEM, payload, model)
        header = f"# {lineup['league_name']} — Week {week} ({season})\n\n"
        markdown = header + content
        conn.execute(
            "INSERT INTO reports (season, week, league_id, content, generated_at) "
            "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(season, week, league_id) DO UPDATE SET "
            "content=excluded.content, generated_at=excluded.generated_at",
            (season, week, league_id, markdown),
        )
        conn.commit()
        print(f"--- league {league_id}, week {week} ({season}) ---")
        print(markdown)
    conn.close()


def _league_scoring_factor(conn: sqlite3.Connection, league_id: str, prior_season: int) -> dict[str, float]:
    """SECONDARY adjustment only — never the primary value driver (see
    cmd_build_draft_pool's module note on why raw points-based VBD was
    dropped as primary). Per position, the ratio of every player's
    prior_season total points scored under THIS league's own
    scoring_settings vs. nflreadpy's generic fantasy_points_ppr, averaged
    across all players with data at that position. Captures how much a
    league's real bonus categories (yardage/TD-distance bonuses etc.)
    move that position's value relative to generic PPR — nothing else.
    Clipped to [0.85, 1.20] so it can only nudge the ECR-driven primary
    value, never dominate or invert it. Two leagues, two factors: reads
    only this league_id's own scoring_settings, never shared."""
    row = conn.execute("SELECT scoring_settings FROM sleeper_leagues WHERE league_id = ?", (league_id,)).fetchone()
    if not row or not row[0]:
        raise ValueError(f"no scoring_settings for league {league_id} — run sync-sleeper first")
    scoring_settings = json.loads(row[0])

    rows = conn.execute(
        "SELECT position, stats, fantasy_points_ppr FROM weekly_stats WHERE season = ?",
        (prior_season,),
    ).fetchall()
    league_total: dict[str, float] = {}
    generic_total: dict[str, float] = {}
    for position, stats_json, fp_ppr in rows:
        if not position or position in ("K", "DEF"):
            continue  # K/DEF keep factor 1.0 — already-approximated scoring, see K_FG_POINTS/_def_points
        league_total[position] = league_total.get(position, 0.0) + score_stats(json.loads(stats_json), scoring_settings)
        generic_total[position] = generic_total.get(position, 0.0) + (fp_ppr or 0.0)

    factor = {}
    for pos, generic_sum in generic_total.items():
        if generic_sum:
            factor[pos] = _clip(league_total.get(pos, 0.0) / generic_sum, 0.85, 1.20)
    return factor


def _replacement_ranks(rankings_rows: list[sqlite3.Row], roster_positions: list[str], teams: int = 10) -> dict[str, int]:
    """Replacement-level pos_rank per position, given this league's actual
    roster_positions and team count. Dedicated slots (QB/RB/WR/TE/K/DEF)
    fill first; the shared FLEX pool (RB/WR/TE only, via the existing
    _slot_eligible rule — not reimplemented) is then filled by walking the
    combined RB/WR/TE pool in overall ECR order. The last position to
    consume a slot at each step sets that position's replacement rank."""
    dedicated: dict[str, int] = {}
    for slot in roster_positions:
        if slot in ("BN", "FLEX"):
            continue
        dedicated[slot] = dedicated.get(slot, 0) + 1
    dedicated_counts = {pos: count * teams for pos, count in dedicated.items()}
    flex_slots = roster_positions.count("FLEX") * teams

    replacement_rank = dict(dedicated_counts)

    flex_eligible = [r for r in rankings_rows if _slot_eligible(r["position"], "FLEX")]
    flex_eligible.sort(key=lambda r: r["ecr"])
    filled = {"RB": 0, "WR": 0, "TE": 0}
    flex_used = 0
    for r in flex_eligible:
        pos = r["position"]
        if filled[pos] < dedicated_counts.get(pos, 0):
            filled[pos] += 1
        elif flex_used < flex_slots:
            filled[pos] += 1
            flex_used += 1
        else:
            continue
        replacement_rank[pos] = filled[pos]
    return replacement_rank


def _load_players_raw() -> dict:
    """Sleeper raw player map cached by sync-players carries fields the
    players table does not persist (depth_chart_order, years_exp, age,
    injury_status) -- needed for upside-target and risk-score research
    below. Loaded straight from the on-disk cache rather than re-fetching
    or widening the players table schema for a couple of research-only
    fields."""
    if not PLAYERS_CACHE_PATH.exists():
        raise ValueError("no cached player map -- run sync-players first")
    return json.loads(PLAYERS_CACHE_PATH.read_text(encoding="utf-8"))


def _team_offense_strength(conn: sqlite3.Connection, season: int) -> dict[str, float]:
    """Per-team average weekly fantasy output (QB+RB+WR+TE fantasy_points_ppr
    summed per team-week, then averaged across the season) from already-
    synced weekly_stats -- a simple, fully local proxy for how tough an
    offense is to defend, used only to rank DST schedule strength below.
    Not a substitute for real offensive DVOA/EPA, but does not require a
    new data source either."""
    rows = conn.execute(
        "SELECT team, week, SUM(fantasy_points_ppr) FROM weekly_stats "
        "WHERE season = ? AND position IN ('QB','RB','WR','TE') AND team IS NOT NULL "
        "GROUP BY team, week",
        (season,),
    ).fetchall()
    by_team: dict[str, list[float]] = {}
    for team, _week, total in rows:
        team = TEAM_CODE_FIX.get(team, team)
        by_team.setdefault(team, []).append(total or 0.0)
    return {team: sum(v) / len(v) for team, v in by_team.items() if v}


def cmd_dst_schedule_strength(season: int, as_of_week: int, window: int, strength_season: int | None) -> None:
    """Schedule-strength-adjusted DST rank: for each team, average the
    offensive strength (_team_offense_strength, sourced from strength_season
    weekly_stats -- normally last season, since preseason draft prep has no
    current-season stats yet) of the opponents it actually faces in
    [as_of_week, as_of_week+window). Lower avg_opp_off_ppg = easier upcoming
    stretch = better streaming DST target for that window -- this is
    deliberately NOT the same thing as season-long defensive quality, per
    the users roster-construction rule (schedule over raw DEF ranking). Reusable
    by build-draft-pool (secondary adj_value nudge) and draft-live."""
    strength_season = strength_season or (season - 1)
    conn = get_db()
    off_strength = _team_offense_strength(conn, strength_season)
    if not off_strength:
        raise ValueError(f"no weekly_stats for season {strength_season} -- run sync-stats first")

    sched = nfl.load_schedules(seasons=[season]).to_dicts()
    sched = [g for g in sched if g.get("game_type") == "REG"]

    upcoming_opp: dict[str, list[tuple[int, str]]] = {}
    for g in sched:
        wk = g.get("week")
        if wk is None or wk < as_of_week or wk >= as_of_week + window:
            continue
        home = TEAM_CODE_FIX.get(g["home_team"], g["home_team"])
        away = TEAM_CODE_FIX.get(g["away_team"], g["away_team"])
        upcoming_opp.setdefault(home, []).append((wk, away))
        upcoming_opp.setdefault(away, []).append((wk, home))

    if not upcoming_opp:
        raise ValueError(f"no season {season} REG games found in weeks {as_of_week}-{as_of_week + window - 1} "
                          f"-- check --season/--as-of-week (nflreadpy may not carry this season yet)")

    league_avg = sum(off_strength.values()) / len(off_strength)
    results = []
    for team, opps in upcoming_opp.items():
        strengths = [off_strength.get(opp, league_avg) for _, opp in opps]
        avg_opp = sum(strengths) / len(strengths) if strengths else league_avg
        results.append({
            "team": team, "avg_opp_off_ppg": round(avg_opp, 2), "games": len(opps),
            "opponents": [f"wk{w}:{o}" for w, o in sorted(opps)],
        })
    results.sort(key=lambda r: r["avg_opp_off_ppg"])
    for i, r in enumerate(results, start=1):
        r["schedule_rank"] = i

    conn.execute(
        "DELETE FROM dst_schedule_strength WHERE season = ? AND as_of_week = ? AND window_weeks = ?",
        (season, as_of_week, window),
    )
    for r in results:
        conn.execute(
            "INSERT INTO dst_schedule_strength (team, season, as_of_week, window_weeks, avg_opp_off_ppg, "
            "schedule_rank, games, opponents, strength_season, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
            (r["team"], season, as_of_week, window, r["avg_opp_off_ppg"], r["schedule_rank"],
             r["games"], json.dumps(r["opponents"]), strength_season),
        )
    conn.commit()
    conn.close()

    print(f"DST schedule strength -- season {season}, weeks {as_of_week}-{as_of_week + window - 1} "
          f"(opponent-offense proxy from season {strength_season} weekly_stats):")
    print(f"  {'rank':<4} {'team':<4} {'avg opp off ppg':<16} games  upcoming opponents")
    for r in results:
        print(f"  {r['schedule_rank']:<4} {r['team']:<4} {r['avg_opp_off_ppg']:<16} {r['games']:<6} "
              f"{', '.join(r['opponents'])}")
    print()
    print("Lower avg_opp_off_ppg = easier upcoming stretch of opposing offenses = better streaming/draft "
          "DST target for THIS window specifically -- not a claim about season-long defensive quality.")


def compute_risk_score(conn: sqlite3.Connection, player_id: str, position: str | None,
                        prior_season: int, raw_players: dict) -> tuple[float | None, str]:
    """0-100 risk score (higher = riskier) for one player, built only from
    already-synced local data -- NOT a substitute for real injury-history
    write-ups or beat-reporter depth-chart intel, which would need the
    research subagent's web access this script does not have. Four
    equally-weighted proxies, each independently derivable from
    weekly_stats/injuries/the cached Sleeper player map:
      - performance volatility: coefficient of variation (stdev/mean) of
        prior_season fantasy_points_ppr across games actually played
      - injury-report frequency: fraction of prior_season weeks this player
        had ANY injury report entry (Questionable/Doubtful/Out/IR)
      - snap-share volatility: stdev of prior_season snap_pct across games
        played (a boom/bust role -- benched some weeks, full-time others --
        is a real risk signal distinct from just points volatility)
      - depth-chart competition: current depth_chart_order from the cached
        Sleeper player map; a committee/backup slot (order >= 2) scores
        higher risk than a clear starter
    Only scored for QB/RB/WR/TE (K/DST have no comparable role-competition
    signal in this data and are excluded -- draft_pool.risk_score stays
    NULL for them). Players with zero prior_season games (rookies, new
    signings) get a fixed moderate-high default (65) tagged as "no prior-
    season data" rather than a fabricated number -- true unknown risk, not
    computed risk.
    """
    if position not in ("QB", "RB", "WR", "TE"):
        return None, ""

    rows = conn.execute(
        "SELECT week, fantasy_points_ppr, snap_pct FROM weekly_stats "
        "WHERE player_id = ? AND season = ? ORDER BY week", (player_id, prior_season),
    ).fetchall()
    games_played = [r for r in rows if r[1] is not None]
    if not games_played:
        return 65.0, "no prior-season data (rookie/new signing) -- unknown risk, not a computed one"

    pts = [r[1] for r in games_played]
    mean_pts = sum(pts) / len(pts)
    cv = None
    if mean_pts > 0 and len(pts) >= 2:
        var = sum((p - mean_pts) ** 2 for p in pts) / len(pts)
        cv = (var ** 0.5) / mean_pts
    volatility_risk = _clip((cv or 0.0) / 1.2, 0.0, 1.0)  # CV ~1.2 treated as max-risk ceiling

    inj_weeks = conn.execute(
        "SELECT COUNT(*) FROM injuries WHERE player_id = ? AND season = ? AND report_status IS NOT NULL",
        (player_id, prior_season),
    ).fetchone()[0]
    total_weeks = conn.execute(
        "SELECT COUNT(DISTINCT week) FROM weekly_stats WHERE season = ?", (prior_season,),
    ).fetchone()[0] or 17
    injury_risk = _clip(inj_weeks / total_weeks, 0.0, 1.0)

    snaps = [r[2] for r in games_played if r[2] is not None]
    snap_risk = 0.0
    if len(snaps) >= 2:
        snap_mean = sum(snaps) / len(snaps)
        snap_var = sum((s - snap_mean) ** 2 for s in snaps) / len(snaps)
        snap_risk = _clip((snap_var ** 0.5) / 30.0, 0.0, 1.0)  # 30pp stdev treated as max-risk ceiling

    raw = raw_players.get(player_id) or {}
    depth_order = raw.get("depth_chart_order")
    depth_risk = 0.0 if not depth_order or depth_order <= 1 else _clip((depth_order - 1) / 3.0, 0.0, 1.0)

    score = 100.0 * (0.30 * volatility_risk + 0.30 * injury_risk + 0.20 * snap_risk + 0.20 * depth_risk)

    notes = []
    if volatility_risk > 0.5:
        notes.append(f"volatile weekly output (CV={cv:.2f})" if cv is not None else "volatile weekly output")
    if injury_risk > 0.3:
        notes.append(f"{inj_weeks} injury-report weeks last season")
    if snap_risk > 0.4:
        notes.append("inconsistent snap share")
    if depth_risk > 0:
        notes.append(f"depth chart order {depth_order}")
    note = "; ".join(notes) if notes else "no major local risk flags"
    return round(score, 1), note


def cmd_upside_targets(league_id: str, season: int, prior_season: int | None) -> None:
    """Candidate pool of mid/late-round upside/handcuff targets, built ONLY
    from already-synced local data (rankings ECR, weekly_stats, injuries,
    the cached Sleeper player map's depth_chart_order/years_exp/age) -- see
    the docstring on compute_risk_score for the same local-data-only
    caveat. This is explicitly NOT a substitute for real scouting/beat-
    reporter content (who is actually trending up in camp, a specific
    coach's comments, etc.) -- that lives with the research subagent's web
    access, which this script does not have. Two categories, matching the
    bar the user set (a real path to starting snaps, not just generic bench
    depth, which the ECR/VBD-based draft_pool value model already ranks
    fine on its own):
      - rookie-role: years_exp == 0 AND already depth_chart_order <= 2 (a
        real path via an existing shallow role, not just a name on a
        roster)
      - starter-ahead-risk: a bench RB/WR/TE (currently low ECR-implied
        value, i.e. NOT already a clear starter) sitting directly behind
        (depth_chart_order - 1) a starter who is either aging (>=29) or
        has a real prior-season injury-report history (>=4 flagged weeks)
        -- the "if a specific, identifiable thing breaks their way" bar.
    """
    prior_season = prior_season or (season - 1)
    conn = get_db()
    conn.row_factory = sqlite3.Row
    raw_players = _load_players_raw()

    kept_player_ids = {
        row[0] for row in conn.execute(
            "SELECT player_id FROM keeper_declarations WHERE league_id = ? AND season = ? AND player_id IS NOT NULL",
            (league_id, season),
        ).fetchall()
    }

    rankings_rows = conn.execute(
        "SELECT player_id, position, ecr FROM rankings WHERE season = ? AND ecr_type = 'do' "
        "AND player_id IS NOT NULL AND position IN ('RB','WR','TE')",
        (season,),
    ).fetchall()
    if not rankings_rows:
        raise ValueError(f"no rankings for season {season} -- run sync-rankings first")

    by_pos: dict[str, list[sqlite3.Row]] = {}
    for r in rankings_rows:
        by_pos.setdefault(r["position"], []).append(r)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda r: r["ecr"])
    pos_rank_by_player = {r["player_id"]: (pos, i + 1)
                           for pos, rows in by_pos.items() for i, r in enumerate(rows)}

    by_team_pos: dict[tuple[str, str], list[dict]] = {}
    for pid, p in raw_players.items():
        if not isinstance(p, dict) or not p.get("team") or p.get("position") not in ("RB", "WR", "TE"):
            continue
        by_team_pos.setdefault((p["team"], p["position"]), []).append({**p, "player_id": pid})

    # Rank ceiling keeps this to "plausibly flex-worthy by mid-season" --
    # not just any bench name; rank floor keeps it to genuinely "buried"
    # players the ECR/VBD-based draft_pool value model would already have
    # ranked as an ordinary starter. depth_chart_order is capped at the
    # immediate backup slot (2 or 3) -- a 6th-string WR "behind" a starter
    # is not a real handcuff, just roster filler.
    LATE_RANK_FLOOR = {"RB": 20, "WR": 24, "TE": 10}
    RANK_CEILING = {"RB": 45, "WR": 55, "TE": 18}
    AGING_THRESHOLD = 30

    conn.execute("DELETE FROM upside_targets WHERE league_id = ? AND season = ?", (league_id, season))
    n = 0
    for pid, p in raw_players.items():
        if not isinstance(p, dict) or pid in kept_player_ids:
            continue
        pos = p.get("position")
        if pos not in ("RB", "WR", "TE"):
            continue
        pos_rank_info = pos_rank_by_player.get(pid)
        if not pos_rank_info:
            continue
        _, pos_rank = pos_rank_info
        if pos_rank <= LATE_RANK_FLOOR.get(pos, 20) or pos_rank > RANK_CEILING.get(pos, 60):
            continue

        depth_order = p.get("depth_chart_order")
        years_exp = p.get("years_exp")
        team = p.get("team")
        full_name = p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()

        category = None
        reason = None
        signal = {"pos_rank": pos_rank, "depth_chart_order": depth_order, "years_exp": years_exp}

        if years_exp == 0 and depth_order is not None and depth_order <= 2:
            category = "rookie-role"
            reason = (f"rookie, already depth_chart_order {depth_order} at {team} {pos} "
                      f"despite ECR-implied pos_rank {pos_rank} -- real path via an existing shallow role")
        elif depth_order is not None and 2 <= depth_order <= 3 and team:
            ahead = [q for q in by_team_pos.get((team, pos), [])
                     if q.get("depth_chart_order") == depth_order - 1]
            for a in ahead:
                a_age = a.get("age")
                a_inj_weeks = conn.execute(
                    "SELECT COUNT(*) FROM injuries WHERE player_id = ? AND season = ? AND report_status IS NOT NULL",
                    (a["player_id"], prior_season),
                ).fetchone()[0]
                if a_age and a_age >= AGING_THRESHOLD:
                    category = "aging-starter-ahead"
                    reason = (f"behind {a.get('full_name')} (age {a_age}) on {team} {pos} depth chart "
                              f"(order {depth_order} vs {depth_order-1}) -- real path if age catches up")
                    signal["starter_ahead"] = {"name": a.get("full_name"), "age": a_age}
                    break
                if a_inj_weeks >= 4:
                    category = "injury-prone-starter-ahead"
                    reason = (f"behind {a.get('full_name')} on {team} {pos} depth chart, who had "
                              f"{a_inj_weeks} injury-report weeks in {prior_season} -- real path on next injury")
                    signal["starter_ahead"] = {"name": a.get("full_name"), "injury_weeks_prior_season": a_inj_weeks}
                    break

        if category is None:
            continue

        conn.execute(
            "INSERT INTO upside_targets (league_id, season, player_id, full_name, position, team, "
            "category, reason, signal_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(league_id, season, player_id) DO UPDATE SET "
            "full_name=excluded.full_name, category=excluded.category, reason=excluded.reason, "
            "signal_json=excluded.signal_json, computed_at=excluded.computed_at",
            (league_id, season, pid, full_name, pos, team, category, reason, json.dumps(signal)),
        )
        n += 1
    conn.commit()

    print(f"upside_targets for league {league_id}, season {season}: {n} candidates flagged")
    print("(local-data-only: ECR pos_rank + Sleeper depth_chart_order/age + prior-season injury-report "
          "frequency. depth_chart_order/age reflect Sleeper's player-map SNAPSHOT as of the last sync-players "
          "run, which can lag real camp-battle news. This does NOT include actual beat-reporter/camp-report "
          "scouting content -- that would need the research subagent's web access, which this script does not "
          "have. Treat this as a data-derived shortlist to sanity-check against real news closer to draft day, "
          "not a finished verdict.)")
    for cat in ("rookie-role", "aging-starter-ahead", "injury-prone-starter-ahead"):
        rows = conn.execute(
            "SELECT full_name, position, team, reason FROM upside_targets "
            "WHERE league_id = ? AND season = ? AND category = ? ORDER BY position, full_name",
            (league_id, season, cat),
        ).fetchall()
        if rows:
            print(f"\n-- {cat} ({len(rows)}) --")
            for r in rows:
                print(f"  {r[0]:22} {r[1]:3} {r[2] or '':4} {r[3]}")
    conn.close()


def _grade_from_ratio(ratio: float | None) -> str:
    """avg starter ECR pos_rank / this league's replacement-level pos_rank
    for that position -- lower ratio (better rank, well ahead of
    replacement) grades higher. Same tiering spirit as build-draft-pool's
    ECR-primary value curve, just expressed as a letter instead of a VBD
    number since this tool has no VBD/adj_value machinery of its own."""
    if ratio is None:
        return "?"
    if ratio <= 0.4:
        return "A"
    if ratio <= 0.7:
        return "B"
    if ratio <= 1.0:
        return "C"
    if ratio <= 1.4:
        return "D"
    return "F"


def _snap_trend(conn: sqlite3.Connection, player_id: str, stats_season: int) -> tuple[str, str] | None:
    """Compares a player's most recent game's snap_pct against the average
    of up to 3 prior games this season. Returns (direction, detail) where
    direction is 'up'/'down', or None if there isn't enough recent
    snap_pct data to say anything (never fabricates a trend from < 2
    games)."""
    rows = conn.execute(
        "SELECT week, snap_pct FROM weekly_stats WHERE player_id = ? AND season = ? "
        "AND snap_pct IS NOT NULL ORDER BY week DESC LIMIT 4",
        (player_id, stats_season),
    ).fetchall()
    if len(rows) < 2:
        return None
    last = rows[0][1]
    prior = [r[1] for r in rows[1:]]
    prior_avg = sum(prior) / len(prior)
    if last <= prior_avg - 8:
        return "down", f"snap share down to {last:.0f}% last game vs {prior_avg:.0f}% prior avg"
    if last >= prior_avg + 8:
        return "up", f"snap share up to {last:.0f}% last game vs {prior_avg:.0f}% prior avg"
    return None


def cmd_roster_grade(league_id: str, roster_id: int | None, season: int,
                      draft_id: str | None = None, my_slot: int | None = None) -> None:
    """General-purpose roster evaluator, usable on ANY roster in a league
    (the user's own or an opponent's, for head-to-head prep). In-season/any-time
    tool -- deliberately does NOT require build-draft-pool/draft_pool to
    have been run (unless grading a --draft-id mock roster, see below).
    Built only from already-synced local data (sleeper_rosters, players,
    rankings ECR, weekly_stats, injuries); degrades gracefully and says so
    plainly wherever a needed table is empty for this context, rather than
    crashing or fabricating a grade.

    Per-position grading reuses _replacement_ranks' dedicated-slots-then-
    FLEX-pool walk (same pattern build_draft_pool uses leaguewide) but
    applied to just this roster's own player pool, to see which of a
    roster's own players would actually start at this league's real
    roster_positions, then compares their ECR pos_rank against the
    league-wide replacement-level pos_rank _replacement_ranks computes for
    that slot. ECR-based tiering only -- no VBD/adj_value machinery, this
    doesn't need it.

    draft_id (optional): grade a roster's picks from a standalone Sleeper
    mock/rehearsal draftboard instead of the real league's sleeper_rosters
    -- draft_picks is scoped by draft_id, not roster_id alone, so a mock
    draftboard's picks are otherwise invisible to this command. Same seat
    resolution as draft-live's --draft-id/--my-slot: a mock draft's picks
    carry no real league roster_id, only a draft_slot, so --my-slot (the
    1-teams pick-order seat) is resolved to a roster_id via that draft
    object's own slot_to_roster_id map -- pass --roster-id directly instead
    only if you already know the mock's synthetic roster_id. This league's
    real roster_positions/scoring context still applies unchanged (the
    board a mock draft is rehearsing against is this league's own). Because
    a mock draftboard isn't a real league, "unrostered" for the waiver-
    pickup section falls back to this league's own draft_pool.drafted=0
    (which reflects this specific draft's own picks, IF build-draft-pool
    was (re)run fresh before this draftboard started and draft-live has
    been polling it) rather than cross-referencing sleeper_rosters.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    league_row = conn.execute(
        "SELECT roster_positions, name FROM sleeper_leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    if not league_row:
        raise ValueError(f"no sleeper_leagues row for {league_id} -- run sync-sleeper first")
    roster_positions = json.loads(league_row["roster_positions"])
    league_name = league_row["name"]

    draft_mode = draft_id is not None
    rostered_all: set[str] = set()

    if draft_mode:
        draft = sleeper_get(f"/draft/{draft_id}")
        slot_to_roster = {int(k): v for k, v in (draft.get("slot_to_roster_id") or {}).items()}
        if my_slot is not None:
            resolved = slot_to_roster.get(my_slot)
            if resolved is None:
                raise ValueError(f"--my-slot {my_slot} not found in draft {draft_id}'s slot_to_roster_id "
                                  f"map ({sorted(slot_to_roster.keys())}) -- check the slot number")
            roster_id = resolved
        if roster_id is None:
            raise ValueError("--draft-id requires --my-slot (preferred) or --roster-id to identify which "
                              "mock draftboard seat/roster to grade")
        teams = (draft.get("settings") or {}).get("teams") or len(slot_to_roster) or 0

        pick_rows = conn.execute(
            "SELECT player_id FROM draft_picks WHERE draft_id = ? AND roster_id = ? AND player_id IS NOT NULL",
            (draft_id, roster_id),
        ).fetchall()
        player_ids = [r["player_id"] for r in pick_rows]
        if not player_ids:
            raise ValueError(f"no picks found in draft_picks for draft {draft_id} roster_id {roster_id} -- "
                              f"run draft-live --league-id {league_id} --draft-id {draft_id} against this "
                              f"draft first to sync its picks")
        seat_label = f"seat {my_slot}, " if my_slot is not None else ""
        owner_name = f"{seat_label}roster {roster_id} (mock draft {draft_id})"
    else:
        roster_row = conn.execute(
            "SELECT player_ids, owner_name FROM sleeper_rosters WHERE league_id = ? AND roster_id = ?",
            (league_id, roster_id),
        ).fetchone()
        if not roster_row:
            raise ValueError(f"no roster_id {roster_id} in league {league_id} -- run sync-sleeper first")
        player_ids = json.loads(roster_row["player_ids"]) or []
        owner_name = roster_row["owner_name"] or f"roster {roster_id}"

        all_rosters = conn.execute(
            "SELECT roster_id, player_ids FROM sleeper_rosters WHERE league_id = ?", (league_id,)
        ).fetchall()
        teams = len(all_rosters)
        for r in all_rosters:
            rostered_all.update(json.loads(r["player_ids"]) or [])

    # Draft-mode "who's already spoken for" comes from draft_pool.drafted
    # (this league's own build-draft-pool board, kept in sync per-pick by
    # draft-live's poll) rather than sleeper_rosters -- a mock draftboard
    # isn't a real league so sleeper_rosters wouldn't reflect its picks.
    mock_drafted_pids: set[str] = set()
    mock_pool_note = None
    if draft_mode:
        mock_drafted_pids = {
            row[0] for row in conn.execute(
                "SELECT player_id FROM draft_pool WHERE league_id = ? AND season = ? AND drafted = 1",
                (league_id, season),
            ).fetchall()
        }
        if not mock_drafted_pids:
            mock_pool_note = (f"draft_pool shows zero drafted=1 rows for league {league_id} season {season} -- "
                               f"waiver-pickup suggestions below may include players actually already taken in "
                               f"this mock draft. Re-run build-draft-pool fresh before this draftboard and make "
                               f"sure draft-live has been polling draft {draft_id} to keep drafted flags current.")

    def is_spoken_for(pid: str) -> bool:
        return pid in mock_drafted_pids if draft_mode else pid in rostered_all

    print(f"# Roster Grade -- {owner_name}, {league_name}, season {season}\n")
    if draft_mode:
        print(f"NOTE: graded from mock draft {draft_id}'s draft_picks, not this league's real sleeper_rosters.\n")
    if mock_pool_note:
        print(f"NOTE: {mock_pool_note}\n")

    rankings_rows = conn.execute(
        "SELECT player_id, position, ecr FROM rankings WHERE season = ? AND ecr_type = 'do' "
        "AND player_id IS NOT NULL AND ecr IS NOT NULL",
        (season,),
    ).fetchall()
    if not rankings_rows:
        print(f"No rankings synced for season {season} -- run sync-rankings first. "
              f"Cannot compute ECR-based position grades/strengths/weaknesses without it.")
        conn.close()
        return

    by_pos: dict[str, list[sqlite3.Row]] = {}
    for r in rankings_rows:
        by_pos.setdefault(r["position"], []).append(r)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda r: r["ecr"])
    pos_rank_by_player: dict[str, int] = {}
    ecr_by_player: dict[str, float] = {}
    for pos, rows in by_pos.items():
        for i, r in enumerate(rows):
            pos_rank_by_player[r["player_id"]] = i + 1
            ecr_by_player[r["player_id"]] = r["ecr"]

    dedicated_slots: dict[str, int] = {}
    for slot in roster_positions:
        if slot in ("BN", "FLEX"):
            continue
        dedicated_slots[slot] = dedicated_slots.get(slot, 0) + 1
    flex_slots = roster_positions.count("FLEX")
    replacement_rank = _replacement_ranks(rankings_rows, roster_positions, teams=teams)

    # Determine which season's weekly_stats/injuries to source recency
    # signals (snap trend, drop candidates, waiver adds) from -- falls
    # back a season if the target season hasn't been played/synced yet
    # (e.g. preseason, before sync-stats has run for it), and says so.
    stats_note = None
    this_season_rows = conn.execute("SELECT COUNT(*) FROM weekly_stats WHERE season = ?", (season,)).fetchone()[0]
    stats_season = season
    if this_season_rows == 0:
        stats_season = season - 1
        fallback_rows = conn.execute("SELECT COUNT(*) FROM weekly_stats WHERE season = ?", (stats_season,)).fetchone()[0]
        if fallback_rows == 0:
            stats_note = (f"No weekly_stats synced for season {season} or {stats_season} -- run sync-stats. "
                           f"Snap-share trend / drop-candidate signals below are skipped entirely.")
            stats_season = None
        else:
            stats_note = (f"No weekly_stats synced yet for season {season} (season hasn't started or "
                           f"sync-stats hasn't run) -- recent-performance/snap-trend signals below use "
                           f"season {stats_season} instead.")
    if stats_note:
        print(f"NOTE: {stats_note}\n")

    raw_players: dict = {}
    try:
        raw_players = _load_players_raw()
    except ValueError:
        pass  # depth_chart_order signal just gets skipped below

    roster_players = []
    for pid in player_ids:
        prow = conn.execute("SELECT full_name, position, team FROM players WHERE player_id = ?", (pid,)).fetchone()
        pos = prow["position"] if prow else None
        roster_players.append({
            "player_id": pid,
            "full_name": prow["full_name"] if prow else pid,
            "position": pos,
            "team": prow["team"] if prow else None,
            "ecr": ecr_by_player.get(pid),
            "pos_rank": pos_rank_by_player.get(pid),
        })

    DISPLAY_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]

    # -- Assign this roster's own players to their real starter slots by
    # ECR, same dedicated-then-FLEX walk _replacement_ranks uses leaguewide,
    # just scoped to this one roster's own player pool.
    starter_assignment: dict[str, list[dict]] = {}
    used_pids: set[str] = set()
    for pos in DISPLAY_ORDER:
        count = dedicated_slots.get(pos, 0)
        if count == 0:
            continue
        candidates = sorted(
            [p for p in roster_players if p["position"] == pos],
            key=lambda p: p["pos_rank"] if p["pos_rank"] is not None else 9999,
        )
        chosen = candidates[:count]
        starter_assignment[pos] = chosen
        used_pids.update(p["player_id"] for p in chosen)

    flex_pool = sorted(
        [p for p in roster_players if p["position"] in ("RB", "WR", "TE") and p["player_id"] not in used_pids],
        key=lambda p: p["pos_rank"] if p["pos_rank"] is not None else 9999,
    )
    flex_chosen = flex_pool[:flex_slots]
    used_pids.update(p["player_id"] for p in flex_chosen)

    bench = [p for p in roster_players if p["player_id"] not in used_pids]

    print("## Per-Position Grades\n")
    grades: dict[str, tuple[str, float | None]] = {}  # pos -> (grade, ratio)
    for pos in DISPLAY_ORDER:
        count = dedicated_slots.get(pos, 0)
        if count == 0:
            continue
        chosen = starter_assignment.get(pos, [])
        if len(chosen) < count:
            missing = count - len(chosen)
            print(f"- **{pos}: F** -- only {len(chosen)} of {count} required {pos} slot(s) rostered "
                  f"({missing} unfilled) -- real roster hole, needs a waiver add.")
            grades[pos] = ("F", None)
            continue
        ranks = [p["pos_rank"] for p in chosen if p["pos_rank"] is not None]
        if not ranks:
            names = ", ".join(p["full_name"] for p in chosen)
            print(f"- **{pos}: ?** -- {names} -- no ECR match for these players in the rankings table, can't grade.")
            grades[pos] = ("?", None)
            continue
        avg_rank = sum(ranks) / len(ranks)
        replacement = replacement_rank.get(pos)
        ratio = (avg_rank / replacement) if replacement else None
        grade = _grade_from_ratio(ratio)
        names = ", ".join(f"{p['full_name']} (ECR #{p['pos_rank']})" for p in chosen)
        print(f"- **{pos}: {grade}** -- {names} -- avg starter pos_rank {avg_rank:.1f} vs this league's "
              f"~{pos} replacement level (rank {replacement})")
        grades[pos] = (grade, ratio)

    if flex_slots:
        ratios = [
            p["pos_rank"] / replacement_rank[p["position"]]
            for p in flex_chosen
            if p["pos_rank"] is not None and replacement_rank.get(p["position"])
        ]
        if len(flex_chosen) < flex_slots:
            print(f"- **FLEX depth: F** -- only {len(flex_chosen)} of {flex_slots} FLEX slot(s) fillable "
                  f"from this roster's own RB/WR/TE pool -- real depth hole.")
            grades["FLEX"] = ("F", None)
        elif not ratios:
            print("- **FLEX depth: ?** -- no ECR match for FLEX-slot players, can't grade.")
            grades["FLEX"] = ("?", None)
        else:
            avg_ratio = sum(ratios) / len(ratios)
            grade = _grade_from_ratio(avg_ratio)
            names = ", ".join(f"{p['full_name']} ({p['position']}, ECR #{p['pos_rank']})" for p in flex_chosen)
            print(f"- **FLEX depth: {grade}** -- {names}")
            grades["FLEX"] = (grade, avg_ratio)

    strengths = sorted(
        [(pos, r) for pos, (g, r) in grades.items() if g in ("A", "B") and r is not None], key=lambda x: x[1]
    )[:3]
    weaknesses = sorted(
        [(pos, r) for pos, (g, r) in grades.items() if g in ("D", "F")], key=lambda x: (x[1] is None, x[1] or 0),
        reverse=True,
    )[:3]

    print("\n## Team Strengths\n")
    if strengths:
        for pos, r in strengths:
            print(f"- {pos} (grade {grades[pos][0]}, {r:.2f}x replacement-level ECR rank)")
    else:
        print("- None clearly above replacement level at any graded position.")

    print("\n## Team Weaknesses\n")
    if weaknesses:
        for pos, r in weaknesses:
            ratio_str = f"{r:.2f}x replacement-level ECR rank" if r is not None else "unfilled/ungraded"
            print(f"- {pos} (grade {grades[pos][0]}, {ratio_str})")
    else:
        print("- None clearly below replacement level at any graded position.")

    # -- Waiver watch list: genuine drop/monitor candidates on the bench,
    # not just "worst player on roster".
    print("\n## Waiver Watch List (bench drop/monitor candidates)\n")
    watch = []
    for p in bench:
        if not p["position"]:
            continue
        reasons = []
        replacement = replacement_rank.get(p["position"])
        if p["pos_rank"] is not None and replacement and p["pos_rank"] > replacement * 1.5:
            reasons.append(f"ECR pos_rank {p['pos_rank']} well below this league's ~{p['position']} "
                            f"replacement level (rank {replacement})")
        if stats_season is not None:
            trend = _snap_trend(conn, p["player_id"], stats_season)
            if trend and trend[0] == "down":
                reasons.append(trend[1])
        depth_order = (raw_players.get(p["player_id"]) or {}).get("depth_chart_order")
        if depth_order and depth_order >= 3:
            reasons.append(f"buried on depth chart (order {depth_order})")
        if reasons:
            watch.append((p, reasons))
    if watch:
        for p, reasons in watch:
            print(f"- {p['full_name']} ({p['position']}, {p['team'] or '?'}): " + "; ".join(reasons))
    else:
        print("- No bench players currently flag as genuine drop/monitor candidates from local data.")

    # -- Waiver pickup suggestions: currently-unrostered players in this
    # league only, similar ECR/snap-trend logic in the opposite direction.
    print("\n## Waiver Pickup Suggestions (currently unrostered in this league)\n" if not draft_mode
          else "\n## Waiver Pickup Suggestions (currently undrafted in this mock draft)\n")
    weak_positions = {pos for pos, (g, _r) in grades.items() if g in ("D", "F")}
    value_adds: list[tuple[dict, str]] = []
    for r in rankings_rows:
        pid = r["player_id"]
        if is_spoken_for(pid):
            continue
        slot_pos = "DEF" if r["position"] == "DST" else r["position"]
        if slot_pos not in DISPLAY_ORDER and slot_pos not in ("RB", "WR", "TE"):
            continue
        replacement = replacement_rank.get(slot_pos)
        rank = pos_rank_by_player.get(pid)
        if not replacement or rank is None or rank > replacement + 5:
            continue
        prow = conn.execute("SELECT full_name, team FROM players WHERE player_id = ?", (pid,)).fetchone()
        name = prow["full_name"] if prow else str(pid)
        team = prow["team"] if prow else None
        note = f"ECR #{rank} {slot_pos} (this league's replacement level: {replacement})"
        if slot_pos in weak_positions:
            note += " -- fills a roster weakness"
        value_adds.append(({"full_name": name, "position": slot_pos, "team": team, "player_id": pid, "rank": rank}, note))
    value_adds.sort(key=lambda x: x[0]["rank"])
    if value_adds:
        for p, note in value_adds[:10]:
            print(f"- {p['full_name']} ({p['position']}, {p['team'] or '?'}): {note}")
    else:
        print("- No unrostered players in this league currently graded at/near replacement level.")

    if stats_season is not None:
        trending_up = []
        rostered_pids = {p["player_id"] for p in roster_players}
        for r in rankings_rows:
            pid = r["player_id"]
            if is_spoken_for(pid) or pid in rostered_pids:
                continue
            slot_pos = "DEF" if r["position"] == "DST" else r["position"]
            if slot_pos not in ("QB", "RB", "WR", "TE"):
                continue
            trend = _snap_trend(conn, pid, stats_season)
            if trend and trend[0] == "up":
                prow = conn.execute("SELECT full_name, team FROM players WHERE player_id = ?", (pid,)).fetchone()
                name = prow["full_name"] if prow else pid
                team = prow["team"] if prow else None
                trending_up.append((name, slot_pos, team, trend[1]))
        if trending_up:
            print("\n### Trending Up (snap share), Unrostered\n")
            for name, pos, team, detail in trending_up[:5]:
                print(f"- {name} ({pos}, {team or '?'}): {detail}")

    conn.close()


def cmd_build_draft_pool(league_id: str, season: int, prior_season: int | None,
                          skip_keeper_check: bool = False) -> None:
    """Build the VBD-adjusted draft board for one league.

    skip_keeper_check: bypasses the per-roster "must have >=3 declared
    keepers" hard-fail below entirely. FOR PRE-DECLARATION MOCK-DRAFT
    REHEARSAL/DRY-RUNS ONLY (declarations aren't due until 8/24) — replaces
    the old manual workaround of inserting throwaway NULL-player_id rows
    into keeper_declarations and deleting them after. Any real declarations
    that do already exist are still honored (still excluded from the pool
    below) — this flag only lifts the hard-fail, it doesn't force-zero
    exclusions. NEVER pass this for a real draft: without the check, a
    roster with incomplete declarations could see a teammate's actual
    keeper still sitting in the pool as if undrafted.
    """
    prior_season = prior_season or (season - 1)
    conn = get_db()
    conn.row_factory = sqlite3.Row

    league_row = conn.execute(
        "SELECT roster_positions FROM sleeper_leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    if not league_row:
        raise ValueError(f"no sleeper_leagues row for {league_id} — run sync-sleeper first")
    roster_positions = json.loads(league_row["roster_positions"])

    # Keepers must already be fully declared — draft_history.keeper_eligible
    # is last year's broader "could be kept" pool, not this year's actual
    # picks, so a short keeper_declarations count means declarations aren't
    # synced yet, not that a team only kept fewer players. --skip-keeper-check
    # (mock-draft rehearsal only, see docstring) bypasses this hard-fail.
    if skip_keeper_check:
        print("--skip-keeper-check set: skipping the >=3-declared-keepers hard-fail "
              "(mock-draft rehearsal mode — never use this for a real draft)")
    else:
        rosters = conn.execute("SELECT roster_id FROM sleeper_rosters WHERE league_id = ?", (league_id,)).fetchall()
        for r in rosters:
            n_declared = conn.execute(
                "SELECT COUNT(*) FROM keeper_declarations WHERE league_id = ? AND roster_id = ? AND season = ?",
                (league_id, r["roster_id"], season),
            ).fetchone()[0]
            if n_declared < 3:
                raise ValueError(
                    f"roster {r['roster_id']} in league {league_id} has only {n_declared} declared keepers "
                    f"for season {season} (expected 3) — run keepers-declare for every team first, or the "
                    f"draft pool will wrongly include an already-kept player (or pass --skip-keeper-check "
                    f"for a pre-declaration mock-draft rehearsal only)"
                )

    kept_player_ids = {
        row[0] for row in conn.execute(
            "SELECT player_id FROM keeper_declarations WHERE league_id = ? AND season = ? AND player_id IS NOT NULL",
            (league_id, season),
        ).fetchall()
    }

    rankings_rows = conn.execute(
        "SELECT player_id, position, ecr FROM rankings "
        "WHERE season = ? AND ecr_type = 'do' AND player_id IS NOT NULL",
        (season,),
    ).fetchall()
    if not rankings_rows:
        raise ValueError(f"no rankings for season {season} — run sync-rankings first")

    scoring_factor = _league_scoring_factor(conn, league_id, prior_season)
    replacement_rank = _replacement_ranks(rankings_rows, roster_positions)

    # PRIMARY value signal: the player's own cross-position dynasty-overall
    # ECR (verified live against real data — it's a genuine single ordered
    # list, e.g. Josh Allen lands ~rank 22, not top-5, exactly where real
    # drafters take QBs in a 1-QB league). An earlier version of this
    # function used a per-position points curve (built from real prior-
    # season stats) as the primary driver instead, which badly overvalued
    # QB/K: raw seasonal point totals are inflated for those positions
    # relative to how little real strategic value an early pick actually
    # has there (there's always a decent streamable option), and a mock-
    # draft retrospective test caught it recommending a kicker by pick 68.
    # ECR already encodes that real positional-scarcity judgment (it's a
    # human/algorithmic consensus across real leagues) — value_from_ecr
    # is a smooth decay purely over that rank. _league_scoring_factor
    # (real prior-season stats scored under this league's own
    # scoring_settings vs generic PPR) is now only a secondary multiplier
    # reflecting how this league's specific bonus categories move a
    # position's value, clipped to [0.85, 1.20] so it can nudge but never
    # dominate or invert the ECR-driven primary signal.
    def value_from_ecr(ecr: float) -> float:
        return 1000.0 / (ecr + 9.0)

    by_position: dict[str, list[sqlite3.Row]] = {}
    for r in rankings_rows:
        by_position.setdefault(r["position"], []).append(r)

    # Secondary DST nudge: most-recently-computed schedule-strength rank
    # (cmd_dst_schedule_strength — the user's rule is upcoming-schedule over
    # raw season-long defensive quality). Optional signal — if it hasn't
    # been run yet, DST value falls back to plain ECR, same as every other
    # position, rather than hard-failing the whole draft pool over it.
    dst_rank_row = conn.execute(
        "SELECT team, schedule_rank FROM dst_schedule_strength WHERE season = ? "
        "ORDER BY as_of_week ASC, computed_at DESC", (season,),
    ).fetchall()
    dst_rank: dict[str, int] = {}
    for team, rank in dst_rank_row:
        dst_rank.setdefault(team, rank)  # keep the earliest as_of_week's ranking (draft-day default)
    n_dst_teams = len(dst_rank)

    raw_players = _load_players_raw()

    pool_rows = []
    for pos, rows in by_position.items():
        rows_sorted = sorted(rows, key=lambda r: r["ecr"])
        repl_rank = replacement_rank.get(pos)
        repl_ecr = rows_sorted[repl_rank - 1]["ecr"] if repl_rank and repl_rank <= len(rows_sorted) else None
        repl_value = value_from_ecr(repl_ecr) if repl_ecr is not None else None
        factor = scoring_factor.get(pos, 1.0)
        for i, r in enumerate(rows_sorted, start=1):
            if r["player_id"] in kept_player_ids:
                continue
            value = value_from_ecr(r["ecr"])
            adj_value = (value - repl_value if repl_value is not None else value) * factor
            if pos == "DST" and n_dst_teams > 1 and r["player_id"] in dst_rank:
                rank = dst_rank[r["player_id"]]
                # rank 1 (easiest upcoming stretch) -> 1.15x, rank n_dst_teams (toughest) -> 0.85x, linear
                sched_factor = 1.15 - (rank - 1) * (0.30 / (n_dst_teams - 1))
                adj_value *= sched_factor
            risk_score, risk_note = compute_risk_score(conn, r["player_id"], pos, prior_season, raw_players)
            pool_rows.append({
                "player_id": r["player_id"], "position": pos, "ecr": r["ecr"],
                "pos_rank": i, "replacement_rank": repl_rank,
                "est_points": round(value, 2), "adj_value": round(adj_value, 2),
                "risk_score": risk_score, "risk_note": risk_note,
            })

    # Tier breaks on adj_value (cross-position — that's the point of VBD
    # over raw ECR), not per-position: a new tier starts when the gap to
    # the next player exceeds a rolling (window-10) mean + 1.5x stdev of
    # consecutive gaps, minimum tier size 3 to avoid noise-driven
    # single-player tiers.
    pool_rows.sort(key=lambda r: r["adj_value"], reverse=True)
    tier = 1
    gap_window: list[float] = []
    for idx, row in enumerate(pool_rows):
        row["tier"] = tier
        if idx + 1 < len(pool_rows):
            gap = row["adj_value"] - pool_rows[idx + 1]["adj_value"]
            gap_window.append(gap)
            gap_window = gap_window[-10:]
            if len(gap_window) >= 3:
                mean = sum(gap_window) / len(gap_window)
                stdev = (sum((g - mean) ** 2 for g in gap_window) / len(gap_window)) ** 0.5
                tier_size = sum(1 for r2 in pool_rows if r2.get("tier") == tier)
                if gap > mean + 1.5 * stdev and tier_size >= 3:
                    tier += 1

    conn.execute("DELETE FROM draft_pool WHERE league_id = ? AND season = ?", (league_id, season))
    for row in pool_rows:
        conn.execute(
            "INSERT INTO draft_pool (league_id, season, player_id, position, ecr, pos_rank, "
            "replacement_rank, est_points, adj_value, tier, risk_score, risk_note, drafted, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
            (league_id, season, row["player_id"], row["position"], row["ecr"], row["pos_rank"],
             row["replacement_rank"], row["est_points"], row["adj_value"], row["tier"],
             row["risk_score"], row["risk_note"]),
        )
    conn.commit()
    conn.close()
    print(f"built draft_pool for league {league_id}, season {season}: {len(pool_rows)} available players "
          f"({len(kept_player_ids)} keepers excluded), {tier} tiers "
          f"(points curve from season {prior_season}"
          f"{', DST nudged by dst_schedule_strength' if n_dst_teams > 1 else ''})")


def _next_pick_roster(draft: dict, num_picks_made: int) -> int | None:
    """Which roster_id picks next, from Sleeper's own draft object — avoids
    guessing at snake order by hand for every call."""
    slot_to_roster = {int(k): v for k, v in (draft.get("slot_to_roster_id") or {}).items()}
    teams = draft.get("settings", {}).get("teams") or len(slot_to_roster)
    if not teams:
        return None
    rnd, pos_in_round = divmod(num_picks_made, teams)
    is_snake = draft.get("type") == "snake"
    slot = (teams - pos_in_round) if (is_snake and rnd % 2 == 1) else (pos_in_round + 1)
    return slot_to_roster.get(slot)


def _print_recommendation(conn: sqlite3.Connection, league_id: str, season: int, roster_id: int,
                           roster_positions: list[str], draft_id: str, track_roster_id: int,
                           top_n: int = 10, pos_run_counts: dict[str, int] | None = None,
                           run_window: int = 5) -> None:
    """Need-weighted top-N from draft_pool: boost adj_value for positions
    where this roster's own starter slots (keepers + picks made so far in
    this draft) are not yet filled, using the league's real roster_positions.
    A signal to inform the user's pick, never an auto-decided one.

    roster_id vs track_roster_id: normally identical (the real Sleeper
    roster_id, both for pulling this team's keeper_declarations and for
    matching its own picks in the polled draft). They diverge in
    --draft-id/--my-slot mock-draft mode: a standalone Sleeper mock
    draftboard has no real league behind it, so its picks carry a
    draft-local synthetic roster_id (via slot_to_roster_id, keyed off
    --my-slot's draft_slot) that has nothing to do with the real league's
    roster numbering — roster_id stays the real Huddle Buddies roster (for
    real keeper_declarations lookups), track_roster_id is the synthetic
    in-draft id (for matching picks actually made in *this* draft_id).

    Two additions on top of the base need-boost (both from live mock-draft
    learnings in fantasy.md's Draft strategy notes section -- previously
    documented only, not enforced in code, which decision #6 flagged as too
    weak on its own):
      - a soft 2nd-QB value nudge: the plain starter-slot need-boost never
        flags a 2nd QB as a need once the single starting QB slot is filled,
        so a real late-round-value 2nd QB could get buried under other
        similarly-ranked bench filler. Once this roster is down to its last
        few picks and still holds only one QB, a small adj_value boost is
        applied to available QBs so a 2nd QB surfaces as a flagged option in
        the printed list -- it is NOT force-recommended over a genuinely
        better-value pick at another position (real 2025-season data showed
        both leagues' waiver wire never actually ran dry on streamable QBs,
        so the old hard scarcity-driven override wasn't supported by the
        data; this is a late-round-value suggestion instead, per the user's
        call).
      - a risk-aware adjustment: a high-risk/high-upside pick (risk_score
        from compute_risk_score) is only preferred once the roster need it
        would fill is already covered by a safer earlier pick; a genuinely
        open starter need prefers the safer option at a similar adj_value.
      - a positional-run nudge: pos_run_counts is poll_once's own last-5-picks
        tally (the same dict backing its printed "SIGNAL: N of the last 5
        picks were {pos}" line) passed straight through rather than
        recomputed here. When a position hits that same run threshold
        (count >= 3) AND is still an open starter need for this roster, its
        available players get a small adj_value nudge (RUN_NEED_NUDGE) --
        a run on a position you actually need to fill is a mild signal the
        position may thin out before your next turn, same "nudge not
        override" spirit as QB2_NUDGE. A run on a position that is NOT a
        current need does not touch scoring at all -- it only prints an
        informational note (e.g. a QB run while your QB slot is already
        filled) since it's more relevant to later-round planning (like the
        QB2 value window) than to this pick.
    """
    start_slots = [p for p in roster_positions if p != "BN"]

    owned_positions: list[str] = []
    for row in conn.execute(
        "SELECT player_id FROM keeper_declarations WHERE league_id = ? AND roster_id = ? AND season = ? "
        "AND player_id IS NOT NULL", (league_id, roster_id, season),
    ).fetchall():
        prow = conn.execute("SELECT position FROM players WHERE player_id = ?", (row[0],)).fetchone()
        if prow and prow[0]:
            owned_positions.append(prow[0])
    for row in conn.execute(
        "SELECT player_id FROM draft_picks WHERE draft_id = ? AND roster_id = ? AND player_id IS NOT NULL",
        (draft_id, track_roster_id),
    ).fetchall():
        prow = conn.execute("SELECT position FROM players WHERE player_id = ?", (row[0],)).fetchone()
        if prow and prow[0]:
            owned_positions.append(prow[0])

    remaining_slots = list(start_slots)
    for pos in owned_positions:
        for i, slot in enumerate(remaining_slots):
            if _slot_eligible(pos, slot):
                remaining_slots.pop(i)
                break
    unfilled_positions = {s for s in remaining_slots if s != "FLEX"}
    has_open_flex = "FLEX" in remaining_slots

    total_roster_slots = len(roster_positions)  # includes BN -- this teams full draftable capacity
    picks_made = len(owned_positions)
    remaining_own_picks = max(total_roster_slots - picks_made, 0)
    owned_qb_count = owned_positions.count("QB")
    LAST_ROUNDS_FOR_QB2 = 3
    qb2_value_window = owned_qb_count < 2 and 0 < remaining_own_picks <= LAST_ROUNDS_FOR_QB2

    available = conn.execute(
        "SELECT player_id, position, adj_value, tier, risk_score, risk_note FROM draft_pool "
        "WHERE league_id = ? AND season = ? AND drafted = 0 ORDER BY adj_value DESC",
        (league_id, season),
    ).fetchall()

    HIGH_RISK = 65.0
    QB2_NUDGE = 1.1  # soft late-round value suggestion, not a hard override -- see docstring
    RUN_NEED_NUDGE = 1.15  # positional run on an open need -- same nudge family as QB2_NUDGE
    RUN_THRESHOLD = 3  # matches poll_once's own SIGNAL threshold (count >= 3 of last `run_window` picks)
    pos_run_counts = pos_run_counts or {}
    run_positions = {pos for pos, cnt in pos_run_counts.items() if cnt >= RUN_THRESHOLD}
    run_need_positions = run_positions & unfilled_positions
    run_other_positions = run_positions - unfilled_positions
    scored = []
    for r in available:
        boost = 1.0
        is_need = r["position"] in unfilled_positions
        risk = r["risk_score"]
        if is_need:
            boost = 1.25
            if risk is not None and risk >= HIGH_RISK:
                boost *= 0.85
        elif has_open_flex and r["position"] in ("RB", "WR", "TE"):
            boost = 1.1
            if risk is not None and risk >= HIGH_RISK:
                boost *= 0.85
        else:
            if risk is not None and risk >= HIGH_RISK:
                boost *= 1.05
        if qb2_value_window and r["position"] == "QB":
            boost *= QB2_NUDGE
        if r["position"] in run_need_positions:
            boost *= RUN_NEED_NUDGE
        scored.append((r["adj_value"] * boost, r))
    scored.sort(key=lambda x: x[0], reverse=True)

    print(f"  open starter slots: {remaining_slots or 'none -- bench/depth only'}")
    print(f"  roster capacity: {picks_made}/{total_roster_slots} filled, QBs owned: {owned_qb_count}")

    for pos in sorted(run_other_positions):
        cnt = pos_run_counts[pos]
        extra = ""
        if pos == "QB" and owned_qb_count < 2:
            extra = " -- your 2nd-QB nudge may need to happen earlier than planned"
        print(f"  note: {pos} run in progress ({cnt}/{run_window}) -- not an open need right now"
              f"{extra}")

    if qb2_value_window:
        qb_candidates = [r for r in available if r["position"] == "QB"]
        if qb_candidates:
            best_qb = max(qb_candidates, key=lambda r: r["adj_value"])
            prow = conn.execute(
                "SELECT full_name FROM players WHERE player_id = ?", (best_qb["player_id"],)
            ).fetchone()
            name = prow[0] if prow else best_qb["player_id"]
            print(f"  --- late-round value suggestion: only {owned_qb_count} QB owned with "
                  f"{remaining_own_picks} pick(s) left -- {name} (adj_value {best_qb['adj_value']:.1f}) "
                  f"is worth a look for a 2nd QB, but not forced over a better-value pick elsewhere ---")

    for score, r in scored[:top_n]:
        prow = conn.execute("SELECT full_name FROM players WHERE player_id = ?", (r["player_id"],)).fetchone()
        name = prow[0] if prow else r["player_id"]
        need_flag = " [NEED]" if r["position"] in unfilled_positions else ""
        risk_flag = (f" [RISK {r['risk_score']:.0f}: {r['risk_note']}]"
                     if r["risk_score"] is not None and r["risk_score"] >= HIGH_RISK else "")
        qb2_flag = " [2nd-QB value]" if qb2_value_window and r["position"] == "QB" and owned_qb_count < 2 else ""
        run_flag = " [RUN]" if r["position"] in run_need_positions else ""
        print(f"    {name:25} {r['position'] or '?':4} tier {r['tier']:<3} adj_value {r['adj_value']:6.1f}"
              f"{need_flag}{risk_flag}{qb2_flag}{run_flag}")


def cmd_draft_live(league_id: str, roster_id: int | None, poll_seconds: int, watch: bool,
                    draft_id: str | None = None, my_slot: int | None = None) -> None:
    """Poll a Sleeper draft's live picks and, when it's your turn, print a
    need-weighted recommendation from build-draft-pool's board.

    Two distinct draft targets, both share the same league's board:
      - Real draft (default): --league-id alone auto-resolves the league's
        own draft_id, and "your seat" is the real Sleeper roster_id (from
        --roster-id or SLEEPER_USER_ID). Use this for the actual draft.
      - Mock/rehearsal draft (--draft-id + --my-slot): polls an arbitrary
        standalone Sleeper mock draftboard (its own sandbox draft object,
        league_id=null on Sleeper's side, created via sleeper.com/draftboards)
        instead of the real league's draft. FOR DRY-RUN REHEARSAL ONLY, never
        for a real draft. A mock draftboard's picks carry no real roster_id
        (Sleeper leaves it null), only a draft_slot (pick-order column,
        1-teams) — --my-slot N tells this command which draft_slot is yours
        so it can track "my picks in this draft" and detect "your pick next"
        correctly even though the numbers aren't real Huddle Buddies
        roster_ids. The value board itself (roster_positions, keeper set,
        adj_value/tiers) still comes from --league-id's own build-draft-pool
        output — only the live-picks polling target changes.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row

    league = sleeper_get(f"/league/{league_id}")
    if draft_id is None:
        if my_slot is not None:
            raise ValueError("--my-slot only applies with --draft-id (mock/rehearsal mode) — "
                              "a real draft auto-resolves your seat from --roster-id/SLEEPER_USER_ID")
        draft_id = league.get("draft_id")
        if not draft_id:
            raise ValueError(f"league {league_id} has no draft_id on record yet")
    season = int(league.get("season"))
    league_row = conn.execute(
        "SELECT roster_positions FROM sleeper_leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    if not league_row:
        raise ValueError(f"no sleeper_leagues row for {league_id} — run sync-sleeper first")
    roster_positions = json.loads(league_row["roster_positions"])

    if roster_id is None:
        user_id = os.environ.get("SLEEPER_USER_ID")
        if not user_id:
            raise ValueError("no --roster-id given and SLEEPER_USER_ID is not set in .env")
        r = conn.execute(
            "SELECT roster_id FROM sleeper_rosters WHERE league_id = ? AND owner_id = ?",
            (league_id, user_id),
        ).fetchone()
        if not r:
            raise ValueError(f"no roster in league {league_id} owned by SLEEPER_USER_ID {user_id}")
        roster_id = r["roster_id"]

    # track_roster_id: which id identifies "my seat" *within the polled
    # draft's own pick objects* — normally identical to roster_id (real
    # draft), but resolved from --my-slot's draft_slot via the mock draft's
    # own slot_to_roster_id map when in mock mode (see docstring above).
    track_roster_id = roster_id

    def poll_once() -> bool:
        nonlocal track_roster_id
        draft = sleeper_get(f"/draft/{draft_id}", bust_cache=True)
        picks = sleeper_get(f"/draft/{draft_id}/picks", bust_cache=True)
        picks.sort(key=lambda p: p["pick_no"])

        # A mock draft's pick objects carry roster_id=null (no real league
        # roster behind them) — fall back to the draft's own slot_to_roster_id
        # map via draft_slot, which is always populated. Harmless no-op for a
        # real league draft, where pick.roster_id is already set directly.
        slot_to_roster = {int(k): v for k, v in (draft.get("slot_to_roster_id") or {}).items()}
        for p in picks:
            if p.get("roster_id") is None:
                p["roster_id"] = slot_to_roster.get(p.get("draft_slot"))

        if my_slot is not None:
            resolved = slot_to_roster.get(my_slot)
            if resolved is None:
                raise ValueError(f"--my-slot {my_slot} not found in draft {draft_id}'s slot_to_roster_id "
                                  f"map ({sorted(slot_to_roster.keys())}) — check the slot number")
            track_roster_id = resolved

        known_pick_nos = {
            row[0] for row in conn.execute(
                "SELECT pick_no FROM draft_picks WHERE draft_id = ?", (draft_id,)
            ).fetchall()
        }
        new_picks = [p for p in picks if p["pick_no"] not in known_pick_nos]

        for p in picks:
            conn.execute(
                "INSERT INTO draft_picks (draft_id, league_id, pick_no, round, roster_id, player_id, picked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(draft_id, pick_no) DO UPDATE SET "
                "round=excluded.round, roster_id=excluded.roster_id, player_id=excluded.player_id",
                (draft_id, league_id, p["pick_no"], p.get("round"), p.get("roster_id"), p.get("player_id")),
            )
            if p.get("player_id"):
                conn.execute(
                    "UPDATE draft_pool SET drafted = 1 WHERE league_id = ? AND season = ? AND player_id = ?",
                    (league_id, season, p["player_id"]),
                )
        conn.commit()

        if new_picks:
            print(f"--- {len(new_picks)} new pick(s) ---")
            for p in new_picks:
                prow = conn.execute(
                    "SELECT full_name, position FROM players WHERE player_id = ?", (p.get("player_id"),)
                ).fetchone()
                name = prow["full_name"] if prow else p.get("player_id")
                pos = prow["position"] if prow else "?"
                print(f"  pick {p['pick_no']:3} (R{p.get('round')}) roster {p.get('roster_id')}: {name} ({pos})")
        else:
            print(f"no new picks since last check ({len(picks)} total so far)")

        recent = picks[-5:]
        pos_counts: dict[str, int] = {}
        for p in recent:
            prow = conn.execute("SELECT position FROM players WHERE player_id = ?", (p.get("player_id"),)).fetchone()
            pos = prow["position"] if prow else None
            if pos:
                pos_counts[pos] = pos_counts.get(pos, 0) + 1
        for pos, cnt in pos_counts.items():
            if cnt >= 3:
                print(f"  SIGNAL: {cnt} of the last {len(recent)} picks were {pos} — positional run")

        next_roster = _next_pick_roster(draft, len(picks))
        if next_roster == track_roster_id:
            slot_note = f', draft-slot {my_slot} / in-draft id {track_roster_id}' if my_slot is not None else ''
            print(f"\n>>> YOUR PICK (roster {roster_id}{slot_note}) <<<")
            _print_recommendation(conn, league_id, season, roster_id, roster_positions, draft_id, track_roster_id,
                                   pos_run_counts=pos_counts, run_window=len(recent))

        return draft.get("status") == "complete"

    if watch:
        while True:
            done = poll_once()
            if done:
                print("draft complete.")
                break
            time.sleep(poll_seconds)
    else:
        poll_once()

    conn.close()


DRAFT_STRATEGY_SYSTEM = (
    "You write pre-draft fantasy football strategy guidance from a JSON "
    "draft board for one league. The JSON's 'tiers' list (tier number, "
    "players with position/pos_rank/adj_value) is the FINAL, ALREADY-"
    "COMPUTED value-over-replacement board — a deterministic calculation "
    "already decided these tiers and rankings from this league's actual "
    "scoring_settings and roster_positions (both included in the JSON). "
    "Your job is only to NARRATE the scarcity/tier picture and give "
    "general round-by-round strategy guidance (e.g. when a position's "
    "tier cliff falls, whether to prioritize RB/WR early given this "
    "league's roster shape), never to re-rank or override a player's tier "
    "or adj_value. Do NOT invent any player, stat, or value not present in "
    "the JSON. Output markdown."
)


def cmd_draft_strategy(league_id: str, season: int, model: str, force: bool) -> None:
    conn = get_db()
    conn.row_factory = sqlite3.Row

    existing = conn.execute(
        "SELECT content FROM reports WHERE season = ? AND week = 0 AND league_id = ?",
        (season, league_id),
    ).fetchone()
    if existing and not force:
        print(f"--- draft strategy, league {league_id} ({season}) [cached, use --force to regenerate] ---")
        print(existing[0])
        conn.close()
        return

    league_row = conn.execute(
        "SELECT name, roster_positions, scoring_settings FROM sleeper_leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    if not league_row:
        raise ValueError(f"no sleeper_leagues row for {league_id} — run sync-sleeper first")

    pool = conn.execute(
        "SELECT dp.position, dp.pos_rank, dp.adj_value, dp.tier, p.full_name "
        "FROM draft_pool dp JOIN players p ON p.player_id = dp.player_id "
        "WHERE dp.league_id = ? AND dp.season = ? AND dp.drafted = 0 "
        "ORDER BY dp.tier, dp.adj_value DESC",
        (league_id, season),
    ).fetchall()
    if not pool:
        raise ValueError(f"no draft_pool for league {league_id}, season {season} — run build-draft-pool first")

    tiers: dict[int, list[dict]] = {}
    for r in pool:
        tiers.setdefault(r["tier"], []).append({
            "name": r["full_name"], "position": r["position"],
            "pos_rank": r["pos_rank"], "adj_value": r["adj_value"],
        })
    payload = json.dumps({
        "league_name": league_row["name"],
        "roster_positions": json.loads(league_row["roster_positions"]),
        "scoring_settings": json.loads(league_row["scoring_settings"]),
        "tiers": [{"tier": t, "players": ps} for t, ps in sorted(tiers.items())],
    }, default=str)

    content = _ollama_chat(DRAFT_STRATEGY_SYSTEM, payload, model)
    markdown = f"# {league_row['name']} — Draft Strategy ({season})\n\n" + content
    conn.execute(
        "INSERT INTO reports (season, week, league_id, content, generated_at) "
        "VALUES (?, 0, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
        "ON CONFLICT(season, week, league_id) DO UPDATE SET "
        "content=excluded.content, generated_at=excluded.generated_at",
        (season, league_id, markdown),
    )
    conn.commit()
    conn.close()
    print(markdown)


DK_SALARY_CAP = 50000
# DK "Classic" NFL contest roster — the standard slate type, not Showdown/Captain Mode.
DK_CLASSIC_SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _dk_slot_eligible(position: str | None, slot: str) -> bool:
    if not position:
        return False
    if slot == "FLEX":
        return position in ("RB", "WR", "TE")
    return position == slot


def cmd_sync_dk(filepath: str, season: int, week: int) -> None:
    """Parse a DK "Classic" salary CSV exported by hand from the DraftKings
    site (Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,
    TeamAbbrev,AvgPointsPerGame). No DK API call of any kind — the user exports
    this file himself, this only reads it. Crosswalks each row to our own
    players table by normalized name + team so optimize-dk can join in
    in-house projections; DST rows crosswalk directly to the team code,
    matching how sync-stats stores team defenses (player_id = team code)."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    all_players = conn.execute("SELECT player_id, full_name, team FROM players").fetchall()
    by_name_team: dict[tuple[str, str], str] = {}
    by_name_only: dict[str, list[str]] = {}
    for p in all_players:
        if not p["full_name"]:
            continue
        n = _norm_name(p["full_name"])
        team = TEAM_CODE_FIX.get(p["team"], p["team"]) or ""
        by_name_team[(n, team)] = p["player_id"]
        by_name_only.setdefault(n, []).append(p["player_id"])

    n_rows, n_matched, n_skipped = 0, 0, 0
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Name") or "").strip()
            position = (row.get("Position") or "").strip()
            team = TEAM_CODE_FIX.get((row.get("TeamAbbrev") or "").strip(), (row.get("TeamAbbrev") or "").strip())
            dk_id = (row.get("ID") or "").strip()
            if not name or not dk_id:
                continue
            n_rows += 1

            if position == "DST":
                sleeper_id = team or None
            else:
                norm = _norm_name(name)
                sleeper_id = by_name_team.get((norm, team))
                if sleeper_id is None:
                    candidates = by_name_only.get(norm, [])
                    sleeper_id = candidates[0] if len(candidates) == 1 else None
            if sleeper_id:
                n_matched += 1
            else:
                n_skipped += 1

            salary = row.get("Salary")
            avg_pts = row.get("AvgPointsPerGame")
            conn.execute(
                "INSERT INTO dk_salaries (season, week, dk_player_id, name, position, roster_position, "
                "team, salary, game_info, avg_points_per_game, sleeper_id, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(season, week, dk_player_id) DO UPDATE SET "
                "name=excluded.name, position=excluded.position, roster_position=excluded.roster_position, "
                "team=excluded.team, salary=excluded.salary, game_info=excluded.game_info, "
                "avg_points_per_game=excluded.avg_points_per_game, sleeper_id=excluded.sleeper_id, "
                "imported_at=excluded.imported_at",
                (
                    season, week, dk_id, name, position, row.get("Roster Position"), team,
                    int(salary) if salary else None,
                    row.get("Game Info"),
                    float(avg_pts) if avg_pts else None,
                    sleeper_id,
                ),
            )
    conn.commit()
    conn.close()
    print(f"upserted {n_rows} dk_salaries rows for season {season} week {week} "
          f"({n_matched} crosswalked to players, {n_skipped} unmatched — those get no in-house "
          f"projection and fall back to DK's own AvgPointsPerGame in optimize-dk)")


def compute_dk_lineup(conn: sqlite3.Connection, season: int, week: int, stack: bool) -> dict:
    """DK Classic lineup via PuLP ILP: maximize total projected points subject
    to the $50,000 salary cap and the 9-slot Classic roster (QB, 2xRB, 3xWR,
    TE, FLEX, DST), with FLEX eligible to RB/WR/TE. Projection preference is
    our own in-house projections table (heuristic-v1); falls back to DK's own
    AvgPointsPerGame for any player we couldn't crosswalk or haven't projected
    (no prior weekly_stats). Never mutates the DB, never touches DraftKings —
    analysis only, output is the user's to enter by hand."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM dk_salaries WHERE season = ? AND week = ?", (season, week)
    ).fetchall()
    if not rows:
        raise ValueError(f"no dk_salaries for season {season} week {week} — run sync-dk first")

    pool = []
    for r in rows:
        proj_points, proj_source = None, None
        if r["sleeper_id"]:
            proj_row = conn.execute(
                "SELECT proj_points FROM projections WHERE player_id = ? AND season = ? AND week = ?",
                (r["sleeper_id"], season, week),
            ).fetchone()
            if proj_row and proj_row[0] is not None:
                proj_points, proj_source = proj_row[0], "heuristic-v1"
        if proj_points is None and r["avg_points_per_game"] is not None:
            proj_points, proj_source = r["avg_points_per_game"], "dk-avg-fallback"
        if proj_points is None:
            continue  # no data to build a lineup on — never fabricate a number
        pool.append({
            "dk_player_id": r["dk_player_id"], "name": r["name"], "position": r["position"],
            "team": r["team"], "salary": r["salary"], "proj_points": proj_points, "proj_source": proj_source,
        })

    prob = pulp.LpProblem("dk_classic_lineup", pulp.LpMaximize)
    x = {}
    for i, p in enumerate(pool):
        if not p["salary"]:
            continue
        for j, slot in enumerate(DK_CLASSIC_SLOTS):
            if _dk_slot_eligible(p["position"], slot):
                x[(i, j)] = pulp.LpVariable(f"x_{i}_{j}", cat="Binary")

    prob += pulp.lpSum(var * pool[i]["proj_points"] for (i, j), var in x.items())
    prob += pulp.lpSum(var * pool[i]["salary"] for (i, j), var in x.items()) <= DK_SALARY_CAP
    for i in range(len(pool)):
        prob += pulp.lpSum(var for (pi, pj), var in x.items() if pi == i) <= 1
    for j in range(len(DK_CLASSIC_SLOTS)):
        prob += pulp.lpSum(var for (pi, pj), var in x.items() if pj == j) == 1

    if stack:
        # Per team: if a QB from that team is started, require >=1 started
        # WR/TE from the same team (a same-team bring-back for the QB).
        teams = {p["team"] for p in pool if p["team"]}
        for team in teams:
            qb_vars = [var for (i, j), var in x.items() if pool[i]["team"] == team and pool[i]["position"] == "QB"]
            pc_vars = [var for (i, j), var in x.items() if pool[i]["team"] == team and pool[i]["position"] in ("WR", "TE")]
            if qb_vars:
                prob += pulp.lpSum(pc_vars) >= pulp.lpSum(qb_vars)

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[status] != "Optimal":
        raise ValueError(f"solver status: {pulp.LpStatus[status]} — likely not enough eligible/salaried "
                          f"players in the pool to fill all 9 slots under the cap")

    slot_to_player = {}
    for (i, j), var in x.items():
        if var.value() == 1:
            slot_to_player[j] = i
    starters = [{"slot": slot, "player": pool[slot_to_player[j]]} for j, slot in enumerate(DK_CLASSIC_SLOTS)]
    total_salary = sum(s["player"]["salary"] for s in starters)
    total_proj = sum(s["player"]["proj_points"] for s in starters)

    return {
        "season": season, "week": week, "stack": stack, "starters": starters,
        "total_salary": total_salary, "total_proj_points": round(total_proj, 2),
        "salary_remaining": DK_SALARY_CAP - total_salary,
    }


def cmd_optimize_dk(season: int, week: int, contest_type: str, stack: bool | None) -> None:
    if stack is None:
        stack = contest_type == "gpp"  # cash games: take the median, no need to correlate; GPP: chase ceiling
    conn = get_db()
    lineup = compute_dk_lineup(conn, season, week, stack)
    conn.execute(
        "INSERT INTO dk_lineups (season, week, contest_type, lineup_json, total_salary, total_proj_points, "
        "generated_at) VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
        "ON CONFLICT(season, week, contest_type) DO UPDATE SET "
        "lineup_json=excluded.lineup_json, total_salary=excluded.total_salary, "
        "total_proj_points=excluded.total_proj_points, generated_at=excluded.generated_at",
        (season, week, contest_type, json.dumps(lineup), lineup["total_salary"], lineup["total_proj_points"]),
    )
    conn.commit()
    conn.close()
    print(f"DK Classic lineup — week {week} ({season}), {contest_type}"
          f"{' + stacked' if stack else ''}")
    for s in lineup["starters"]:
        p = s["player"]
        src = "" if p["proj_source"] == "heuristic-v1" else f" [{p['proj_source']}]"
        print(f"  {s['slot']:5} {p['name']:25} {p['team'] or '':4} ${p['salary']:6,} {p['proj_points']:5.1f}{src}")
    print(f"  salary: ${lineup['total_salary']:,} / ${DK_SALARY_CAP:,} "
          f"(${lineup['salary_remaining']:,} remaining) — proj total: {lineup['total_proj_points']}")
    print("  Analysis only — enter this lineup on DraftKings yourself, never submitted automatically.")


KEEPER_RULESETS = ("huddle_buddies", "davante_world")


def compute_keeper(
    draft_round: int | None, origin: str, ruleset: str = "huddle_buddies"
) -> tuple[int | None, bool, str | None]:
    """Two independent keeper rulesets, one per league — never mix them.

    huddle_buddies: constitution rules 17-21 + the league's own working
    clarification that any dated pickup is past the trade deadline and
    therefore keeper-ineligible (not in the written constitution, but the
    operative rule per commissioner Joey). Cost = draft_round - 2,
    undrafted/waiver = round 13 flat.

    davante_world: this league has no written constitution — Its Davante's
    World's own rules, relayed directly by the user from the league's actual
    rules text (2026-08-24), after an earlier session had applied
    huddle_buddies' rules here as a stand-in since none were known yet.
    Cost = draft_round - 1 ("one round higher than previous year's draft
    slot"); undrafted players are valued at round 17, so cost = round 16.
    Same R1/R2-blocks-keeping rule as huddle_buddies ("no player may be
    kept for a 1st round pick" — since keeping a 2nd-rounder already costs
    a 1st-round pick, this excludes both R1 and R2). Does NOT encode the
    same-original-round tiebreak rule ("if a team has two players from the
    same round to be kept, one costs 2 rounds higher instead of 1, owner's
    choice which") — that only applies once two specific same-round players
    are both actually chosen as keepers, which is a keeper-selection-time
    judgment call, not a per-player eligibility fact; flag it manually at
    decision time if the roster's keeper shortlist has a same-round pair.

    Returns (keeper_cost_round, eligible, ineligible_reason)."""
    if ruleset not in KEEPER_RULESETS:
        raise ValueError(f"unknown keeper ruleset {ruleset!r}, expected one of {KEEPER_RULESETS}")
    if re.search(r"\((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\)", origin):
        return None, False, "after trade deadline (dated pickup)"
    if draft_round in (1, 2):
        return None, False, f"drafted round {draft_round} (R1/R2 rule)"
    never_drafted = ("Waiver" in origin or "Free Agent" in origin) and "Drafted by" not in origin
    if ruleset == "davante_world":
        if never_drafted:
            return 16, True, None
        if draft_round is None:
            return None, False, "no draft round on record"
        return draft_round - 1, True, None
    if never_drafted:
        return 13, True, None
    if draft_round is None:
        return None, False, "no draft round on record"
    return draft_round - 2, True, None


def _parse_keeper_source(text: str) -> list[dict]:
    """Parse the Huddle Buddies league's own yearly keeper-eligibility dump
    format: '<n>. <FirstName>' section headers, then one line per rostered
    player like 'WR: G. Pickens (DAL) — Round 5 | Drafted' or, for team
    defenses, 'DEF: LAR — Round 13 | Waiver Wire Add'. This is a manually
    compiled league doc, not a Sleeper API response — format is whatever
    the league happens to use each year, adjust this parser if it changes."""
    sections, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\d+\.\s*(\S+)$", line)
        if m:
            if cur:
                sections.append(cur)
            cur = {"name": m.group(1), "players": []}
            continue
        if cur is None:
            continue
        m2 = re.match(r"^(\w+):\s*(.+?)\s*\(([\w/]+)(?:\s*-\s*([\w/]+))?\)\s*.\s*Round\s*(\d+)\s*\|\s*(.+)$", line)
        if m2:
            pos1, namepart, team_or_pos, team2, rnd, origin = m2.groups()
            pos, team = (team_or_pos, team2) if team2 else (pos1, team_or_pos)
            cur["players"].append({"name": namepart.strip(), "pos": pos, "team": team,
                                    "round": int(rnd), "origin": origin.strip()})
            continue
        m3 = re.match(r"^DEF:\s*(\w+)\s*.\s*Round\s*(\d+)\s*\|\s*(.+)$", line)
        if m3:
            team, rnd, origin = m3.groups()
            cur["players"].append({"name": team, "pos": "DEF", "team": team,
                                    "round": int(rnd), "origin": origin.strip()})
    if cur:
        sections.append(cur)
    return sections


def _resolve_season_league(league_id: str, season: int, max_hops: int = 6) -> dict:
    """Walk a Sleeper league's `previous_league_id` chain back from the
    current league_id until finding the league object for `season`. Sleeper
    gives each season of a continuing league its own league_id, chained via
    previous_league_id — this is how we find the right historical draft_id
    and transaction feed for a season without the user telling us the old ID."""
    league = sleeper_get(f"/league/{league_id}")
    for _ in range(max_hops):
        if str(league.get("season")) == str(season):
            return league
        prev = league.get("previous_league_id")
        if not prev:
            break
        league = sleeper_get(f"/league/{prev}")
    raise ValueError(f"couldn't find season {season} in league {league_id}'s history "
                      f"(walked {max_hops} previous_league_id hops)")


def cmd_sync_draft_history(league_id: str, season: int, roster_id: int | None, ruleset: str) -> None:
    """Build draft_history for one roster directly from Sleeper's own draft
    picks + transaction log — no manual dump needed, unlike Huddle Buddies
    (which predates Sleeper and has no clean draft/transaction API history
    of its own). `ruleset` selects which of compute_keeper's two rulesets
    to apply (see its docstring) — pass "davante_world" for Its Davante's
    World's own real rules (relayed by the user 2026-08-24; an earlier session
    used "huddle_buddies" here as a stand-in since no ruleset was known yet
    for this league — do not use that default going forward for this
    league). A player's draft round travels with them through trades within
    a season — we only look at *whether* and *at what round* a player was
    drafted, not who currently owns them within that season, so a
    since-traded-for player still gets their original draft-round cost."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    if roster_id is None:
        user_id = os.environ.get("SLEEPER_USER_ID")
        if not user_id:
            raise ValueError("no --roster-id given and SLEEPER_USER_ID is not set in .env")
        r = conn.execute(
            "SELECT roster_id, player_ids FROM sleeper_rosters WHERE league_id = ? AND owner_id = ?",
            (league_id, user_id),
        ).fetchone()
        if not r:
            raise ValueError(f"no roster in league {league_id} owned by SLEEPER_USER_ID {user_id} "
                              f"— run sync-sleeper first")
        roster_id, player_ids = r["roster_id"], json.loads(r["player_ids"])
    else:
        r = conn.execute(
            "SELECT player_ids FROM sleeper_rosters WHERE league_id = ? AND roster_id = ?",
            (league_id, roster_id),
        ).fetchone()
        if not r:
            raise ValueError(f"no roster_id {roster_id} in league {league_id} — run sync-sleeper first")
        player_ids = json.loads(r["player_ids"])

    hist_league = _resolve_season_league(league_id, season)
    trade_deadline = hist_league.get("settings", {}).get("trade_deadline")
    draft_id = hist_league.get("draft_id")
    if not draft_id:
        raise ValueError(f"league {hist_league['league_id']} (season {season}) has no draft_id on record")

    picks = sleeper_get(f"/draft/{draft_id}/picks")
    draft_round_by_player = {p["player_id"]: p["round"] for p in picks if p.get("player_id")}

    playoff_week_start = hist_league.get("settings", {}).get("playoff_week_start", 15)
    earliest_add: dict[str, tuple[int, int]] = {}  # player_id -> (leg, created_ms)
    for leg in range(1, playoff_week_start + 3):
        txns = sleeper_get(f"/league/{hist_league['league_id']}/transactions/{leg}")
        for t in txns:
            if t.get("status") != "complete" or t.get("type") not in ("waiver", "free_agent"):
                continue
            for pid in (t.get("adds") or {}):
                created = t.get("created") or 0
                if pid not in earliest_add or created < earliest_add[pid][1]:
                    earliest_add[pid] = (t.get("leg", leg), created)

    n = 0
    for pid in player_ids:
        prow = conn.execute("SELECT full_name, position, team FROM players WHERE player_id = ?", (pid,)).fetchone()
        name = prow["full_name"] if prow else pid
        position = prow["position"] if prow else None
        team = prow["team"] if prow else None

        if pid in draft_round_by_player:
            draft_round = draft_round_by_player[pid]
            origin = "Drafted"
        elif pid in earliest_add:
            leg, created_ms = earliest_add[pid]
            draft_round = None
            if trade_deadline is not None and leg > trade_deadline:
                dt = datetime.datetime.fromtimestamp(created_ms / 1000, tz=datetime.timezone.utc)
                origin = f"Waiver Wire Add ({dt.strftime('%b')} {dt.day})"
            else:
                origin = "Waiver Wire Add"
        else:
            draft_round = None
            origin = "No draft/waiver record found this season"

        cost, eligible, reason = compute_keeper(draft_round, origin, ruleset)
        conn.execute(
            "INSERT INTO draft_history (league_id, roster_id, player_name, player_id, position, nfl_team, "
            "draft_round, origin, keeper_eligible, keeper_cost_round, ineligible_reason, season, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(league_id, roster_id, player_name, season) DO UPDATE SET "
            "player_id=excluded.player_id, position=excluded.position, nfl_team=excluded.nfl_team, "
            "draft_round=excluded.draft_round, origin=excluded.origin, keeper_eligible=excluded.keeper_eligible, "
            "keeper_cost_round=excluded.keeper_cost_round, ineligible_reason=excluded.ineligible_reason, "
            "imported_at=excluded.imported_at",
            (league_id, roster_id, name, pid, position, team, draft_round, origin,
             1 if eligible else 0, cost, reason, season),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"synced draft_history for roster {roster_id} in league {league_id}, season {season}: "
          f"{n} players ({len(draft_round_by_player)} drafted picks, {len(earliest_add)} waiver/FA adds "
          f"seen league-wide, trade_deadline=week {trade_deadline}). Run keepers-report to see results.")


def cmd_import_draft_history(league_id: str, filepath: str, season: int) -> None:
    sections = _parse_keeper_source(Path(filepath).read_text(encoding="utf-8"))
    conn = get_db()
    conn.row_factory = sqlite3.Row

    rosters = conn.execute(
        "SELECT roster_id, owner_name, player_ids FROM sleeper_rosters WHERE league_id = ?", (league_id,)
    ).fetchall()
    roster_names = {}
    for r in rosters:
        names = set()
        for pid in json.loads(r["player_ids"]):
            prow = conn.execute("SELECT full_name FROM players WHERE player_id = ?", (pid,)).fetchone()
            if prow:
                names.add(_norm_name(prow["full_name"]))
        roster_names[r["roster_id"]] = {"owner": r["owner_name"], "names": names}

    n = 0
    for s in sections:
        sec_tokens = {_norm_name(p["name"]) for p in s["players"]}
        best_roster, best_score = None, -1
        for rid, info in roster_names.items():
            score = sum(
                1 for tok in sec_tokens
                if tok and any(tok in full or (full.endswith(tok[1:]) and tok[0] == full[0]) for full in info["names"])
            )
            if score > best_score:
                best_score, best_roster = score, rid
        if best_score < len(s["players"]) * 0.7:
            print(f"WARNING: '{s['name']}' matched roster {best_roster} with only "
                  f"{best_score}/{len(s['players'])} overlap — check manually, skipping import for this team")
            continue
        print(f"{s['name']:10} -> roster {best_roster} ({roster_names[best_roster]['owner']}) "
              f"score={best_score}/{len(s['players'])}")

        for p in s["players"]:
            cost, eligible, reason = compute_keeper(p["round"], p["origin"])
            conn.execute(
                "INSERT INTO draft_history (league_id, roster_id, player_name, position, nfl_team, "
                "draft_round, origin, keeper_eligible, keeper_cost_round, ineligible_reason, season, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(league_id, roster_id, player_name, season) DO UPDATE SET "
                "position=excluded.position, nfl_team=excluded.nfl_team, draft_round=excluded.draft_round, "
                "origin=excluded.origin, keeper_eligible=excluded.keeper_eligible, "
                "keeper_cost_round=excluded.keeper_cost_round, ineligible_reason=excluded.ineligible_reason, "
                "imported_at=excluded.imported_at",
                (league_id, best_roster, p["name"], p["pos"], p["team"], p["round"], p["origin"],
                 1 if eligible else 0, cost, reason, season),
            )
            n += 1
    conn.commit()
    conn.close()
    print(f"imported {n} draft_history rows across {len(sections)} teams for season {season}")


def cmd_keepers_report(league_id: str, season: int) -> None:
    """Every eligible keeper for every team, plus anything actually declared
    so far (keeper_declarations, empty until the league shares real picks).
    Never guesses another owner's actual pick — only shows what's eligible
    and what's been explicitly declared."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rosters = conn.execute(
        "SELECT roster_id, owner_name FROM sleeper_rosters WHERE league_id = ? ORDER BY roster_id", (league_id,)
    ).fetchall()
    for r in rosters:
        declared = conn.execute(
            "SELECT player_name, keeper_cost_round FROM keeper_declarations "
            "WHERE league_id = ? AND roster_id = ? AND season = ?",
            (league_id, r["roster_id"], season),
        ).fetchall()
        eligible = conn.execute(
            "SELECT player_name, position, draft_round, keeper_cost_round, origin FROM draft_history "
            "WHERE league_id = ? AND roster_id = ? AND season = ? AND keeper_eligible = 1 "
            "ORDER BY keeper_cost_round",
            (league_id, r["roster_id"], season),
        ).fetchall()
        print(f"\n{r['owner_name']} (roster {r['roster_id']}):")
        if declared:
            print("  DECLARED:")
            for d in declared:
                print(f"    {d['player_name']:20} cost R{d['keeper_cost_round']}")
        else:
            print(f"  not declared yet — {len(eligible)} eligible options:")
            for e in eligible:
                origin_label = f"drafted R{e['draft_round']}" if "Drafted" in e["origin"] or "Drafted by" in e["origin"] else e["origin"]
                print(f"    {e['player_name']:20} {e['position'] or '?':4} {origin_label:30} -> cost R{e['keeper_cost_round']}")
    conn.close()


def cmd_keepers_declare(league_id: str, roster_id: int, player_name: str, season: int,
                         cost_round: int | None, source: str) -> None:
    conn = get_db()
    if cost_round is None:
        row = conn.execute(
            "SELECT keeper_cost_round FROM draft_history WHERE league_id=? AND roster_id=? "
            "AND player_name=? AND season=?",
            (league_id, roster_id, player_name, season),
        ).fetchone()
        cost_round = row[0] if row else None
    conn.execute(
        "INSERT INTO keeper_declarations (league_id, roster_id, player_name, keeper_cost_round, season, "
        "declared_at, source) VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'), ?) "
        "ON CONFLICT(league_id, roster_id, player_name, season) DO UPDATE SET "
        "keeper_cost_round=excluded.keeper_cost_round, declared_at=excluded.declared_at, source=excluded.source",
        (league_id, roster_id, player_name, cost_round, season, source),
    )
    conn.commit()
    conn.close()
    print(f"declared: roster {roster_id}, {player_name}, cost R{cost_round}")


TIER_FILL_COLORS = [
    "FFF2CC", "D9EAD3", "CFE2F3", "F4CCCC", "EAD1DC",
    "D0E0E3", "FCE5CD", "D9D2E9", "C9DAF8", "FFE599",
]


def cmd_export_xlsx(league_id: str, season: int, out_path: str | None) -> None:
    """Export build-draft-pool's board (all rounds, ranked by adj_value,
    with position/tier/risk columns) to a printable .xlsx -- the user's offline
    backup for draft day if internet/tokens go down mid-draft. Analysis
    only, same as everything else in this file -- just a formatted dump of
    what's already in draft_pool, not a new computation."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise ValueError("openpyxl is required for export-xlsx -- pip install openpyxl "
                          "(see requirements.txt)") from e

    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT dp.player_id, dp.position, dp.ecr, dp.pos_rank, dp.adj_value, dp.tier, "
        "dp.risk_score, dp.risk_note, p.full_name, p.team "
        "FROM draft_pool dp LEFT JOIN players p ON p.player_id = dp.player_id "
        "WHERE dp.league_id = ? AND dp.season = ? AND dp.drafted = 0 "
        "ORDER BY dp.adj_value DESC",
        (league_id, season),
    ).fetchall()
    if not rows:
        conn.close()
        raise ValueError(f"no draft_pool rows for league {league_id}, season {season} -- run build-draft-pool first")

    league_name_row = conn.execute(
        "SELECT name FROM sleeper_leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    league_name = league_name_row[0] if league_name_row else league_id
    conn.close()

    if out_path is None:
        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", league_name).strip("_")
        out_path = str(DATA_DIR / f"draft_board_{safe_name}_{season}.xlsx")
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Draft Board"

    headers = ["Overall #", "Player", "Pos", "Team", "Pos Rank", "ECR", "Adj Value", "Tier", "Risk", "Risk Note"]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="434343")
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"

    for i, r in enumerate(rows, start=1):
        name = r["full_name"] or r["player_id"]
        risk = r["risk_score"]
        risk_str = f"{risk:.0f}" if risk is not None else ""
        ws.append([
            i, name, r["position"] or "", r["team"] or "", r["pos_rank"] or "",
            round(r["ecr"], 1) if r["ecr"] is not None else "",
            r["adj_value"], r["tier"], risk_str, r["risk_note"] or "",
        ])
        tier = r["tier"] or 1
        fill_color = TIER_FILL_COLORS[(tier - 1) % len(TIER_FILL_COLORS)]
        fill = PatternFill("solid", fgColor=fill_color)
        for col in range(1, len(headers) + 1):
            ws.cell(row=i + 1, column=col).fill = fill

    widths = [10, 26, 6, 6, 9, 8, 10, 6, 6, 40]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = w

    ws.print_title_rows = "1:1"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    wb.save(out_file)
    print(f"exported {len(rows)} players to {out_file} (league {league_name}, season {season})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    players_p = sub.add_parser("sync-players", help="Pull Sleeper's global player map")
    players_p.add_argument("--force", action="store_true", help="Refetch even if cache is < 24h old")

    sleeper_p = sub.add_parser("sync-sleeper", help="Pull league/roster/matchup/transaction data")
    sleeper_p.add_argument("--league-id", action="append",
                            help="League ID (repeatable). Default: SLEEPER_LEAGUE_IDS from .env")
    sleeper_p.add_argument("--week", type=int, help="NFL week (default: current week per Sleeper)")

    stats_p = sub.add_parser("sync-stats", help="Pull weekly stats/snap counts/injury reports via nflreadpy")
    stats_p.add_argument("--season", type=int, help="NFL season (default: nflreadpy's current season)")
    stats_p.add_argument("--week", type=int, help="Limit to one NFL week (default: whole season to date)")

    project_p = sub.add_parser("project", help="Compute in-house projections for a week from prior weekly_stats")
    project_p.add_argument("--week", type=int, required=True, help="NFL week to project")
    project_p.add_argument("--season", type=int, help="NFL season (default: nflreadpy's current season)")

    odds_p = sub.add_parser("sync-odds", help="Pull Vegas spreads/totals via The Odds API (requires ODDS_API_KEY)")
    odds_p.add_argument("--week", type=int, required=True, help="NFL week")
    odds_p.add_argument("--season", type=int, help="NFL season (default: nflreadpy's current season)")

    weather_p = sub.add_parser("sync-weather", help="Pull forecast conditions for outdoor games via the NWS API (free)")
    weather_p.add_argument("--week", type=int, required=True, help="NFL week")
    weather_p.add_argument("--season", type=int, help="NFL season (default: nflreadpy's current season)")

    opt_p = sub.add_parser("optimize-sleeper", help="LP-optimal start/sit lineup for one league roster")
    opt_p.add_argument("--league-id", required=True, help="Sleeper league ID")
    opt_p.add_argument("--week", type=int, required=True, help="NFL week")
    opt_p.add_argument("--season", type=int, help="NFL season (default: nflreadpy's current season)")
    opt_p.add_argument("--roster-id", type=int, help="Roster ID (default: roster owned by SLEEPER_USER_ID)")

    report_p = sub.add_parser("report", help="Weekly write-up: optimizer output + injuries -> Ollama prose")
    report_p.add_argument("--week", type=int, required=True, help="NFL week")
    report_p.add_argument("--season", type=int, help="NFL season (default: nflreadpy's current season)")
    report_p.add_argument("--league-id", action="append",
                           help="League ID (repeatable). Default: SLEEPER_LEAGUE_IDS from .env")
    report_p.add_argument("--model", default=DEFAULT_REPORT_MODEL, help=f"Ollama model tag (default: {DEFAULT_REPORT_MODEL})")
    report_p.add_argument("--force", action="store_true", help="Regenerate even if a cached report exists")

    import_p = sub.add_parser("import-draft-history", help="Parse a league's yearly keeper-eligibility text dump")
    import_p.add_argument("--league-id", required=True)
    import_p.add_argument("--file", required=True, help="Path to the league's keeper-list text file")
    import_p.add_argument("--season", type=int, required=True, help="Season this draft history is for")

    syncdraft_p = sub.add_parser("sync-draft-history",
                                  help="Build draft_history for one roster directly from Sleeper's draft/transaction API (no manual dump needed)")
    syncdraft_p.add_argument("--league-id", required=True)
    syncdraft_p.add_argument("--season", type=int, required=True, help="Season to pull draft/transaction history for")
    syncdraft_p.add_argument("--roster-id", type=int, help="Roster ID (default: roster owned by SLEEPER_USER_ID)")
    syncdraft_p.add_argument("--ruleset", required=True, choices=KEEPER_RULESETS, help="Which league's keeper rules to apply (see compute_keeper docstring)")

    kreport_p = sub.add_parser("keepers-report", help="Every team's eligible keepers + any declared so far")
    kreport_p.add_argument("--league-id", required=True)
    kreport_p.add_argument("--season", type=int, required=True)

    syncdk_p = sub.add_parser("sync-dk", help="Parse a hand-exported DK Classic salary CSV")
    syncdk_p.add_argument("--file", required=True, help="Path to the DK salary CSV export")
    syncdk_p.add_argument("--season", type=int, required=True)
    syncdk_p.add_argument("--week", type=int, required=True)

    optdk_p = sub.add_parser("optimize-dk", help="Salary-cap ILP-optimal DK Classic lineup (analysis only)")
    optdk_p.add_argument("--season", type=int, required=True)
    optdk_p.add_argument("--week", type=int, required=True)
    optdk_p.add_argument("--contest-type", choices=["cash", "gpp"], default="gpp")
    stack_group = optdk_p.add_mutually_exclusive_group()
    stack_group.add_argument("--stack", dest="stack", action="store_true", default=None,
                              help="Force QB+same-team-WR/TE stacking (default: on for gpp, off for cash)")
    stack_group.add_argument("--no-stack", dest="stack", action="store_false")

    rank_p = sub.add_parser("sync-rankings", help="Pull FantasyPros dynasty-overall ECR via nflreadpy")
    rank_p.add_argument("--season", type=int, required=True, help="Season these rankings apply to")
    rank_p.add_argument("--force", action="store_true", help="Refetch even if already synced today")

    wrank_p = sub.add_parser("sync-weekly-rankings",
                              help="Pull FantasyPros weekly per-position ECR + Sleeper weekly projections")
    wrank_p.add_argument("--season", type=int, required=True)
    wrank_p.add_argument("--week", type=int, required=True)
    wrank_p.add_argument("--force", action="store_true", help="Refetch even if already synced today")

    blend_p = sub.add_parser("blend-projections",
                              help="Fold weekly-rankings' signals into projections (run after project + sync-weekly-rankings)")
    blend_p.add_argument("--season", type=int, required=True)
    blend_p.add_argument("--week", type=int, required=True)
    blend_p.add_argument("--force", action="store_true", help="Reblend even if already blended")

    pool_p = sub.add_parser("build-draft-pool", help="Compute a VBD-adjusted draft board for one league")
    pool_p.add_argument("--league-id", required=True)
    pool_p.add_argument("--season", type=int, required=True, help="Season being drafted")
    pool_p.add_argument("--prior-season", type=int, help="Season to source the points curve from (default: season - 1)")
    pool_p.add_argument("--skip-keeper-check", action="store_true",
                         help="Bypass the >=3-declared-keepers-per-roster hard-fail. Pre-declaration "
                              "mock-draft rehearsal ONLY (keeper deadline is 8/24) — never use for a real "
                              "draft, since it can let a teammate's real undeclared keeper sit in the pool.")

    draftlive_p = sub.add_parser("draft-live", help="Poll a league's live Sleeper draft; surface signals/recommendations")
    draftlive_p.add_argument("--league-id", required=True,
                              help="League ID (used for scoring/roster context + the draft_pool board -- "
                                   "the value board always comes from this league's build-draft-pool output, "
                                   "even in --draft-id mock mode)")
    draftlive_p.add_argument("--draft-id",
                              help="Poll THIS Sleeper draft object instead of --league-id's own auto-resolved "
                                   "draft_id. FOR MOCK/REHEARSAL DRY-RUNS ONLY (a standalone Sleeper mock "
                                   "draftboard is its own sandbox draft object with no real league behind it, "
                                   "league_id=null on Sleeper's side) -- a real draft should keep using plain "
                                   "--league-id auto-resolve, not this.")
    draftlive_p.add_argument("--my-slot", type=int,
                              help="Mock/rehearsal mode ONLY (requires --draft-id): the pick-order column "
                                   "(1-teams) that is your seat in that standalone mock draftboard. A mock "
                                   "draft's picks carry no real roster_id, only a draft_slot -- this maps that "
                                   "slot to the draft's own slot_to_roster_id so 'your pick next' detection and "
                                   "your-picks-so-far tracking work correctly. Not used/needed for a real draft.")
    draftlive_p.add_argument("--roster-id", type=int,
                              help="Real Sleeper roster_id to pull keeper_declarations/roster-need context "
                                   "from (default: roster owned by SLEEPER_USER_ID). Always the real league "
                                   "roster, even in --draft-id/--my-slot mock mode -- --my-slot is what "
                                   "identifies your seat within the polled mock draft itself.")
    draftlive_p.add_argument("--poll-seconds", type=int, default=20, help="Seconds between polls in --watch mode")
    draftlive_p.add_argument("--watch", action="store_true",
                              help="Loop continuously in this terminal; default is a single poll-and-print")

    draftstrat_p = sub.add_parser("draft-strategy", help="Positional-scarcity/tier summary, narrated for Obsidian")
    draftstrat_p.add_argument("--league-id", required=True)
    draftstrat_p.add_argument("--season", type=int, required=True)
    draftstrat_p.add_argument("--model", default=DEFAULT_REPORT_MODEL, help=f"Ollama model tag (default: {DEFAULT_REPORT_MODEL})")
    draftstrat_p.add_argument("--force", action="store_true", help="Regenerate even if a cached write-up exists")

    kdeclare_p = sub.add_parser("keepers-declare", help="Record an actual declared keeper for a roster")
    kdeclare_p.add_argument("--league-id", required=True)
    kdeclare_p.add_argument("--roster-id", type=int, required=True)
    kdeclare_p.add_argument("--player", required=True, help="Player name, must match draft_history exactly")
    kdeclare_p.add_argument("--season", type=int, required=True)
    kdeclare_p.add_argument("--round", type=int, help="Keeper cost round (default: looked up from draft_history)")
    kdeclare_p.add_argument("--source", default="manual", help="Where this came from (default: manual)")

    dst_p = sub.add_parser("dst-schedule-strength",
                            help="Schedule-strength-adjusted DST rank (upcoming opposing offenses, not raw DEF quality)")
    dst_p.add_argument("--season", type=int, required=True, help="Season being scheduled/drafted")
    dst_p.add_argument("--as-of-week", type=int, default=1, help="First week of the upcoming window (default: 1)")
    dst_p.add_argument("--window", type=int, default=4, help="Number of upcoming weeks to average (default: 4)")
    dst_p.add_argument("--strength-season", type=int,
                        help="Season whose weekly_stats feeds the opponent-offense proxy (default: season - 1)")

    upside_p = sub.add_parser("upside-targets",
                               help="Local-data-derived mid/late-round upside/handcuff candidate pool")
    upside_p.add_argument("--league-id", required=True)
    upside_p.add_argument("--season", type=int, required=True)
    upside_p.add_argument("--prior-season", type=int, help="Season to source injury/depth signals from (default: season - 1)")

    grade_p = sub.add_parser("roster-grade",
                              help="Per-position letter grades + strengths/weaknesses/waiver watch for any roster")
    grade_p.add_argument("--league-id", required=True,
                          help="League ID (used for scoring/roster_positions context, same as draft-live -- "
                               "the real league's rules apply even when grading a --draft-id mock roster)")
    grade_p.add_argument("--roster-id", type=int,
                          help="Any roster_id in this league, not just the user's own. In --draft-id mock mode, this "
                               "is the MOCK draftboard's own synthetic roster_id -- normally left unset in favor "
                               "of --my-slot, which resolves it for you via that draft's slot_to_roster_id map.")
    grade_p.add_argument("--season", type=int, help="NFL season (default: nflreadpy's current season)")
    grade_p.add_argument("--draft-id",
                          help="Grade a roster's picks from THIS Sleeper draft object's draft_picks instead of "
                               "the real league's sleeper_rosters -- for grading a standalone mock/rehearsal "
                               "draftboard as if it were real. Same mock-draft target as draft-live's --draft-id. "
                               "'Unrostered' for waiver-pickup suggestions falls back to this league's own "
                               "draft_pool.drafted=0 instead of cross-referencing sleeper_rosters, since a mock "
                               "draftboard isn't a real league.")
    grade_p.add_argument("--my-slot", type=int,
                          help="Mock/rehearsal mode ONLY (requires --draft-id): the pick-order column (1-teams) "
                               "that is your seat in that standalone mock draftboard -- resolved to a roster_id "
                               "via the draft's own slot_to_roster_id map, same as draft-live's --my-slot. Use "
                               "this instead of --roster-id when grading your own mock-draft roster.")

    xlsx_p = sub.add_parser("export-xlsx", help="Export build-draft-pool's board to a printable .xlsx")
    xlsx_p.add_argument("--league-id", required=True)
    xlsx_p.add_argument("--season", type=int, required=True)
    xlsx_p.add_argument("--out", help="Output file path (default: data/fantasy/draft_board_<league>_<season>.xlsx)")

    args = parser.parse_args()

    if args.command == "sync-players":
        cmd_sync_players(args.force)
    elif args.command == "sync-sleeper":
        league_ids = args.league_id or SLEEPER_LEAGUE_IDS
        cmd_sync_sleeper(league_ids, args.week)
    elif args.command == "sync-stats":
        season = args.season or nfl.get_current_season()
        cmd_sync_stats(season, args.week)
    elif args.command == "project":
        season = args.season or nfl.get_current_season()
        cmd_project(season, args.week)
    elif args.command == "sync-odds":
        season = args.season or nfl.get_current_season()
        cmd_sync_odds(season, args.week)
    elif args.command == "sync-weather":
        season = args.season or nfl.get_current_season()
        cmd_sync_weather(season, args.week)
    elif args.command == "optimize-sleeper":
        season = args.season or nfl.get_current_season()
        cmd_optimize_sleeper(args.league_id, args.week, season, args.roster_id)
    elif args.command == "report":
        season = args.season or nfl.get_current_season()
        league_ids = args.league_id or SLEEPER_LEAGUE_IDS
        cmd_report(args.week, season, league_ids, args.model, args.force)
    elif args.command == "import-draft-history":
        cmd_import_draft_history(args.league_id, args.file, args.season)
    elif args.command == "sync-draft-history":
        cmd_sync_draft_history(args.league_id, args.season, args.roster_id, args.ruleset)
    elif args.command == "keepers-report":
        cmd_keepers_report(args.league_id, args.season)
    elif args.command == "keepers-declare":
        cmd_keepers_declare(args.league_id, args.roster_id, args.player, args.season, args.round, args.source)
    elif args.command == "sync-dk":
        cmd_sync_dk(args.file, args.season, args.week)
    elif args.command == "optimize-dk":
        cmd_optimize_dk(args.season, args.week, args.contest_type, args.stack)
    elif args.command == "sync-rankings":
        cmd_sync_rankings(args.season, args.force)
    elif args.command == "sync-weekly-rankings":
        cmd_sync_weekly_rankings(args.season, args.week, args.force)
    elif args.command == "blend-projections":
        cmd_blend_projections(args.season, args.week, args.force)
    elif args.command == "build-draft-pool":
        cmd_build_draft_pool(args.league_id, args.season, args.prior_season, args.skip_keeper_check)
    elif args.command == "draft-live":
        cmd_draft_live(args.league_id, args.roster_id, args.poll_seconds, args.watch, args.draft_id, args.my_slot)
    elif args.command == "draft-strategy":
        cmd_draft_strategy(args.league_id, args.season, args.model, args.force)
    elif args.command == "dst-schedule-strength":
        cmd_dst_schedule_strength(args.season, args.as_of_week, args.window, args.strength_season)
    elif args.command == "upside-targets":
        cmd_upside_targets(args.league_id, args.season, args.prior_season)
    elif args.command == "roster-grade":
        season = args.season or nfl.get_current_season()
        if args.my_slot is not None and not args.draft_id:
            raise ValueError("--my-slot only applies with --draft-id (mock/rehearsal mode) -- "
                              "a real roster grade should use plain --roster-id")
        if not args.draft_id and args.roster_id is None:
            raise ValueError("--roster-id is required unless --draft-id/--my-slot (mock mode) is given")
        cmd_roster_grade(args.league_id, args.roster_id, season, draft_id=args.draft_id, my_slot=args.my_slot)
    elif args.command == "export-xlsx":
        cmd_export_xlsx(args.league_id, args.season, args.out)


if __name__ == "__main__":
    main()
