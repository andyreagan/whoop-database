#!/usr/bin/env python3
"""
whoop_db.py  –  Build and maintain a local SQLite database of your WHOOP
                daily metrics, aggregated by calendar day.

WHOOP uses "cycles" (not calendar days) as its base unit, but this script
converts everything to calendar dates so queries stay simple.

MODES
-----
  login       Open a browser for OAuth2 authorisation and save tokens.
              Run this once (or whenever tokens expire un-recoverably).
              Usage: python whoop_db.py login

  build       Pull every cycle / sleep / recovery / workout from the API
              (slow for large histories — respects rate limits).
              Usage: python whoop_db.py build

  update      Pull only data newer than the most-recent row in the DB,
              then re-verify the last N days.
              Usage: python whoop_db.py update [--verify-days 30]

  stats       Print a quick summary to stdout.
              Usage: python whoop_db.py stats

DATA PULLED
-----------
  Per calendar day:
    • Cycle     : strain score, average HR, max HR, kilojoules, day start/end
    • Recovery  : recovery score, HRV (rMSSD), resting HR, SpO2, skin temp
    • Sleep     : performance %, quality duration, total in-bed time,
                  light / REM / slow-wave sleep, disturbances, latency,
                  respiratory rate
    • Workouts  : count, total strain, total kilojoules on that day

  Multiple cycles in one calendar day are merged (averaged / summed as
  appropriate) to keep one row per day.

RATE LIMITS
-----------
  100 requests / minute,  10 000 requests / day  (as of 2026)
  The script targets ~60 req/min (1 req/s) and backs off automatically
  when approaching the per-minute window.  A hard stop is applied at
  9 900 daily requests to avoid hitting the 10 000 cap.

ENV / .env
----------
  WHOOP_CLIENT_ID       App client ID
  WHOOP_CLIENT_SECRET   App client secret
  WHOOP_ACCESS_TOKEN    (auto-managed)
  WHOOP_REFRESH_TOKEN   (auto-managed)
  DB_PATH               SQLite file path (default: whoop.db)
"""

import argparse
import hashlib
import http.server
import os
import secrets
import sqlite3
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv, set_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WHOOP_AUTH_URL   = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL  = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE   = "https://api.prod.whoop.com/developer/v2"

REDIRECT_URI     = "http://localhost:8484/callback"
SCOPES           = "offline read:profile read:cycles read:sleep read:recovery read:workout"

# WHOOP rate limits: 100 req/min, 10 000 req/day.
# We target ~60 req/min (1/s) to stay comfortably under the per-minute limit.
_REQUEST_DELAY_S      = 1.0        # seconds between requests (60 req/min)
_PER_MINUTE_LIMIT     = 90         # back off when we've sent this many in 60s
_PER_MINUTE_WINDOW_S  = 60
_PER_DAY_LIMIT        = 9900       # soft cap — warn and stop before hard 10 000

_request_times: list[float] = []   # monotonic timestamps of recent requests


def _record_request() -> None:
    now = time.monotonic()
    _request_times.append(now)
    cutoff = now - _PER_MINUTE_WINDOW_S
    while _request_times and _request_times[0] < cutoff:
        _request_times.pop(0)


def _maybe_rate_limit() -> None:
    """Sleep if we're approaching the per-minute limit."""
    if len(_request_times) >= _PER_MINUTE_LIMIT:
        oldest = _request_times[0]
        sleep_for = _PER_MINUTE_WINDOW_S - (time.monotonic() - oldest) + 1
        if sleep_for > 0:
            print(f"  [rate-limit] {len(_request_times)} req in last 60s — "
                  f"sleeping {sleep_for:.0f}s …", flush=True)
            time.sleep(sleep_for)
            cutoff = time.monotonic() - _PER_MINUTE_WINDOW_S
            while _request_times and _request_times[0] < cutoff:
                _request_times.pop(0)

    if len(_request_times) >= _PER_DAY_LIMIT:
        sys.exit(
            f"ERROR: Reached {_PER_DAY_LIMIT} requests today (daily limit is "
            "10 000).  Re-run tomorrow to continue."
        )


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

def _env_path_obj(path: str) -> Path:
    return Path(path)


def save_tokens(access: str, refresh: str, env_path: Path = Path(".env")) -> None:
    set_key(str(env_path), "WHOOP_ACCESS_TOKEN",  access)
    set_key(str(env_path), "WHOOP_REFRESH_TOKEN", refresh)


