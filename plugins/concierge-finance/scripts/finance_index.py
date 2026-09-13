"""Import and categorize Chase/BoA transaction exports into a local SQLite store.

Parses CSV/OFX/QFX files dropped in data/finance/inbox/, categorizes each
transaction's merchant via a local Ollama model (content never reaches
Claude's API — bank data stays on this machine), and stores everything in
data/finance.db for querying. Processed files move to data/finance/archive/.

Usage:
    python scripts/finance_index.py import [--model phi4:14b]
    python scripts/finance_index.py report [--month YYYY-MM] [--category X]
    python scripts/finance_index.py search <query>
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(os.environ.get("CONCIERGE_HOME") or Path.cwd())
DATA_DIR = ROOT / "data" / "finance"
INBOX_DIR = DATA_DIR / "inbox"
ARCHIVE_DIR = DATA_DIR / "archive"
DB_PATH = ROOT / "data" / "finance.db"
CSR_BENEFITS_PATH = ROOT / "scripts" / "csr_benefits.json"

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gpt-oss:20b"
REQUEST_TIMEOUT = 60.0

CATEGORIES = [
    "Housing", "Utilities", "Groceries", "Dining", "Transportation", "Auto",
    "Insurance", "Subscriptions", "Shopping", "Health", "Entertainment",
    "Travel", "Income", "Transfer", "Fees", "Uncategorized",
]

CLASSIFY_SYSTEM = (
    "You categorize a personal bank transaction merchant/description into "
    "exactly one category. Respond with ONLY a JSON object, no other text: "
    '{"category": "<one of: ' + ", ".join(CATEGORIES) + '>"}'
)

# Credit-card payment memos (the user paying off a card from checking) aren't
# real spend — they're a Transfer between his own accounts. The LLM
# classifier has no way to tell that apart from a real subscription/fee
# charge based on merchant text alone, so route these deterministically
# before ever calling the model.
TRANSFER_PATTERN = re.compile(
    r"PAYMENT THANK YOU|AUTOPAY|AUTOMATIC PAYMENT|ONLINE (?:PMT|PAYMENT)"
    r"|CARD ?PMT|CREDIT CARD PAYMENT",
    re.IGNORECASE,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    institution TEXT NOT NULL,
    account_name TEXT NOT NULL,
    account_type TEXT,
    UNIQUE(institution, account_name)
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    source_file TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS merchant_categories (
    merchant_raw TEXT PRIMARY KEY,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    import_id INTEGER NOT NULL REFERENCES imports(id),
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    merchant_raw TEXT NOT NULL,
    category TEXT NOT NULL,
    dedupe_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS IX_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS IX_transactions_category ON transactions(category);
"""


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def get_or_create_account(conn: sqlite3.Connection, institution: str,
                           account_name: str, account_type: str | None) -> int:
    row = conn.execute(
        "SELECT id FROM accounts WHERE institution = ? AND account_name = ?",
        (institution, account_name),
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO accounts (institution, account_name, account_type) VALUES (?, ?, ?)",
        (institution, account_name, account_type),
    )
    return cur.lastrowid


def normalize_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_merchant(description: str) -> str:
    text = re.sub(r"\d{4,}", "", description)  # strip card/store/ref numbers
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text[:60] or "UNKNOWN"


def dedupe_hash(account_id: int, date: str, amount: float, description: str) -> str:
    raw = f"{account_id}|{date}|{amount:.2f}|{description.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def categorize(merchant: str, model: str, cache: dict[str, str]) -> str:
    if merchant in cache:
        return cache[merchant]
    if TRANSFER_PATTERN.search(merchant):
        cache[merchant] = "Transfer"
        return "Transfer"
    try:
        resp = httpx.post(
            OLLAMA_URL,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CLASSIFY_SYSTEM},
                    {"role": "user", "content": merchant},
                ],
                "stream": False,
                "format": "json",
                # gpt-oss ignores think=False and needs an effort level instead;
                # "low" keeps one-word categorization from reasoning at length.
                "think": "low" if model.startswith("gpt-oss") else False,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "{}")
        category = json.loads(content).get("category")
        if category not in CATEGORIES:
            category = "Uncategorized"
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        category = "Uncategorized"
    cache[merchant] = category
    return category


