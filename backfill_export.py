#!/usr/bin/env python3
"""
backfill_export.py  –  One-shot backfill of a WHOOP account data export
                       (my_whoop_data_YYYY_MM_DD.zip) into whoop.db.

The developer API (whoop_db.py) can't fetch everything the account export
contains. This script adds the export-only data:

  • journal table        – daily journal questions/answers (alcohol,
                           caffeine, screen-in-bed, …); the API has no
                           journal endpoint at all.

                           PRIVACY: whoop.db is published, so the public
                           journal table only receives questions on the
                           PUBLIC_QUESTIONS allowlist and NEVER free-text
                           notes. The complete journal (all questions +
                           notes) is written to journal_private.db, which
                           is gitignored. Questions not on the allowlist —
                           including any new ones WHOOP adds later — stay
                           private until explicitly reviewed and added.
  • daily.sleep_need_min – WHOOP's computed nightly sleep need
  • daily.sleep_debt_min – accumulated sleep debt
  • daily.timezone_offset– per-day timezone (e.g. "-04:00"); the API only
                           exposes offsets per sleep.

Everything else in the export (strain, recovery, HRV, sleeps, workouts) is
already in the DB from the API — richer there, in fact — and is ignored.

MATCHING
--------
Export rows carry no cycle IDs, and "Cycle start time" is *local* sleep-onset
time (usually the evening before daily.date, which is the wake date). Rows
are matched to daily rows by converting the local start + "Cycle timezone"
to UTC and looking it up against daily.cycle_start (second precision, ±1 s);
unmatched rows fall back to containment in [cycle_start, cycle_end).

Idempotent: re-running (or running a newer export) replaces journal rows and
overwrites the three daily columns.

Usage: python backfill_export.py [--zip my_whoop_data_2026_07_24.zip]
                                 [--db whoop.db] [--dry-run]
"""

import argparse
import csv
import glob
import io
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Journal privacy allowlist
# ---------------------------------------------------------------------------
# Only these questions may appear in the public whoop.db. Everything else
# (and every free-text note, allowlisted or not) goes to journal_private.db
# only. Deliberately an allowlist: unreviewed/new questions default private.

PUBLIC_QUESTIONS = frozenset([
    "Ate food close to bedtime?",
    "Commuted to work?",
    "Consumed caffeine?",
    "Consumed carbohydrates?",
    "Consumed dairy?",
    "Consumed fats?",
    "Consumed fruits and/or vegetables?",
    "Consumed protein?",
    "Experienced fatigue?",
    "Experienced stress?",
    "Experiencing COVID-19 symptoms?",
    "Felt a sense of purpose?",
    "Felt you had control over your life?",
    "Felt you had the resources/skills needed to complete your daily goals?",
    "Followed an intermittent fasting diet?",
    "Following a vegetarian diet?",
    "Have an injury or wound?",
    "Have any alcoholic drinks?",
    "Hydrated sufficiently?",
    "Meditated?",
    "Parenting an infant?",
    "Read (non-screened device) while in bed?",
    "Saw direct sunlight upon waking up?",
    "Shared your bed?",
    "Slept in the same bed as usual?",
    "Spent time stretching?",
    "Took a cold shower?",
    "Took a magnesium supplement?",
    "Took an ice bath?",
    "Tracked your calories?",
    "Used a sauna?",
    "Viewed a screen device in bed?",
    "Wore blue-light blocking glasses before bed?",
    "Worked from home?",
])

PRIVATE_DB = "journal_private.db"

# ---------------------------------------------------------------------------
# Export parsing helpers
# ---------------------------------------------------------------------------


def parse_offset(tz: str) -> timedelta:
    """'UTC-04:00' -> timedelta(hours=-4)."""
    sign = -1 if "-" in tz else 1
    h, m = tz.replace("UTC", "").lstrip("+-").split(":")
    return sign * timedelta(hours=int(h), minutes=int(m))