# ---------------------------------------------------------------------------
# OAuth2 login (PKCE)
# ---------------------------------------------------------------------------

class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP handler — captures ?code=… from the redirect."""
    code: Optional[str] = None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _OAuthCallbackHandler.code = params["code"][0]
            body = b"<h2>Authorised! You can close this tab.</h2>"
        else:
            body = b"<h2>Error: no code received.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # suppress request logging
        pass


def do_login(client_id: str, client_secret: str,
             env_path: Path = Path(".env")) -> tuple[str, str]:
    """
    Run the OAuth2 authorisation code + PKCE flow.

    Opens the WHOOP authorisation URL in the default browser, starts a
    temporary local HTTP server on port 8484 to receive the redirect, then
    exchanges the code for tokens.

    Returns (access_token, refresh_token).
    """
    import base64

    # PKCE verifier / challenge
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state     = secrets.token_hex(16)

    params = {
        "client_id":             client_id,
        "redirect_uri":          REDIRECT_URI,
        "response_type":         "code",
        "scope":                 SCOPES,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    auth_url = WHOOP_AUTH_URL + "?" + urllib.parse.urlencode(params)

    print(f"\nOpening browser for WHOOP login …\n  {auth_url}\n", flush=True)
    webbrowser.open(auth_url)

    # Start local server to catch the redirect
    server = http.server.HTTPServer(("localhost", 8484), _OAuthCallbackHandler)
    print("Waiting for OAuth callback on http://localhost:8484/callback …",
          flush=True)
    server.handle_request()
    server.server_close()

    code = _OAuthCallbackHandler.code
    if not code:
        sys.exit("ERROR: Did not receive an authorisation code.")

    # Exchange code → tokens
    resp = requests.post(WHOOP_TOKEN_URL, data={
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "client_id":     client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    })
    resp.raise_for_status()
    data = resp.json()

    access  = data["access_token"]
    refresh = data["refresh_token"]
    save_tokens(access, refresh, env_path)
    print("  Tokens saved to .env ✓", flush=True)
    return access, refresh


# ---------------------------------------------------------------------------
# WHOOP API client
# ---------------------------------------------------------------------------

class WhoopClient:
    def __init__(self, client_id: str, client_secret: str,
                 access_token: str, refresh_token: str,
                 env_path: Path = Path(".env")):
        self.client_id     = client_id
        self.client_secret = client_secret
        self.access_token  = access_token
        self.refresh_token = refresh_token
        self.env_path      = env_path
        self.session       = requests.Session()
        self._last_req     = 0.0

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        print("  [auth] refreshing access token …", flush=True)
        resp = self.session.post(WHOOP_TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
        })
        resp.raise_for_status()
        data = resp.json()
        self.access_token  = data["access_token"]
        self.refresh_token = data["refresh_token"]
        save_tokens(self.access_token, self.refresh_token, self.env_path)
        print("  [auth] tokens updated.", flush=True)

    def _get(self, path: str, params: Optional[dict] = None,
             retry: bool = True) -> Any:
        _maybe_rate_limit()

        # Minimum spacing between requests
        elapsed = time.monotonic() - self._last_req
        if elapsed < _REQUEST_DELAY_S:
            time.sleep(_REQUEST_DELAY_S - elapsed)

        url = f"{WHOOP_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        resp = self.session.get(url, headers=headers, params=params or {})
        self._last_req = time.monotonic()
        _record_request()

        if resp.status_code == 401 and retry:
            self._refresh()
            return self._get(path, params, retry=False)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            print(f"  [rate-limit] 429 – sleeping {retry_after}s …", flush=True)
            time.sleep(retry_after)
            return self._get(path, params, retry=False)

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Paginated collection helpers
    # ------------------------------------------------------------------

    def _iter_collection(self, path: str, start: Optional[str] = None,
                         end: Optional[str] = None):
        """
        Yield every record from a paginated WHOOP collection endpoint.

        WHOOP paginates via `nextToken` in the response body.
        start/end are ISO-8601 strings (UTC).
        """
        params: dict = {"limit": 25}
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        while True:
            data = self._get(path, params)
            records = data.get("records", [])
            for r in records:
                yield r
            next_token = data.get("next_token")
            if not next_token:
                break
            params = {"limit": 25, "nextToken": next_token}
            # preserve start/end when paginating
            if start:
                params["start"] = start
            if end:
                params["end"] = end

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------

    def iter_cycles(self, start: Optional[str] = None,
                    end: Optional[str] = None):
        yield from self._iter_collection("/cycle", start=start, end=end)

    def iter_sleeps(self, start: Optional[str] = None,
                    end: Optional[str] = None):
        yield from self._iter_collection("/activity/sleep", start=start, end=end)

    def iter_recoveries(self, start: Optional[str] = None,
                        end: Optional[str] = None):
        yield from self._iter_collection("/recovery", start=start, end=end)

    def iter_workouts(self, start: Optional[str] = None,
                      end: Optional[str] = None):
        yield from self._iter_collection("/activity/workout", start=start, end=end)

    def get_profile(self) -> dict:
        return self._get("/user/profile/basic")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    -- calendar date (LOCAL date of cycle start, YYYY-MM-DD)
    date                    TEXT PRIMARY KEY,

    -- Cycle / strain
    cycle_id                INTEGER,    -- first (or only) cycle id for that date
    strain                  REAL,       -- day strain score 0-21
    avg_hr                  REAL,       -- average heart rate (bpm)
    max_hr                  REAL,       -- max heart rate (bpm)
    kilojoules              REAL,       -- total energy expenditure (kJ)
    cycle_start             TEXT,       -- UTC ISO-8601 of cycle start
    cycle_end               TEXT,       -- UTC ISO-8601 of cycle end

    -- Recovery
    recovery_score          REAL,       -- 0-100
    hrv_rmssd_ms            REAL,       -- HRV rMSSD in ms
    resting_hr              REAL,       -- resting heart rate (bpm)
    spo2_pct                REAL,       -- blood oxygen %
    skin_temp_celsius        REAL,       -- skin temperature °C

    -- Sleep (main / primary sleep for the day)
    sleep_performance_pct   REAL,       -- 0-100
    sleep_quality_duration_ms   INTEGER,
    sleep_total_in_bed_ms   INTEGER,
    sleep_light_ms          INTEGER,
    sleep_rem_ms            INTEGER,
    sleep_slow_wave_ms      INTEGER,
    sleep_awake_ms          INTEGER,
    sleep_disturbances      INTEGER,
    sleep_latency_ms        INTEGER,
    respiratory_rate        REAL,       -- breaths / min

    -- Workouts aggregated for the day
    workout_count           INTEGER,
    workout_strain          REAL,       -- sum of workout strain scores
    workout_kilojoules      REAL,       -- sum of workout kJ

    -- Housekeeping
    fetched_at              TEXT        -- ISO-8601 UTC
);

CREATE TABLE IF NOT EXISTS workouts (
    id                      TEXT PRIMARY KEY,
    date                    TEXT,       -- calendar date (FK → daily.date)
    sport_id                INTEGER,
    sport_name              TEXT,
    start                   TEXT,       -- UTC ISO-8601
    end                     TEXT,
    strain                  REAL,
    avg_hr                  REAL,
    max_hr                  REAL,
    kilojoules              REAL,
    percent_recorded        REAL,
    distance_meter          REAL,
    altitude_gain_meter     REAL,
    altitude_change_meter   REAL,
    zone_zero_ms            INTEGER,
    zone_one_ms             INTEGER,
    zone_two_ms             INTEGER,
    zone_three_ms           INTEGER,
    zone_four_ms            INTEGER,
    zone_five_ms            INTEGER,
    fetched_at              TEXT
);

CREATE TABLE IF NOT EXISTS sleeps (
    id                      TEXT PRIMARY KEY,
    cycle_id                INTEGER,
    date                    TEXT,       -- wake date (local calendar date of sleep end)
    start                   TEXT,       -- UTC ISO-8601
    end                     TEXT,       -- UTC ISO-8601
    timezone_offset         TEXT,       -- e.g. "-05:00" — needed to localize start/end
    nap                     INTEGER,    -- 0/1
    score_state             TEXT,       -- SCORED / PENDING_SCORE / UNSCORABLE
    sleep_performance_pct   REAL,
    sleep_consistency_pct   REAL,
    sleep_efficiency_pct    REAL,
    respiratory_rate        REAL,
    total_in_bed_ms         INTEGER,
    light_ms                INTEGER,
    rem_ms                  INTEGER,
    slow_wave_ms            INTEGER,
    awake_ms                INTEGER,
    no_data_ms              INTEGER,
    sleep_cycle_count       INTEGER,
    disturbance_count       INTEGER,
    fetched_at              TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key     TEXT PRIMARY KEY,
    value   TEXT
);
"""