# ---------------------------------------------------------------------------
# CSV parsing — Chase and BoA use different column layouts. Column names
# below match each bank's typical export as of 2026; if a real export
# doesn't match, adjust sniff_institution()/parse_chase()/parse_boa().
# ---------------------------------------------------------------------------

def sniff_institution(header: list[str]) -> str | None:
    lowered = {h.strip().lower() for h in header}
    if {"transaction date", "description", "amount"} <= lowered:
        return "chase"
    if {"date", "description", "amount"} <= lowered and "running bal." in lowered:
        return "boa"
    return None


def sniff_institution_from_filename(name: str) -> str | None:
    lowered = name.lower()
    if "chase" in lowered:
        return "chase"
    if "boa" in lowered or "bankofamerica" in lowered or "bofa" in lowered:
        return "boa"
    return None


def read_csv_rows(path: Path) -> tuple[str | None, list[dict]]:
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        lines = f.readlines()

    header_idx = None
    institution = None
    for i, line in enumerate(lines):
        try:
            candidate = next(csv.reader([line]))
        except StopIteration:
            continue
        institution = sniff_institution(candidate)
        if institution:
            header_idx = i
            break

    if header_idx is None:
        return None, []

    reader = csv.DictReader(lines[header_idx:])
    return institution, list(reader)


def parse_chase(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        date = row.get("Transaction Date") or row.get("Post Date")
        amount = row.get("Amount")
        if not date or amount is None:
            continue
        out.append({
            "date": normalize_date(date),
            "description": (row.get("Description") or "").strip(),
            "amount": float(amount),
        })
    return out


def parse_boa(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        date = row.get("Date")
        amount_raw = (row.get("Amount") or "").strip().replace(",", "")
        if not date or not amount_raw:
            continue
        try:
            amount = float(amount_raw)
        except ValueError:
            continue
        out.append({
            "date": normalize_date(date),
            "description": (row.get("Description") or "").strip(),
            "amount": amount,
        })
    return out


PARSERS = {"chase": parse_chase, "boa": parse_boa}

# ---------------------------------------------------------------------------
# OFX/QFX parsing — SGML-style tags, no closing tag on leaf elements in
# OFX 1.x, so a small regex scan is simpler than a full SGML parser here.
# ---------------------------------------------------------------------------

OFX_TRN_RE = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.DOTALL | re.IGNORECASE)
OFX_FIELD_RE = re.compile(r"<(\w+)>([^<\r\n]*)")


def parse_ofx(text: str) -> list[dict]:
    out = []
    for block in OFX_TRN_RE.findall(text):
        fields = dict(OFX_FIELD_RE.findall(block))
        date_raw = (fields.get("DTPOSTED") or "")[:8]
        amount_raw = fields.get("TRNAMT")
        name = fields.get("NAME") or fields.get("MEMO") or ""
        if not date_raw or amount_raw is None:
            continue
        try:
            date = datetime.strptime(date_raw, "%Y%m%d").strftime("%Y-%m-%d")
            amount = float(amount_raw)
        except ValueError:
            continue
        out.append({"date": date, "description": name.strip(), "amount": amount})
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_import(model: str) -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    cache = dict(conn.execute("SELECT merchant_raw, category FROM merchant_categories").fetchall())

    files = sorted(p for p in INBOX_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    if not files:
        print("No files in inbox.")
        conn.close()
        return

    for path in files:
        ext = path.suffix.lower()

        if ext == ".csv":
            institution, rows = read_csv_rows(path)
            parser = PARSERS.get(institution or "")
            if not parser:
                institution = sniff_institution_from_filename(path.name)
                parser = PARSERS.get(institution or "")
            if not parser:
                print(f"skip (unrecognized CSV format): {path.name}")
                continue
            parsed = parser(rows)
        elif ext in (".ofx", ".qfx"):
            institution = sniff_institution_from_filename(path.name) or "unknown"
            parsed = parse_ofx(path.read_text(encoding="utf-8", errors="replace"))
        elif ext == ".pdf":
            dest = ARCHIVE_DIR / "unparsed" / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            print(f"archived unparsed (PDF, not extracted): {path.name}")
            continue
        else:
            print(f"skip (unsupported extension {ext}): {path.name}")
            continue

        if not parsed:
            print(f"skip (no transactions parsed): {path.name}")
            continue

        account_id = get_or_create_account(conn, institution, path.stem, None)
        import_id = conn.execute(
            "INSERT INTO imports (account_id, source_file) VALUES (?, ?)",
            (account_id, path.name),
        ).lastrowid

        inserted = 0
        for tx in parsed:
            merchant = normalize_merchant(tx["description"])
            category = categorize(merchant, model, cache)
            conn.execute(
                "INSERT OR REPLACE INTO merchant_categories (merchant_raw, category) VALUES (?, ?)",
                (merchant, category),
            )
            h = dedupe_hash(account_id, tx["date"], tx["amount"], tx["description"])
            cur = conn.execute(
                "INSERT OR IGNORE INTO transactions "
                "(account_id, import_id, date, description, amount, merchant_raw, category, dedupe_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, import_id, tx["date"], tx["description"], tx["amount"], merchant, category, h),
            )
            if cur.rowcount:
                inserted += 1
        conn.commit()

        dest_dir = ARCHIVE_DIR / institution
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_dir / path.name))
        print(f"{path.name}: {inserted}/{len(parsed)} new transactions ({institution})")

    conn.close()