def local_to_utc(local_str: str, tz: str) -> datetime:
    """'2026-07-22 22:53:34' + 'UTC-04:00' -> naive UTC datetime."""
    return datetime.fromisoformat(local_str) - parse_offset(tz)


def norm_offset(tz: str) -> str:
    """'UTC-04:00' -> '-04:00' (matches sleeps.timezone_offset format)."""
    return tz.replace("UTC", "")


def read_csv(zf: zipfile.ZipFile, name: str) -> list:
    with zf.open(name) as fh:
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))


# ---------------------------------------------------------------------------
# Cycle-start -> daily.date matching
# ---------------------------------------------------------------------------


class CycleMatcher:
    def __init__(self, con: sqlite3.Connection):
        self.by_start = {}   # UTC 'YYYY-MM-DDTHH:MM:SS' -> date
        self.windows = []    # (start_dt, end_dt|None, date)
        for date, cs, ce in con.execute(
                "SELECT date, cycle_start, cycle_end FROM daily WHERE cycle_start IS NOT NULL"):
            start = datetime.fromisoformat(cs.replace("Z", "")).replace(microsecond=0)
            end = datetime.fromisoformat(ce.replace("Z", "")) if ce else None
            self.by_start[start] = date
            self.windows.append((start, end, date))
        self.windows.sort()

    def match(self, local_str: str, tz: str):
        """Return daily.date for an export row, or None."""
        utc = local_to_utc(local_str, tz)
        for delta in (0, 1, -1):  # second-truncation tolerance
            hit = self.by_start.get(utc + timedelta(seconds=delta))
            if hit:
                return hit
        # fallback: cycle window containment (second cycle of a merged day, etc.)
        for start, end, date in self.windows:
            if start <= utc and (end is None or utc < end):
                return date
        return None


# ---------------------------------------------------------------------------
# Backfill steps
# ---------------------------------------------------------------------------