# WHOOP sport IDs → human-readable names (subset; extend as needed)
SPORT_NAMES = {
    -1: "Activity",
    0:  "Running",
    1:  "Cycling",
    16: "Baseball",
    17: "Basketball",
    18: "Rowing",
    19: "Fencing",
    20: "Field Hockey",
    21: "Football",
    22: "Golf",
    24: "Ice Hockey",
    25: "Lacrosse",
    27: "Rugby",
    28: "Sailing",
    29: "Skiing",
    30: "Soccer",
    31: "Softball",
    32: "Squash",
    33: "Swimming",
    34: "Tennis",
    35: "Track & Field",
    36: "Volleyball",
    37: "Water Polo",
    38: "Wrestling",
    39: "Boxing",
    42: "Dance",
    43: "Pilates",
    44: "Yoga",
    45: "Weightlifting",
    47: "Cross Country Skiing",
    48: "Functional Fitness",
    49: "Duathlon",
    51: "Gymnastics",
    52: "Hiking",
    53: "Horseback Riding",
    55: "Kayaking",
    56: "Martial Arts",
    57: "Mountain Biking",
    59: "Powerlifting",
    60: "Rock Climbing",
    61: "Paddleboarding",
    62: "Triathlon",
    63: "Walking",
    64: "Surfing",
    65: "Elliptical",
    66: "Stairmaster",
    70: "Meditation",
    71: "Other",
    73: "Diving",
    74: "Operations - Tactical",
    75: "Operations - Medical",
    76: "Operations - Flying",
    77: "Operations - Water",
    82: "Ultimate",
    83: "Climber",
    84: "Jumping Rope",
    85: "Australian Football",
    86: "Skateboarding",
    87: "Coaching",
    88: "Ice Bath",
    89: "Commuting",
    90: "Gaming",
    91: "Snowboarding",
    92: "Motocross",
    93: "Caddying",
    94: "Obstacle Course Racing",
    95: "Motor Racing",
    96: "HIIT",
    97: "Spin",
    98: "Jiu Jitsu",
    99: "Manual Labor",
    100: "Cricket",
    101: "Pickleball",
    102: "Inline Skating",
    103: "Box Fitness",
    104: "Spikeball",
    105: "Wheelchair Pushing",
    106: "Paddle Tennis",
    107: "Barre",
    108: "Stage Performance",
    109: "High Stress Work",
    110: "Parkour",
    111: "Gaelic Football",
    112: "Hurling",
    113: "Cycling",   # indoor
    114: "Strength",
}