def cmd_report(month: str | None, category: str | None) -> None:
    conn = get_db()
    query = "SELECT category, SUM(amount) AS total, COUNT(*) AS n FROM transactions WHERE 1=1"
    params: list = []
    if month:
        query += " AND date LIKE ?"
        params.append(f"{month}%")
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " GROUP BY category ORDER BY total ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    if not rows:
        print("No transactions found for the given filters.")
        return
    for cat, total, n in rows:
        print(f"{cat:15s} {total:10.2f}  ({n} txns)")


def cmd_search(query: str) -> None:
    conn = get_db()
    rows = conn.execute(
        "SELECT date, description, amount, category FROM transactions "
        "WHERE description LIKE ? ORDER BY date DESC LIMIT 50",
        (f"%{query}%",),
    ).fetchall()
    conn.close()
    if not rows:
        print("No matching transactions.")
        return
    for date, desc, amount, cat in rows:
        print(f"{date}  {amount:10.2f}  {cat:15s}  {desc}")


def cmd_benefits(month: str | None) -> None:
    """Cross-reference categorized Chase transactions against Chase Sapphire
    Reserve's benefit caps. Matching is heuristic (merchant substring or
    category) — treat results as directional, not an exact statement audit.
    """
    if not CSR_BENEFITS_PATH.exists():
        print(f"No benefit config at {CSR_BENEFITS_PATH}")
        return
    config = json.loads(CSR_BENEFITS_PATH.read_text(encoding="utf-8"))

    if month is None:
        today = datetime.today()
        month = f"{today.year:04d}-{today.month:02d}"
    year, mon = (int(p) for p in month.split("-"))

    last_day = calendar.monthrange(year, mon)[1]
    month_range = (f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last_day:02d}")
    half = 1 if mon <= 6 else 2
    half_range = (f"{year}-01-01", f"{year}-06-30") if half == 1 else (f"{year}-07-01", f"{year}-12-31")
    year_range = (f"{year}-01-01", f"{year}-12-31")

    anniversary = config.get("cardmember_anniversary")
    if anniversary is None:
        print(f"(no cardmember_anniversary set in {CSR_BENEFITS_PATH.name} — "
              f"treating annual_cardmember benefits as calendar year {year})")
        cardmember_range = year_range
    else:
        anniv_mon, anniv_day = (int(p) for p in anniversary.split("-"))
        start_year = year if (mon, 1) >= (anniv_mon, anniv_day) else year - 1
        # cardmember year covers 1 day before the anniversary a year later.
        # month can only exceed max valid day (e.g. Feb 29->28) on non-leap years.
        try:
            start = datetime(start_year, anniv_mon, anniv_day)
        except ValueError:
            start = datetime(start_year, anniv_mon, anniv_day - 1)
        try:
            end = datetime(start_year + 1, anniv_mon, anniv_day) - timedelta(days=1)
        except ValueError:
            end = datetime(start_year + 1, anniv_mon, anniv_day - 1) - timedelta(days=1)
        cardmember_range = (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    conn = get_db()

    def sum_credits(date_range: tuple[str, str], match: dict | None) -> tuple[float, int]:
        if not match:
            return 0.0, 0
        clauses = ["amount > 0", "date BETWEEN ? AND ?"]
        params: list = [date_range[0], date_range[1]]
        if "merchant_contains" in match:
            ors = " OR ".join(["merchant_raw LIKE ?"] * len(match["merchant_contains"]))
            clauses.append(f"({ors})")
            params.extend(f"%{m}%" for m in match["merchant_contains"])
        if "category" in match:
            ors = " OR ".join(["category = ?"] * len(match["category"]))
            clauses.append(f"({ors})")
            params.extend(match["category"])
        query = f"SELECT COALESCE(SUM(amount),0), COUNT(*) FROM transactions WHERE {' AND '.join(clauses)}"
        row = conn.execute(query, params).fetchone()
        return row[0], row[1]

    def last_match(match: dict | None) -> str | None:
        if not match or "merchant_contains" not in match:
            return None
        ors = " OR ".join(["merchant_raw LIKE ?"] * len(match["merchant_contains"]))
        params = [f"%{m}%" for m in match["merchant_contains"]]
        row = conn.execute(
            f"SELECT MAX(date) FROM transactions WHERE amount > 0 AND ({ors})", params
        ).fetchone()
        return row[0]

    print(f"Chase Sapphire Reserve benefit usage — as of {month}\n")
    for b in config["benefits"]:
        name = b["name"]
        period = b["period"]
        note = b.get("note", "")

        if period == "manual" or b.get("match") is None:
            print(f"- {name}: not trackable from transaction data. {note}")
            continue

        if period == "monthly":
            used, n = sum_credits(month_range, b["match"])
            cap = b["cap"]
            print(f"- {name}: ${used:.2f}/${cap:.2f} this month ({n} matching credit(s), "
                  f"${max(cap - used, 0):.2f} remaining). {note}")
        elif period == "semiannual":
            used, n = sum_credits(half_range, b["match"])
            cap = b["cap_h1"] if half == 1 else b["cap_h2"]
            half_label = "Jan-Jun" if half == 1 else "Jul-Dec"
            print(f"- {name}: ${used:.2f}/${cap:.2f} this half ({half_label} {year}, {n} credit(s), "
                  f"${max(cap - used, 0):.2f} remaining). {note}")
        elif period == "annual_calendar":
            used, n = sum_credits(year_range, b["match"])
            cap = b["cap"]
            print(f"- {name}: ${used:.2f}/${cap:.2f} this calendar year ({n} credit(s), "
                  f"${max(cap - used, 0):.2f} remaining). {note}")
        elif period == "annual_cardmember":
            used, n = sum_credits(cardmember_range, b["match"])
            cap = b["cap"]
            label = "approx. this year" if anniversary is None else f"this cardmember year ({cardmember_range[0]} to {cardmember_range[1]})"
            print(f"- {name}: ${used:.2f}/${cap:.2f} {label} ({n} matching credit(s), "
                  f"${max(cap - used, 0):.2f} remaining). {note}")
        elif period == "every_4_years":
            last = last_match(b["match"])
            if last:
                print(f"- {name}: last used {last} (cap ${b['cap']:.2f}, resets every 4 years). {note}")
            else:
                print(f"- {name}: no matching charge found in imported history. {note}")
        else:
            print(f"- {name}: unknown period '{period}' in config, skipped.")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    import_p = sub.add_parser("import", help="Import/categorize files from data/finance/inbox/")
    import_p.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model tag (default: {DEFAULT_MODEL})")

    report_p = sub.add_parser("report", help="Spending totals by category")
    report_p.add_argument("--month", help="Filter to YYYY-MM")
    report_p.add_argument("--category", help="Filter to a single category")

    search_p = sub.add_parser("search", help="Search transactions by description substring")
    search_p.add_argument("query")

    benefits_p = sub.add_parser("benefits", help="Chase Sapphire Reserve benefit usage vs. csr_benefits.json")
    benefits_p.add_argument("--month", help="YYYY-MM (default: current month)")

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args.model)
    elif args.command == "report":
        cmd_report(args.month, args.category)
    elif args.command == "search":
        cmd_search(args.query)
    elif args.command == "benefits":
        cmd_benefits(args.month)


if __name__ == "__main__":
    main()