def ensure_schema(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(daily)")}
    for col, typ in [("sleep_need_min", "INTEGER"), ("sleep_debt_min", "INTEGER"),
                     ("timezone_offset", "TEXT")]:
        if col not in cols:
            con.execute(f"ALTER TABLE daily ADD COLUMN {col} {typ}")
    # Public journal is rebuilt from scratch every run so allowlist changes
    # (and redactions) always take full effect. No notes column: free-text
    # never enters the public db.
    con.execute("DROP TABLE IF EXISTS journal")
    con.execute("""
        CREATE TABLE journal (
            date            TEXT NOT NULL,   -- wake date (FK -> daily.date)
            cycle_start     TEXT NOT NULL,   -- UTC ISO-8601 of the cycle
            question        TEXT NOT NULL,   -- PUBLIC_QUESTIONS only
            answered_yes    INTEGER,         -- 0/1
            PRIMARY KEY (date, question, cycle_start)
        ) WITHOUT ROWID
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_journal_question ON journal(question)")


def ensure_private_schema(con: sqlite3.Connection) -> None:
    con.execute("DROP TABLE IF EXISTS journal")
    con.execute("""
        CREATE TABLE journal (
            date            TEXT NOT NULL,
            cycle_start     TEXT NOT NULL,
            question        TEXT NOT NULL,   -- ALL questions
            answered_yes    INTEGER,
            notes           TEXT,            -- free text, private only
            PRIMARY KEY (date, question, cycle_start)
        ) WITHOUT ROWID
    """)


def backfill_daily(con, matcher, cycles, dry: bool):
    updated = unmatched = 0
    seen = set()  # first cycle of a merged day wins (db convention)
    for r in cycles:
        date = matcher.match(r["Cycle start time"], r["Cycle timezone"])
        if date is None:
            unmatched += 1
            continue
        if date in seen:
            continue
        seen.add(date)
        need = int(float(r["Sleep need (min)"])) if r["Sleep need (min)"] else None
        debt = int(float(r["Sleep debt (min)"])) if r["Sleep debt (min)"] else None
        tz = norm_offset(r["Cycle timezone"])
        if not dry:
            con.execute(
                "UPDATE daily SET sleep_need_min=?, sleep_debt_min=?, timezone_offset=? WHERE date=?",
                (need, debt, tz, date))
        updated += 1
    return updated, unmatched


def backfill_journal(con, private_con, matcher, entries, dry: bool):
    public = private = unmatched = 0
    for r in entries:
        date = matcher.match(r["Cycle start time"], r["Cycle timezone"])
        if date is None:
            unmatched += 1
            continue
        utc = local_to_utc(r["Cycle start time"], r["Cycle timezone"])
        q = r["Question text"]
        yes = 1 if r["Answered yes"] == "true" else 0
        if not dry:
            private_con.execute(
                "INSERT OR REPLACE INTO journal (date, cycle_start, question, answered_yes, notes) "
                "VALUES (?,?,?,?,?)",
                (date, utc.isoformat() + "Z", q, yes, r["Notes"] or None))
        private += 1
        if q in PUBLIC_QUESTIONS:
            if not dry:
                con.execute(
                    "INSERT OR REPLACE INTO journal (date, cycle_start, question, answered_yes) "
                    "VALUES (?,?,?,?)",
                    (date, utc.isoformat() + "Z", q, yes))
            public += 1
    return public, private, unmatched


# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--zip", dest="zip_path",
                    default=sorted(glob.glob("my_whoop_data_*.zip"))[-1:] or None,
                    help="export zip (default: newest my_whoop_data_*.zip here)")
    ap.add_argument("--db", default="whoop.db")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args()

    zip_path = args.zip_path[0] if isinstance(args.zip_path, list) else args.zip_path
    if not zip_path:
        sys.exit("no my_whoop_data_*.zip found; pass --zip")

    zf = zipfile.ZipFile(zip_path)
    cycles = read_csv(zf, "physiological_cycles.csv")
    entries = read_csv(zf, "journal_entries.csv")

    con = sqlite3.connect(args.db)
    private_con = sqlite3.connect(PRIVATE_DB)
    try:
        if not args.dry_run:
            ensure_schema(con)
            ensure_private_schema(private_con)
        matcher = CycleMatcher(con)

        d_upd, d_un = backfill_daily(con, matcher, cycles, args.dry_run)
        j_pub, j_priv, j_un = backfill_journal(con, private_con, matcher, entries, args.dry_run)
        if not args.dry_run:
            con.commit()
            private_con.commit()

        tag = "[dry-run] " if args.dry_run else ""
        print(f"{tag}{zip_path} -> {args.db} (+ {PRIVATE_DB})")
        print(f"{tag}daily   : {d_upd} rows updated (need/debt/timezone), {d_un} export cycles unmatched")
        print(f"{tag}journal : {j_pub} public entries (allowlisted, no notes), "
              f"{j_priv} private entries, {j_un} unmatched")
        if not args.dry_run:
            hidden = con.execute(
                "SELECT COUNT(*) FROM journal WHERE question NOT IN (%s)"
                % ",".join("?" * len(PUBLIC_QUESTIONS)), tuple(PUBLIC_QUESTIONS)).fetchone()[0]
            cols = {r[1] for r in con.execute("PRAGMA table_info(journal)")}
            assert hidden == 0 and "notes" not in cols, "public journal failed redaction check"
            nn, = con.execute("SELECT COUNT(*) FROM daily WHERE sleep_need_min IS NOT NULL").fetchone()
            n, = con.execute("SELECT COUNT(*) FROM journal").fetchone()
            print(f"totals  : public journal={n} rows (redaction verified), "
                  f"daily rows with sleep_need={nn}")
    finally:
        con.close()
        private_con.close()


if __name__ == "__main__":
    main()
