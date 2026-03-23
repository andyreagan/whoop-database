# whoop-database

A local SQLite database of your daily WHOOP metrics, with a CLI to build and
keep it up-to-date.

WHOOP tracks data in *cycles* rather than calendar days, but this script
converts everything to calendar dates so queries stay simple and portable.

## Metrics stored (per day)

| Category | Metrics |
|---|---|
| **Strain / Cycle** | Strain score, avg HR, max HR, kilojoules, cycle start/end |
| **Recovery** | Recovery score, HRV (rMSSD ms), resting HR, SpO2 %, skin temp °C |
| **Sleep** | Sleep performance %, total in-bed time, light / REM / slow-wave / awake ms, disturbance count, latency, respiratory rate |
| **Workouts (daily totals)** | Count, total strain, total kJ |

A separate `workouts` table stores one row per individual workout with full
zone-duration breakdown, sport name, distance, etc.

---

## Setup

### 1 — Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) then:

```bash
uv sync
```

### 2 — Create a WHOOP API application

1. Go to <https://developer.whoop.com> → *Developer Dashboard*
2. Create a new application
3. Set the redirect URI to `http://localhost:8484/callback`
4. Copy the **Client ID** and **Client Secret** into `.env`:

```
WHOOP_CLIENT_ID=your_client_id
WHOOP_CLIENT_SECRET=your_client_secret
```

### 3 — Log in (run once)

```bash
uv run whoop-db login
```

This opens your browser for the WHOOP OAuth login, then saves the tokens
back into `.env` automatically.

---

## Usage

All commands: `uv run whoop-db <mode>` (or `uv run python whoop_db.py <mode>`).

### Initial full build

```bash
uv run whoop-db build
```

Pulls your complete WHOOP history (cycles, recoveries, sleeps, workouts) and
writes one row per calendar day into `whoop.db`.

### Incremental update (run on a schedule / cron)

```bash
uv run whoop-db update

# Re-verify 60 days instead of the default 30
uv run whoop-db update --verify-days 60
```

Fetches data from `verify_days` before the most-recent stored date through
today, overwriting any changed rows.

### Quick stats report

```bash
uv run whoop-db stats
```

Prints:

- Overall averages (strain, recovery, HRV, RHR, respiratory rate, SpO2, sleep performance)
- Last 14 days in a compact table
- Top 10 workout types by count

---

## GitHub / cron workflow

The database file `whoop.db` is checked into the repo so you always have a
portable snapshot.  A typical GitHub Actions cron job would:

1. `uv run whoop-db update`
2. `git add whoop.db && git commit -m "chore: update whoop data" && git push`

---

## Common options

| Flag | Default | Description |
|---|---|---|
| `--db PATH` | `$DB_PATH` from `.env` | Override the SQLite file location |
| `--env PATH` | `.env` | Use a different env file |
| `--verify-days N` | `30` | Days to re-verify in `update` mode |

---

## Development

```bash
uv run ruff check whoop_db.py
uv run pytest
```