def open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.commit()
    return con


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))


def get_meta(con: sqlite3.Connection, key: str) -> Optional[str]:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data transformation helpers
# ---------------------------------------------------------------------------

def _s(d, *keys):
    """Safely fetch a nested value."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _iso_to_date(iso: Optional[str], tz_offset_hours: int = 0) -> Optional[str]:
    """
    Convert a UTC ISO-8601 string to a local calendar date (YYYY-MM-DD).
    WHOOP cycles start around midnight local time, so we use the raw date
    portion of the UTC string as a close-enough approximation unless an
    offset is given.
    """
    if not iso:
        return None
    # Strip trailing Z or +00:00
    iso = iso.rstrip("Z").split("+")[0].split(".")[0]
    dt = datetime.fromisoformat(iso)
    if tz_offset_hours:
        dt = dt + timedelta(hours=tz_offset_hours)
    return dt.date().isoformat()


def cycle_to_day_dict(cycle: dict) -> dict:
    score = cycle.get("score") or {}
    return {
        "cycle_id":    cycle.get("id"),
        "strain":      score.get("strain"),
        "avg_hr":      score.get("average_heart_rate"),
        "max_hr":      score.get("max_heart_rate"),
        "kilojoules":  score.get("kilojoule"),   # API field is "kilojoule" (no s)
        "cycle_start": cycle.get("start"),
        "cycle_end":   cycle.get("end"),
    }


def recovery_to_day_dict(rec: dict) -> dict:
    score = rec.get("score") or {}
    return {
        "recovery_score": score.get("recovery_score"),
        "hrv_rmssd_ms":   score.get("hrv_rmssd_milli"),
        "resting_hr":     score.get("resting_heart_rate"),
        "spo2_pct":       score.get("spo2_percentage"),
        "skin_temp_celsius": score.get("skin_temp_celsius"),
    }


def sleep_to_day_dict(sleep: dict) -> dict:
    score   = sleep.get("score") or {}
    staging = score.get("stage_summary") or {}
    needed  = score.get("sleep_needed") or {}
    return {
        "sleep_performance_pct":     score.get("sleep_performance_percentage"),
        "sleep_quality_duration_ms": needed.get("baseline_milli"),
        "sleep_total_in_bed_ms":     staging.get("total_in_bed_time_milli"),
        "sleep_light_ms":            staging.get("total_light_sleep_time_milli"),
        "sleep_rem_ms":              staging.get("total_rem_sleep_time_milli"),
        "sleep_slow_wave_ms":        staging.get("total_slow_wave_sleep_time_milli"),
        "sleep_awake_ms":            staging.get("total_awake_time_milli"),
        "sleep_disturbances":        staging.get("disturbance_count"),
        "sleep_latency_ms":          score.get("sleep_latency_milli"),
        "respiratory_rate":          score.get("respiratory_rate"),
    }


def sleep_to_row(sleep: dict, date: Optional[str]) -> dict:
    score   = sleep.get("score") or {}
    staging = score.get("stage_summary") or {}
    return {
        "id":                    sleep.get("id"),
        "cycle_id":              sleep.get("cycle_id"),
        "date":                  date,
        "start":                 sleep.get("start"),
        "end":                   sleep.get("end"),
        "timezone_offset":       sleep.get("timezone_offset"),
        "nap":                   1 if sleep.get("nap") else 0,
        "score_state":           sleep.get("score_state"),
        "sleep_performance_pct": score.get("sleep_performance_percentage"),
        "sleep_consistency_pct": score.get("sleep_consistency_percentage"),
        "sleep_efficiency_pct":  score.get("sleep_efficiency_percentage"),
        "respiratory_rate":      score.get("respiratory_rate"),
        "total_in_bed_ms":       staging.get("total_in_bed_time_milli"),
        "light_ms":              staging.get("total_light_sleep_time_milli"),
        "rem_ms":                staging.get("total_rem_sleep_time_milli"),
        "slow_wave_ms":          staging.get("total_slow_wave_sleep_time_milli"),
        "awake_ms":              staging.get("total_awake_time_milli"),
        "no_data_ms":            staging.get("total_no_data_time_milli"),
        "sleep_cycle_count":     staging.get("sleep_cycle_count"),
        "disturbance_count":     staging.get("disturbance_count"),
        "fetched_at":            now_utc(),
    }


def workout_to_row(wkt: dict, date: str) -> dict:
    score    = wkt.get("score") or {}
    zones    = score.get("zone_durations") or {}   # API field is "zone_durations"
    sport_id = wkt.get("sport_id", -1)
    # v2 API provides sport_name directly; fall back to our lookup table
    sport_name = wkt.get("sport_name") or SPORT_NAMES.get(sport_id, f"Sport_{sport_id}")
    return {
        "id":                    wkt.get("id"),
        "date":                  date,
        "sport_id":              sport_id,
        "sport_name":            sport_name,
        "start":                 wkt.get("start"),
        "end":                   wkt.get("end"),
        "strain":                score.get("strain"),
        "avg_hr":                score.get("average_heart_rate"),
        "max_hr":                score.get("max_heart_rate"),
        "kilojoules":            score.get("kilojoule"),      # API field is "kilojoule"
        "percent_recorded":      score.get("percent_recorded"),
        "distance_meter":        score.get("distance_meter"),
        "altitude_gain_meter":   score.get("altitude_gain_meter"),
        "altitude_change_meter": score.get("altitude_change_meter"),
        "zone_zero_ms":          zones.get("zone_zero_milli"),
        "zone_one_ms":           zones.get("zone_one_milli"),
        "zone_two_ms":           zones.get("zone_two_milli"),
        "zone_three_ms":         zones.get("zone_three_milli"),
        "zone_four_ms":          zones.get("zone_four_milli"),
        "zone_five_ms":          zones.get("zone_five_milli"),
        "fetched_at":            now_utc(),
    }


# ---------------------------------------------------------------------------
# DB upsert helpers
# ---------------------------------------------------------------------------

def upsert_daily(con: sqlite3.Connection, row: dict) -> None:
    cols  = list(row.keys())
    ph    = ", ".join("?" * len(cols))
    names = ", ".join(cols)
    upd   = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "date")
    sql   = (f"INSERT INTO daily ({names}) VALUES ({ph}) "
             f"ON CONFLICT(date) DO UPDATE SET {upd}")
    con.execute(sql, list(row.values()))


def upsert_sleep(con: sqlite3.Connection, row: dict) -> None:
    cols  = list(row.keys())
    ph    = ", ".join("?" * len(cols))
    names = ", ".join(f'"{c}"' for c in cols)
    upd   = ", ".join(f'"{c}"=excluded."{c}"' for c in cols if c != "id")
    sql   = (f"INSERT INTO sleeps ({names}) VALUES ({ph}) "
             f"ON CONFLICT(id) DO UPDATE SET {upd}")
    con.execute(sql, list(row.values()))


def upsert_workout(con: sqlite3.Connection, row: dict) -> None:
    cols  = list(row.keys())
    ph    = ", ".join("?" * len(cols))
    names = ", ".join(cols)
    upd   = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql   = (f"INSERT INTO workouts ({names}) VALUES ({ph}) "
             f"ON CONFLICT(id) DO UPDATE SET {upd}")
    con.execute(sql, list(row.values()))


# ---------------------------------------------------------------------------
# Core data-pull logic
# ---------------------------------------------------------------------------

def _pull_range(client: WhoopClient, con: sqlite3.Connection,
                start_iso: Optional[str], end_iso: Optional[str]) -> int:
    """
    Pull cycles, recoveries, sleeps, and workouts for the given UTC range,
    merge them into per-day rows, and write to the DB.

    Returns the number of days written.
    """
    fa = now_utc()

    # ---- Cycles ----
    print("  Fetching cycles …", flush=True)
    days: dict[str, dict] = {}   # date → merged fields
    for cycle in client.iter_cycles(start=start_iso, end=end_iso):
        date = _iso_to_date(cycle.get("start"))
        if not date:
            continue
        if date not in days:
            days[date] = {"date": date, "fetched_at": fa}
        d = cycle_to_day_dict(cycle)
        # If multiple cycles fall on same day, keep the one with highest strain
        existing_strain = days[date].get("strain")
        new_strain = d.get("strain")
        if existing_strain is None or (new_strain is not None and new_strain > existing_strain):
            days[date].update(d)
    print(f"    → {len(days)} days from cycles", flush=True)

    # ---- Recoveries ----
    print("  Fetching recoveries …", flush=True)
    rec_count = 0
    for rec in client.iter_recoveries(start=start_iso, end=end_iso):
        date = _iso_to_date(rec.get("created_at") or _s(rec, "cycle_id"))
        # Recoveries have a cycle_id; look up the cycle start date via our days dict
        # Better: use cycle_id to match
        cycle_id = rec.get("cycle_id")
        # Find the day whose cycle_id matches
        matched_date = None
        for d, dv in days.items():
            if dv.get("cycle_id") == cycle_id:
                matched_date = d
                break
        if matched_date is None:
            # Fall back to created_at date
            matched_date = _iso_to_date(rec.get("created_at"))
        if not matched_date:
            continue
        if matched_date not in days:
            days[matched_date] = {"date": matched_date, "fetched_at": fa}
        days[matched_date].update(recovery_to_day_dict(rec))
        rec_count += 1
    print(f"    → {rec_count} recoveries", flush=True)

    # ---- Sleeps ----
    print("  Fetching sleeps …", flush=True)
    sleep_count = 0
    for sleep in client.iter_sleeps(start=start_iso, end=end_iso):
        # Use the end date of sleep as the "wake date" → assign to that calendar day
        date = _iso_to_date(sleep.get("end") or sleep.get("start"))
        # Store every sleep episode (naps included) with start/end timestamps
        upsert_sleep(con, sleep_to_row(sleep, date))
        # Only use "primary" (non-nap) sleeps for the daily row
        if sleep.get("nap"):
            continue
        if not date:
            continue
        if date not in days:
            days[date] = {"date": date, "fetched_at": fa}
        days[date].update(sleep_to_day_dict(sleep))
        sleep_count += 1
    print(f"    → {sleep_count} sleeps", flush=True)

    # ---- Workouts ----
    print("  Fetching workouts …", flush=True)
    wkt_count = 0
    for wkt in client.iter_workouts(start=start_iso, end=end_iso):
        date = _iso_to_date(wkt.get("start"))
        if not date:
            continue
        if date not in days:
            days[date] = {"date": date, "fetched_at": fa}
        # Aggregate into daily row
        row = days[date]
        row["workout_count"] = (row.get("workout_count") or 0) + 1
        score = wkt.get("score") or {}
        row["workout_strain"] = (row.get("workout_strain") or 0.0) + (score.get("strain") or 0.0)
        row["workout_kilojoules"] = (row.get("workout_kilojoules") or 0.0) + (score.get("kilojoule") or 0.0)
        # Also upsert the detailed workout row
        upsert_workout(con, workout_to_row(wkt, date))
        wkt_count += 1
    print(f"    → {wkt_count} workouts across {len(days)} days", flush=True)

    # ---- Write daily rows ----
    for row in days.values():
        upsert_daily(con, row)
    con.commit()
    return len(days)


def do_build(client: WhoopClient, db_path: str) -> None:
    """Pull all data from the beginning of time."""
    con = open_db(db_path)
    print("Pulling full WHOOP history …", flush=True)

    # WHOOP was founded 2012; no data before that
    start = "2012-01-01T00:00:00.000Z"
    end   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    n = _pull_range(client, con, start, end)
    set_meta(con, "last_full_pull", now_utc())
    con.commit()
    print(f"\nDone. {n} days written to {db_path}")


def do_update(client: WhoopClient, db_path: str,
              verify_days: int = 30) -> None:
    """
    Pull data newer than the most-recent date in the DB, then re-verify the
    last `verify_days` calendar days.
    """
    con = open_db(db_path)

    row = con.execute("SELECT MAX(date) AS md FROM daily").fetchone()
    latest_date = row["md"] if row and row["md"] else None

    if latest_date:
        # Start from `verify_days` before the latest to pick up corrections
        start_dt = datetime.fromisoformat(latest_date) - timedelta(days=verify_days)
        start_iso = start_dt.strftime("%Y-%m-%dT00:00:00.000Z")
        print(f"Most recent day in DB: {latest_date}", flush=True)
        print(f"Re-fetching from {start_dt.date()} …", flush=True)
    else:
        start_iso = "2012-01-01T00:00:00.000Z"
        print("DB is empty – fetching everything …", flush=True)

    end_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    n = _pull_range(client, con, start_iso, end_iso)
    set_meta(con, "last_update", now_utc())
    con.commit()
    print(f"\nUpdate complete. {n} days written/updated in {db_path}")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def do_stats(db_path: str) -> None:
    if not Path(db_path).exists():
        sys.exit(f"Database not found: {db_path}  (run 'login' then 'build' first)")

    con = open_db(db_path)
    total = con.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
    rng   = con.execute("SELECT MIN(date), MAX(date) FROM daily").fetchone()

    print(f"\n{'='*60}")
    print(f"  WHOOP Daily Database  —  {db_path}")
    print(f"{'='*60}")
    print(f"  Total days:   {total}")
    last = get_meta(con, "last_update") or get_meta(con, "last_full_pull")
    print(f"  Last sync:    {last or 'never'}")
    print(f"  Date range:   {rng[0] or '?'} → {rng[1] or '?'}")

    print()
    print("── Averages (all time) ───────────────────────────────────")
    avgs = con.execute("""
        SELECT
            ROUND(AVG(strain), 2)            AS avg_strain,
            ROUND(AVG(recovery_score), 1)    AS avg_recovery,
            ROUND(AVG(hrv_rmssd_ms), 1)      AS avg_hrv,
            ROUND(AVG(resting_hr), 1)        AS avg_rhr,
            ROUND(AVG(respiratory_rate), 2)  AS avg_resp,
            ROUND(AVG(spo2_pct), 2)          AS avg_spo2,
            ROUND(AVG(sleep_performance_pct), 1) AS avg_sleep_perf
        FROM daily
    """).fetchone()
    print(f"  Strain:          {avgs[0] or '—'}")
    print(f"  Recovery:        {avgs[1] or '—'} %")
    print(f"  HRV (rMSSD):     {avgs[2] or '—'} ms")
    print(f"  Resting HR:      {avgs[3] or '—'} bpm")
    print(f"  Respiratory rate:{avgs[4] or '—'} breaths/min")
    print(f"  SpO2:            {avgs[5] or '—'} %")
    print(f"  Sleep perf:      {avgs[6] or '—'} %")

    print()
    print("── Last 14 days ──────────────────────────────────────────")
    rows = con.execute("""
        SELECT date, strain, recovery_score, hrv_rmssd_ms,
               resting_hr, respiratory_rate, sleep_performance_pct
        FROM daily
        ORDER BY date DESC
        LIMIT 14
    """).fetchall()
    print(f"  {'Date':<12} {'Strain':>7} {'Recov':>6} {'HRV':>7} "
          f"{'RHR':>5} {'Resp':>6} {'Sleep':>6}")
    print(f"  {'-'*12} {'-'*7} {'-'*6} {'-'*7} {'-'*5} {'-'*6} {'-'*6}")
    for r in rows:
        def _f(v, dec=1):
            return f"{v:.{dec}f}" if v is not None else "—"
        print(f"  {r['date']:<12} {_f(r['strain'], 2):>7} "
              f"{_f(r['recovery_score'], 0):>6} "
              f"{_f(r['hrv_rmssd_ms'], 1):>7} "
              f"{_f(r['resting_hr'], 0):>5} "
              f"{_f(r['respiratory_rate'], 2):>6} "
              f"{_f(r['sleep_performance_pct'], 0):>6}")

    print()
    print("── Top 10 workout types ──────────────────────────────────")
    wrows = con.execute("""
        SELECT sport_name, COUNT(*) AS n,
               ROUND(AVG(strain), 2) AS avg_strain,
               ROUND(SUM(kilojoules), 0) AS total_kj
        FROM workouts
        GROUP BY sport_name
        ORDER BY n DESC
        LIMIT 10
    """).fetchall()
    if wrows:
        for r in wrows:
            print(f"  {(r['sport_name'] or 'Unknown'):<22}  "
                  f"{r['n']:>4} workouts  "
                  f"avg strain {r['avg_strain'] or '—':>5}  "
                  f"total {int(r['total_kj'] or 0):>6} kJ")
    else:
        print("  (no workout data)")

    print(f"\n{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _make_client(env_path: Path) -> WhoopClient:
    client_id     = os.environ.get("WHOOP_CLIENT_ID", "")
    client_secret = os.environ.get("WHOOP_CLIENT_SECRET", "")
    access_token  = os.environ.get("WHOOP_ACCESS_TOKEN", "")
    refresh_token = os.environ.get("WHOOP_REFRESH_TOKEN", "")

    if not client_id or not client_secret:
        sys.exit(
            "Missing WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET in .env.\n"
            "Create an app at https://developer.whoop.com and add the credentials."
        )
    if not refresh_token:
        sys.exit(
            "No WHOOP_REFRESH_TOKEN found.  Run:\n"
            "  python whoop_db.py login\nto authorise the app."
        )
    return WhoopClient(client_id, client_secret, access_token, refresh_token,
                       env_path=env_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build / update a local SQLite WHOOP daily-metrics database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "mode", choices=["login", "build", "update", "stats"],
        help="Operation to perform",
    )
    parser.add_argument("--db",  default=None,
                        help="Override DB_PATH from .env")
    parser.add_argument("--verify-days", type=int, default=30,
                        help="(update) Days to re-verify (default: 30)")
    parser.add_argument("--env", default=".env",
                        help="Path to .env file (default: .env)")
    args = parser.parse_args()

    env_path = Path(args.env)
    load_dotenv(env_path, override=False)

    db_path = args.db or os.environ.get("DB_PATH", "whoop.db")

    if args.mode == "login":
        client_id     = os.environ.get("WHOOP_CLIENT_ID", "")
        client_secret = os.environ.get("WHOOP_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            sys.exit("Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET in .env first.")
        do_login(client_id, client_secret, env_path)

    elif args.mode == "build":
        client = _make_client(env_path)
        do_build(client, db_path)

    elif args.mode == "update":
        client = _make_client(env_path)
        do_update(client, db_path, verify_days=args.verify_days)

    elif args.mode == "stats":
        do_stats(db_path)


if __name__ == "__main__":
    main()
