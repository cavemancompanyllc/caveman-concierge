-- concierge-media tables in the caveman-core ledger (<workspace>/data/ledger.db).
-- caveman-core applies every enabled plugin's schema/*.sql after its own core
-- schema; media_ctl.py also applies this file itself before first use, so it
-- never depends on a ledger sync having run. Idempotent: CREATE ... IF NOT EXISTS.
--
-- media_triage_decisions: one row per decision about a Jellyseerr request -
-- automatic (`requests triage`) or manual (`requests approve/decline`).
-- It is the source of truth for three things triage needs between runs:
--   * requests already approved/rejected (skip them if they linger as pending)
--   * escalations already notified (don't re-ping Discord every run)
--   * the rolling 7-day approval count per account-bound identity (cap_key)
-- request_id is only unique within one Jellyseerr, so every lookup is scoped
-- by instance (the instance name from config/media.json).
-- Times are ISO 8601 UTC with an explicit Z, matching the core ledger.

CREATE TABLE IF NOT EXISTS media_triage_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    instance TEXT NOT NULL,
    request_id INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve','reject','escalate')),
    source TEXT NOT NULL DEFAULT 'triage' CHECK (source IN ('triage','manual')),
    reason TEXT,
    requester TEXT,          -- display label only; never used for policy
    cap_key TEXT,            -- lowercased Plex/Jellyfin username; NULL for manual
    legacy_ref TEXT UNIQUE   -- set when imported from data/media/triage_log.json
);

CREATE INDEX IF NOT EXISTS idx_media_triage_request
    ON media_triage_decisions (instance, request_id);
CREATE INDEX IF NOT EXISTS idx_media_triage_cap
    ON media_triage_decisions (instance, decision, at);
