#!/usr/bin/env python3
"""
build_html.py  –  Generate index.html from whoop.db.

Usage:
    uv run python build_html.py           # reads whoop.db, writes index.html
    uv run python build_html.py --db PATH --out PATH
"""

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


def compute_sri(con: sqlite3.Connection) -> dict[str, float]:
    """
    Sleep Regularity Index per date: minute-by-minute sleep/wake concordance
    between this day and the next, scaled so 100 = identical schedule and
    -100 = perfectly inverted (0 ≈ random).  Uses every sleep episode (naps
    included), localized via each episode's own timezone offset.
    """
    eps = con.execute(
        "SELECT start, end, timezone_offset FROM sleeps "
        "WHERE start IS NOT NULL AND end IS NOT NULL"
    ).fetchall()

    minutes: dict[str, list[bool]] = {}   # local date → 1440 asleep flags
    for r in eps:
        off  = r["timezone_offset"] or "+00:00"
        sign = -1 if off.startswith("-") else 1
        delta = sign * timedelta(hours=int(off[1:3]), minutes=int(off[4:6]))
        s = datetime.fromisoformat(r["start"].replace("Z", "+00:00")) + delta
        e = datetime.fromisoformat(r["end"].replace("Z", "+00:00")) + delta
        t = s.replace(second=0, microsecond=0)
        while t < e:
            arr = minutes.setdefault(t.date().isoformat(), [False] * 1440)
            arr[t.hour * 60 + t.minute] = True
            t += timedelta(minutes=1)

    sri: dict[str, float] = {}
    for dkey, arr in minutes.items():
        nkey = (date.fromisoformat(dkey) + timedelta(days=1)).isoformat()
        nxt = minutes.get(nkey)
        if nxt is None:
            continue   # next day untracked → would fake-match on "awake"
        match = sum(a == b for a, b in zip(arr, nxt))
        sri[dkey] = round(200 * match / 1440 - 100, 1)
    return sri


def load_data(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    daily = con.execute("""
        SELECT
            date,
            -- strain < 1 only happens when the watch wasn't worn (or barely);
            -- a fully-worn sedentary day still scores ~4+.  Null those days so
            -- charts show gaps instead of fake dips.  Raw values stay in the DB.
            CASE WHEN strain >= 1 THEN strain END                    AS strain,
            CASE WHEN strain >= 1 AND avg_hr > 0 THEN avg_hr END     AS avg_hr,
            CASE WHEN strain >= 1 AND max_hr > 0 THEN max_hr END     AS max_hr,
            CASE WHEN strain >= 1 AND kilojoules > 0 THEN kilojoules END AS kilojoules,
            recovery_score,
            hrv_rmssd_ms,
            resting_hr,
            spo2_pct,
            skin_temp_celsius,
            sleep_performance_pct,
            ROUND(sleep_total_in_bed_ms / 60000.0, 2)  AS sleep_total_in_bed_min,
            ROUND(sleep_light_ms        / 60000.0, 2)  AS sleep_light_min,
            ROUND(sleep_rem_ms          / 60000.0, 2)  AS sleep_rem_min,
            ROUND(sleep_slow_wave_ms    / 60000.0, 2)  AS sleep_sws_min,
            ROUND(sleep_awake_ms        / 60000.0, 2)  AS sleep_awake_min,
            sleep_disturbances,
            ROUND(sleep_latency_ms      / 60000.0, 2)  AS sleep_latency_min,
            respiratory_rate,
            workout_count,
            workout_strain,
            workout_kilojoules
        FROM daily
        ORDER BY date ASC
    """).fetchall()

    workouts = con.execute("""
        SELECT
            id, date, sport_name, sport_id,
            start, end,
            strain, avg_hr, max_hr, kilojoules,
            distance_meter,
            zone_zero_ms, zone_one_ms, zone_two_ms,
            zone_three_ms, zone_four_ms, zone_five_ms
        FROM workouts
        ORDER BY start ASC
    """).fetchall()

    sri = compute_sri(con)
    consistency = dict(con.execute("""
        SELECT date, AVG(sleep_consistency_pct)
        FROM sleeps
        WHERE nap = 0 AND sleep_consistency_pct IS NOT NULL
        GROUP BY date
    """).fetchall())

    # Fill missing calendar days with all-null rows so charts render real
    # gaps (instead of splicing time out) and rolling windows stay true
    # calendar windows.
    rows = [dict(r) for r in daily]
    for r in rows:
        r["sri"] = sri.get(r["date"])
        r["sleep_consistency_pct"] = consistency.get(r["date"])
    if rows:
        by_date = {r["date"]: r for r in rows}
        nulls   = {k: None for k in rows[0] if k != "date"}
        d, last = date.fromisoformat(rows[0]["date"]), date.fromisoformat(rows[-1]["date"])
        filled  = []
        while d <= last:
            ds = d.isoformat()
            filled.append(by_date.get(ds) or {"date": ds, **nulls})
            d += timedelta(days=1)
        rows = filled

    return {
        "daily":    rows,
        "workouts": [dict(r) for r in workouts],
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-site="whoop">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="ar-repo" content="whoop-database">
<meta name="ar-generated" content="{generated}">
<title>Andy's WHOOP Dashboard</title>
<link rel="stylesheet" href="ar.css">
<style>
/* phase-trajectory ramp: old → new reads faint → prominent in either theme */
:root { --ramp-from: #c4b5fd; --ramp-to: #4c1d95; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) { --ramp-from: #4a3f86; --ramp-to: #d9d0ff; }
}
:root[data-theme="dark"] { --ramp-from: #4a3f86; --ramp-to: #d9d0ff; }
#ph-readout { font-size: 22px; font-weight: 800; fill: var(--muted); opacity: .45; }
.ph-lbl { font-weight: 600; fill: var(--muted); }
</style>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script src="ar.js"></script>
</head>
<body>

<!-- SIDEBAR -->
<nav class="sidebar">
  <div class="sidebar-logo">Andy's <span>WHOOP</span></div>

  <div class="nav-section">
    <div class="nav-section-title">Overview</div>
    <div class="nav-item active" data-section="dashboard">
      <span class="icon">⚡</span> Dashboard
    </div>
  </div>

  <div class="nav-section">
    <div class="nav-section-title">Health Markers</div>
    <div class="nav-item" data-section="recovery">
      <span class="icon">💚</span> Recovery
    </div>
    <div class="nav-item" data-section="hrv">
      <span class="icon">〰️</span> HRV
    </div>
    <div class="nav-item" data-section="rhr">
      <span class="icon">❤️</span> Resting HR
    </div>
    <div class="nav-item" data-section="strain">
      <span class="icon">🔥</span> Strain
    </div>
  </div>

  <div class="nav-section">
    <div class="nav-section-title">Sleep</div>
    <div class="nav-item" data-section="sleep">
      <span class="icon">🌙</span> Sleep
    </div>
    <div class="nav-item" data-section="respiratory">
      <span class="icon">💨</span> Respiratory
    </div>
  </div>

  <div class="nav-section">
    <div class="nav-section-title">Activity</div>
    <div class="nav-item" data-section="workouts">
      <span class="icon">🏃</span> Workouts
    </div>
    <div class="nav-item" data-section="log">
      <span class="icon">📋</span> Daily Log
    </div>
  </div>

  <div class="nav-section">
    <div class="nav-section-title">Long-Term</div>
    <div class="nav-item" data-section="longterm">
      <span class="icon">📈</span> All-Time Trends
    </div>
  </div>
</nav>

<!-- MAIN -->
<div class="main">
  <div class="topbar">
    <div>
      <h1 id="section-title">Dashboard</h1>
      <div class="subtitle" id="header-sub">Loading…</div>
    </div>
    <div class="range-btns" id="range-btns">
      <button class="range-btn" data-days="30">30d</button>
      <button class="range-btn" data-days="90">90d</button>
      <button class="range-btn" data-days="180">180d</button>
      <button class="range-btn" data-days="365">1yr</button>
      <button class="range-btn active" data-days="0">All</button>
    </div>
  </div>

  <div class="content">

    <!-- ── DASHBOARD ── -->
    <div class="section active" id="section-dashboard">
      <div class="kpi-strip" id="kpi-strip"></div>
      <div class="metric-grid" id="dashboard-charts"></div>
    </div>

    <!-- ── RECOVERY ── -->
    <div class="section" id="section-recovery">
      <div class="metric-grid" id="recovery-charts"></div>
    </div>

    <!-- ── HRV ── -->
    <div class="section" id="section-hrv">
      <div class="metric-grid" id="hrv-charts"></div>
    </div>

    <!-- ── RHR ── -->
    <div class="section" id="section-rhr">
      <div class="metric-grid" id="rhr-charts"></div>
    </div>

    <!-- ── STRAIN ── -->
    <div class="section" id="section-strain">
      <div class="metric-grid" id="strain-charts"></div>
    </div>

    <!-- ── SLEEP ── -->
    <div class="section" id="section-sleep">
      <div class="metric-grid" id="sleep-charts"></div>
    </div>

    <!-- ── RESPIRATORY ── -->
    <div class="section" id="section-respiratory">
      <div class="metric-grid" id="resp-charts"></div>
    </div>

    <!-- ── WORKOUTS ── -->
    <div class="section" id="section-workouts">
      <div class="metric-grid wide" style="margin-bottom:20px">
        <div class="metric-card">
          <div class="metric-card-header">
            <div class="metric-card-title">Workouts by Sport</div>
          </div>
          <div id="sport-bar-chart"></div>
        </div>
      </div>
      <div class="metric-card">
        <div class="metric-card-header">
          <div class="metric-card-title">All Workouts</div>
        </div>
        <div class="table-wrap" style="margin-top:12px">
          <table id="wkt-table">
            <thead><tr>
              <th data-col="date">Date</th>
              <th data-col="sport_name">Sport</th>
              <th data-col="strain" class="num">Strain</th>
              <th data-col="avg_hr" class="num">Avg HR</th>
              <th data-col="max_hr" class="num">Max HR</th>
              <th data-col="kilojoules" class="num">kJ</th>
              <th data-col="distance_meter" class="num">km</th>
              <th class="num">Z1</th><th class="num">Z2</th>
              <th class="num">Z3</th><th class="num">Z4</th><th class="num">Z5</th>
            </tr></thead>
            <tbody id="wkt-tbody"></tbody>
          </table>
        </div>
        <div class="pagination" id="wkt-pagination"></div>
      </div>
    </div>

    <!-- ── DAILY LOG ── -->
    <div class="section" id="section-log">
      <div class="metric-card">
        <div class="metric-card-header">
          <div class="metric-card-title">Daily Log</div>
        </div>
        <div class="table-wrap" style="margin-top:12px">
          <table id="daily-table">
            <thead><tr>
              <th data-col="date">Date</th>
              <th data-col="strain" class="num">Strain</th>
              <th data-col="recovery_score" class="num">Recovery</th>
              <th data-col="hrv_rmssd_ms" class="num">HRV ms</th>
              <th data-col="resting_hr" class="num">RHR</th>
              <th data-col="respiratory_rate" class="num">Resp</th>
              <th data-col="spo2_pct" class="num">SpO2</th>
              <th data-col="sleep_performance_pct" class="num">Sleep%</th>
              <th data-col="sleep_total_in_bed_min" class="num">In Bed</th>
              <th data-col="sleep_rem_min" class="num">REM</th>
              <th data-col="sleep_sws_min" class="num">SWS</th>
              <th data-col="workout_count" class="num">Wkts</th>
            </tr></thead>
            <tbody id="daily-tbody"></tbody>
          </table>
        </div>
        <div class="pagination" id="daily-pagination"></div>
      </div>
    </div>

    <!-- ── LONG-TERM ── -->
    <div class="section" id="section-longterm">
      <div class="note">
        These charts always show your <strong>complete WHOOP history</strong>,
        independent of the range filter above. Each point is the monthly average.
        Use the granularity toggle on each card to switch between monthly and quarterly.
      </div>
      <div class="metric-grid wide" style="margin-bottom:16px">
        <div class="metric-card">
          <div class="metric-card-header">
            <div>
              <div class="metric-card-title">Sleep Duration × Regularity — Monthly Trajectory</div>
              <div class="metric-card-sub">Each dot is one month (avg nightly sleep vs. SRI),
                connected in time order. Faint&nbsp;→&nbsp;bold&nbsp;=&nbsp;past&nbsp;→&nbsp;present.</div>
            </div>
            <div class="roll-btns"><button class="roll-btn" id="phase-replay">▶ Replay</button></div>
          </div>
          <div id="phase-chart" style="position:relative"></div>
        </div>
      </div>
      <div class="metric-grid" id="longterm-charts"></div>
    </div>

  </div><!-- /content -->
</div><!-- /main -->

<script>
// ── DATA ──────────────────────────────────────────────────────────────────
{data_js}

// ── STATE ─────────────────────────────────────────────────────────────────
let activeDays    = 0;   // 0 = all time
let activeSection = 'dashboard';
let filtered      = [...DAILY];
let filtWorkouts  = [...WORKOUTS];
let wktSortCol = 'date', wktSortDir = -1;
let dailySortCol = 'date', dailySortDir = -1;
let wktPage = 1, dailyPage = 1;
const PAGE = 60;

// rolling window per metric-card (keyed by card id)
const rollState = {};

// ── HELPERS ───────────────────────────────────────────────────────────────
const avg  = a => a.length ? a.reduce((s,v)=>s+v,0)/a.length : null;
const fmtN0 = v => v == null ? '—' : Math.round(v);
const fmtN1 = v => v == null ? '—' : Number(v).toFixed(1);
const fmtN2 = v => v == null ? '—' : Number(v).toFixed(2);
const fmtMin = m => {
  if (m == null) return '—';
  const h = Math.floor(m/60), mn = Math.round(m%60);
  return h ? `${h}h ${String(mn).padStart(2,'0')}m` : `${mn}m`;
};
const fmtKm = m => m == null ? '—' : (m/1000).toFixed(1);
const zFmt  = ms => ms == null ? '—' : Math.round(ms/60000)+'m';
const recClass = v => v >= 67 ? 'good' : v >= 34 ? 'warn' : 'bad';

function rolling(data, field, n) {
  return data.map((_, i) => {
    const sl = data.slice(Math.max(0,i-n+1),i+1).map(d=>d[field]).filter(v=>v!=null);
    return sl.length ? sl.reduce((s,v)=>s+v,0)/sl.length : null;
  });
}

// ── FILTER ────────────────────────────────────────────────────────────────
function applyRange(days) {
  activeDays = days;
  document.querySelectorAll('.range-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.days) === days);
  });
  if (days === 0) {
    filtered     = [...DAILY];
    filtWorkouts = [...WORKOUTS];
  } else {
    const cutoff = DAILY.length
      ? (() => { const d=new Date(); d.setDate(d.getDate()-days); return d.toISOString().slice(0,10); })()
      : '';
    filtered     = DAILY.filter(d => d.date >= cutoff);
    filtWorkouts = WORKOUTS.filter(w => (w.start||'').slice(0,10) >= cutoff);
  }
  wktPage = dailyPage = 1;
  render();
}

document.querySelectorAll('.range-btn').forEach(b =>
  b.addEventListener('click', () => applyRange(parseInt(b.dataset.days)))
);

// ── NAV ───────────────────────────────────────────────────────────────────
const SECTION_TITLES = {
  dashboard:'Dashboard', recovery:'Recovery', hrv:'HRV',
  rhr:'Resting Heart Rate', strain:'Strain', sleep:'Sleep',
  respiratory:'Respiratory Rate', workouts:'Workouts', log:'Daily Log',
  longterm:'All-Time Trends',
};
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    activeSection = item.dataset.section;
    document.querySelectorAll('.nav-item').forEach(i=>i.classList.remove('active'));
    item.classList.add('active');
    document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
    document.getElementById('section-'+activeSection).classList.add('active');
    document.getElementById('section-title').textContent = SECTION_TITLES[activeSection]||activeSection;
    render();
  });
});

// ── CHARTS ────────────────────────────────────────────────────────────────
// Cards are built as HTML strings; their charts are queued here and drawn
// (with AR.lineChart, d3) once the HTML is in the document.
const drawQ = [];
const flushCharts = () => drawQ.splice(0).forEach(f => f());

// ── METRIC CARD BUILDER ───────────────────────────────────────────────────
// opts: { id, title, field, color, unit, decimals, rollWindows, yFmt, h }
const cardOpts = {};   // "<section>/<id>" → opts, for re-drawing on roll-window clicks

function drawCard(el) {
  const o = cardOpts[el.dataset.key];
  const n = rollState[o.id];
  AR.lineChart(el, {
    x: filtered.map(d => d.date),
    y: n === 1 ? filtered.map(d => d[o.field]) : rolling(filtered, o.field, n),
    color: o.color, height: o.h || 130,
    yFmt: o.yFmt || (v => v.toFixed(o.decimals ?? 1)),
  });
}

function buildMetricCard(data, opts) {
  const { id, title, field, color, unit='', decimals=1,
          rollWindows=[7,14,30,90] } = opts;

  if (!rollState[id]) rollState[id] = rollWindows[1] || rollWindows[0];
  if (!rollWindows.includes(rollState[id])) rollState[id] = rollWindows[rollWindows.length-1];

  const valid = data.map(d => d[field]).filter(v=>v!=null);
  const cur   = valid.length ? valid[valid.length-1] : null;
  const mn    = valid.length ? Math.min(...valid) : null;
  const mx    = valid.length ? Math.max(...valid) : null;
  const av    = avg(valid);

  const curStr = cur!=null ? cur.toFixed(decimals)+unit : '—';
  const subStr = av!=null
    ? `avg ${av.toFixed(decimals)}${unit} · min ${mn.toFixed(decimals)}${unit} · max ${mx.toFixed(decimals)}${unit}`
    : 'No data';

  const rollBtns = rollWindows.map(w =>
    `<button class="roll-btn${rollState[id]===w?' active':''}"
             data-card="${id}" data-win="${w}">${w}d</button>`
  ).join('');

  const key = activeSection + '/' + id;
  cardOpts[key] = opts;
  drawQ.push(() => drawCard(document.querySelector(`.chart-area[data-key="${key}"]`)));

  return `<div class="metric-card">
    <div class="metric-card-header">
      <div>
        <div class="metric-card-title">${title}</div>
        <div class="metric-card-val" style="color:${color}">${curStr}</div>
        <div class="metric-card-sub">${subStr}</div>
      </div>
      <div class="roll-btns">${rollBtns}</div>
    </div>
    <div class="chart-area" data-key="${key}"></div>
  </div>`;
}

// Rolling window button clicks (event delegation): redraw just that card's chart
document.addEventListener('click', e => {
  const b = e.target.closest('.roll-btn[data-win]');
  if (!b) return;
  const win = parseInt(b.dataset.win), card = b.closest('.metric-card');
  rollState[b.dataset.card] = win;
  card.querySelectorAll('.roll-btn').forEach(rb =>
    rb.classList.toggle('active', parseInt(rb.dataset.win) === win));
  drawCard(card.querySelector('.chart-area'));
});

// ── KPI STRIP ─────────────────────────────────────────────────────────────
function renderKPI() {
  const rec  = filtered.map(d=>d.recovery_score).filter(v=>v!=null);
  const hrv  = filtered.map(d=>d.hrv_rmssd_ms).filter(v=>v!=null);
  const rhr  = filtered.map(d=>d.resting_hr).filter(v=>v!=null);
  const str  = filtered.map(d=>d.strain).filter(v=>v!=null);
  const sp   = filtered.map(d=>d.sleep_performance_pct).filter(v=>v!=null);
  const resp = filtered.map(d=>d.respiratory_rate).filter(v=>v!=null);
  const spo2 = filtered.map(d=>d.spo2_pct).filter(v=>v!=null);

  const tracked = filtered.filter(d =>
    d.strain != null || d.recovery_score != null || d.sleep_performance_pct != null).length;
  const dates = filtered.map(d=>d.date).filter(Boolean).sort();
  document.getElementById('header-sub').textContent =
    dates.length ? `${dates[0]} → ${dates[dates.length-1]} · ${tracked} tracked days` : 'No data';

  const defs = [
    ['Recovery',    rec.length  ? avg(rec).toFixed(0)+'%' : '—',  'green',  avg(rec)?.toFixed(0)+'% avg'],
    ['HRV rMSSD',   hrv.length  ? avg(hrv).toFixed(1)+' ms' : '—','accent',       ''],
    ['Resting HR',  rhr.length  ? avg(rhr).toFixed(0)+' bpm' : '—','blue',      ''],
    ['Day Strain',  str.length  ? avg(str).toFixed(1) : '—',       'yellow',   ''],
    ['Sleep Perf',  sp.length   ? avg(sp).toFixed(0)+'%' : '—',   'purple',    ''],
    ['Resp Rate',   resp.length ? avg(resp).toFixed(2) : '—',      'teal',     'br/min'],
    ['SpO2',        spo2.length ? avg(spo2).toFixed(1)+'%' : '—', 'teal',     ''],
    ['Days',        tracked, 'border', filtWorkouts.length+' workouts'],
  ];
  document.getElementById('kpi-strip').innerHTML = defs.map(([l,v,cls,sub]) =>
    `<div class="kpi-card" style="--kpi-color:var(--${cls})">
       <div class="kpi-label">${l}</div>
       <div class="kpi-value">${v}</div>
       ${sub?`<div class="kpi-sub">${sub}</div>`:''}
     </div>`
  ).join('');
}

// ── DASHBOARD CHARTS ──────────────────────────────────────────────────────
function renderDashboard() {
  renderKPI();
  document.getElementById('dashboard-charts').innerHTML = [
    buildMetricCard(filtered, {id:'recovery', title:'Recovery Score',
      field:'recovery_score', color:'var(--green)', unit:'%', decimals:0,
      rollWindows:[1,7,14,30]}),
    buildMetricCard(filtered, {id:'hrv', title:'HRV — rMSSD',
      field:'hrv_rmssd_ms', color:'var(--accent)', unit:' ms', decimals:1,
      rollWindows:[1,7,14,30]}),
    buildMetricCard(filtered, {id:'rhr', title:'Resting Heart Rate',
      field:'resting_hr', color:'var(--blue)', unit:' bpm', decimals:0,
      rollWindows:[1,7,14,30]}),
    buildMetricCard(filtered, {id:'strain', title:'Day Strain',
      field:'strain', color:'var(--yellow)', unit:'', decimals:1,
      rollWindows:[1,7,28]}),
    buildMetricCard(filtered, {id:'sleep', title:'Sleep Performance',
      field:'sleep_performance_pct', color:'var(--purple)', unit:'%', decimals:0,
      rollWindows:[1,7,14,30]}),
    buildMetricCard(filtered, {id:'resp', title:'Respiratory Rate',
      field:'respiratory_rate', color:'var(--teal)', unit:' br/min', decimals:2,
      rollWindows:[1,7,14,30]}),
  ].join('');
}

// ── SECTION CHARTS ────────────────────────────────────────────────────────
function renderRecovery() {
  document.getElementById('recovery-charts').innerHTML = [
    buildMetricCard(filtered, {id:'recovery', title:'Recovery Score',
      field:'recovery_score', color:'var(--green)', unit:'%', decimals:0,
      rollWindows:[1,7,14,30,90], h:160}),
    buildMetricCard(filtered, {id:'hrv', title:'HRV — rMSSD',
      field:'hrv_rmssd_ms', color:'var(--accent)', unit:' ms', decimals:1,
      rollWindows:[1,7,14,30,90], h:160}),
    buildMetricCard(filtered, {id:'rhr', title:'Resting Heart Rate',
      field:'resting_hr', color:'var(--blue)', unit:' bpm', decimals:0,
      rollWindows:[1,7,14,30,90], h:160}),
    buildMetricCard(filtered, {id:'spo2', title:'Blood Oxygen (SpO2)',
      field:'spo2_pct', color:'var(--teal)', unit:'%', decimals:1,
      rollWindows:[1,7,14,30], h:160}),
  ].join('');
}

function renderHRV() {
  document.getElementById('hrv-charts').innerHTML = [
    buildMetricCard(filtered, {id:'hrv', title:'HRV — rMSSD',
      field:'hrv_rmssd_ms', color:'var(--accent)', unit:' ms', decimals:1,
      rollWindows:[1,7,14,30,90,180], h:180}),
    buildMetricCard(filtered, {id:'recovery', title:'Recovery Score',
      field:'recovery_score', color:'var(--green)', unit:'%', decimals:0,
      rollWindows:[1,7,14,30,90], h:180}),
  ].join('');
}

function renderRHR() {
  document.getElementById('rhr-charts').innerHTML = [
    buildMetricCard(filtered, {id:'rhr', title:'Resting Heart Rate',
      field:'resting_hr', color:'var(--blue)', unit:' bpm', decimals:0,
      rollWindows:[1,7,14,30,90,180], h:180}),
    buildMetricCard(filtered, {id:'spo2', title:'SpO2',
      field:'spo2_pct', color:'var(--teal)', unit:'%', decimals:1,
      rollWindows:[1,7,14,30], h:180}),
  ].join('');
}

function renderStrain() {
  document.getElementById('strain-charts').innerHTML = [
    buildMetricCard(filtered, {id:'strain', title:'Day Strain',
      field:'strain', color:'var(--yellow)', unit:'', decimals:1,
      rollWindows:[1,7,28,90], h:180}),
    buildMetricCard(filtered, {id:'skin', title:'Skin Temperature',
      field:'skin_temp_celsius', color:'var(--accent)', unit:' °C', decimals:1,
      rollWindows:[1,7,14,30], h:180}),
  ].join('');
}

function renderSleep() {
  document.getElementById('sleep-charts').innerHTML = [
    buildMetricCard(filtered, {id:'sleep', title:'Sleep Performance',
      field:'sleep_performance_pct', color:'var(--purple)', unit:'%', decimals:0,
      rollWindows:[1,7,14,30,90], h:150}),
    buildMetricCard(filtered, {id:'sri', title:'Sleep Regularity Index (SRI)',
      field:'sri', color:'var(--accent)', unit:'', decimals:0,
      rollWindows:[1,4,7,14,30,90], h:150}),
    buildMetricCard(filtered, {id:'consist', title:'Sleep Consistency (WHOOP)',
      field:'sleep_consistency_pct', color:'var(--green)', unit:'%', decimals:0,
      rollWindows:[1,7,14,30,90], h:150}),
    buildMetricCard(filtered, {id:'tib', title:'Time in Bed',
      field:'sleep_total_in_bed_min', color:'var(--blue)', unit:' min', decimals:0,
      rollWindows:[1,7,14,30], h:150,
      yFmt: v => fmtMin(v)}),
    buildMetricCard(filtered, {id:'rem', title:'REM Sleep',
      field:'sleep_rem_min', color:'var(--purple)', unit:' min', decimals:0,
      rollWindows:[1,7,14,30], h:150}),
    buildMetricCard(filtered, {id:'sws', title:'Slow-Wave Sleep (SWS)',
      field:'sleep_sws_min', color:'var(--teal)', unit:' min', decimals:0,
      rollWindows:[1,7,14,30], h:150}),
    buildMetricCard(filtered, {id:'light', title:'Light Sleep',
      field:'sleep_light_min', color:'var(--blue)', unit:' min', decimals:0,
      rollWindows:[1,7,14,30], h:150}),
    buildMetricCard(filtered, {id:'latency', title:'Sleep Latency',
      field:'sleep_latency_min', color:'var(--yellow)', unit:' min', decimals:1,
      rollWindows:[1,7,14,30], h:150}),
    buildMetricCard(filtered, {id:'dist', title:'Disturbances',
      field:'sleep_disturbances', color:'var(--red)', unit:'', decimals:0,
      rollWindows:[1,7,14,30], h:150}),
  ].join('');
}

function renderResp() {
  document.getElementById('resp-charts').innerHTML = [
    buildMetricCard(filtered, {id:'resp', title:'Respiratory Rate',
      field:'respiratory_rate', color:'var(--teal)', unit:' br/min', decimals:2,
      rollWindows:[1,7,14,30,90,180], h:180}),
    buildMetricCard(filtered, {id:'spo2', title:'SpO2',
      field:'spo2_pct', color:'var(--blue)', unit:'%', decimals:1,
      rollWindows:[1,7,14,30], h:180}),
  ].join('');
}

// ── WORKOUTS ──────────────────────────────────────────────────────────────
function renderWorkouts() {
  // Sport bar chart
  const counts = {};
  filtWorkouts.forEach(w => {
    const s = w.sport_name||'unknown';
    counts[s] = (counts[s]||0)+1;
  });
  const sorted = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,15);
  AR.hbarChart('#sport-bar-chart', {
    labels: sorted.map(e => e[0]), values: sorted.map(e => e[1]),
    tip: i => `<b>${sorted[i][0]}</b><br>${sorted[i][1]} workouts`,
  });

  // Table
  const mul = wktSortDir;
  const sw = [...filtWorkouts].sort((a,b)=>{
    const av=a[wktSortCol], bv=b[wktSortCol];
    if(av==null&&bv==null) return 0;
    if(av==null) return 1; if(bv==null) return -1;
    return av<bv?-mul:av>bv?mul:0;
  });
  const total=sw.length, pages=Math.max(1,Math.ceil(total/PAGE));
  wktPage=Math.min(wktPage,pages);
  const slice=sw.slice((wktPage-1)*PAGE,wktPage*PAGE);

  document.querySelectorAll('#wkt-table thead th[data-col]').forEach(th=>{
    th.classList.remove('sorted-asc','sorted-desc');
    if(th.dataset.col===wktSortCol) th.classList.add(wktSortDir===1?'sorted-asc':'sorted-desc');
  });
  document.getElementById('wkt-tbody').innerHTML = slice.map(w=>`<tr>
    <td>${(w.start||'').slice(0,10)}</td>
    <td><span class="badge">${w.sport_name||'—'}</span></td>
    <td class="num">${fmtN1(w.strain)}</td>
    <td class="num">${fmtN0(w.avg_hr)}</td>
    <td class="num">${fmtN0(w.max_hr)}</td>
    <td class="num">${fmtN0(w.kilojoules)}</td>
    <td class="num">${fmtKm(w.distance_meter)}</td>
    <td class="num">${zFmt(w.zone_one_ms)}</td>
    <td class="num">${zFmt(w.zone_two_ms)}</td>
    <td class="num">${zFmt(w.zone_three_ms)}</td>
    <td class="num">${zFmt(w.zone_four_ms)}</td>
    <td class="num">${zFmt(w.zone_five_ms)}</td>
  </tr>`).join('');
  renderPagination('wkt-pagination', wktPage, pages, total, p=>{wktPage=p;renderWorkouts();});
}

// ── DAILY LOG ─────────────────────────────────────────────────────────────
function renderDailyLog() {
  const mul = dailySortDir;
  const sd = [...filtered].sort((a,b)=>{
    const av=a[dailySortCol], bv=b[dailySortCol];
    if(av==null&&bv==null) return 0;
    if(av==null) return 1; if(bv==null) return -1;
    return av<bv?-mul:av>bv?mul:0;
  });
  const total=sd.length, pages=Math.max(1,Math.ceil(total/PAGE));
  dailyPage=Math.min(dailyPage,pages);
  const slice=sd.slice((dailyPage-1)*PAGE,dailyPage*PAGE);

  document.querySelectorAll('#daily-table thead th[data-col]').forEach(th=>{
    th.classList.remove('sorted-asc','sorted-desc');
    if(th.dataset.col===dailySortCol) th.classList.add(dailySortDir===1?'sorted-asc':'sorted-desc');
  });
  document.getElementById('daily-tbody').innerHTML = slice.map(d=>`<tr>
    <td>${d.date}</td>
    <td class="num">${fmtN1(d.strain)}</td>
    <td class="num">${d.recovery_score!=null
      ?`<span class="badge ${recClass(d.recovery_score)}">${Math.round(d.recovery_score)}%</span>`
      :'—'}</td>
    <td class="num">${fmtN1(d.hrv_rmssd_ms)}</td>
    <td class="num">${fmtN0(d.resting_hr)}</td>
    <td class="num">${d.respiratory_rate!=null?d.respiratory_rate.toFixed(2):'—'}</td>
    <td class="num">${d.spo2_pct!=null?d.spo2_pct.toFixed(1)+'%':'—'}</td>
    <td class="num">${d.sleep_performance_pct!=null?Math.round(d.sleep_performance_pct)+'%':'—'}</td>
    <td class="num">${fmtMin(d.sleep_total_in_bed_min)}</td>
    <td class="num">${fmtMin(d.sleep_rem_min)}</td>
    <td class="num">${fmtMin(d.sleep_sws_min)}</td>
    <td class="num">${d.workout_count||'—'}</td>
  </tr>`).join('');
  renderPagination('daily-pagination', dailyPage, pages, total, p=>{dailyPage=p;renderDailyLog();});
}

// ── PAGINATION ────────────────────────────────────────────────────────────
function renderPagination(id, page, pages, total, cb) {
  const el = document.getElementById(id);
  if(pages<=1){el.innerHTML='';return;}
  const btns=[`<span class="info">Page ${page}/${pages} (${total.toLocaleString()} rows)</span>`];
  if(page>1) btns.push(`<button onclick="(${cb})(${page-1})">‹</button>`);
  const lo=Math.max(1,page-2), hi=Math.min(pages,page+2);
  for(let p=lo;p<=hi;p++)
    btns.push(`<button class="${p===page?'active':''}" onclick="(${cb})(${p})">${p}</button>`);
  if(page<pages) btns.push(`<button onclick="(${cb})(${page+1})">›</button>`);
  el.innerHTML=btns.join('');
}

// ── SORT HEADERS ──────────────────────────────────────────────────────────
document.querySelectorAll('#wkt-table thead th[data-col]').forEach(th=>{
  th.addEventListener('click',()=>{
    if(wktSortCol===th.dataset.col) wktSortDir*=-1;
    else{wktSortCol=th.dataset.col;wktSortDir=-1;}
    wktPage=1; renderWorkouts();
  });
});
document.querySelectorAll('#daily-table thead th[data-col]').forEach(th=>{
  th.addEventListener('click',()=>{
    if(dailySortCol===th.dataset.col) dailySortDir*=-1;
    else{dailySortCol=th.dataset.col;dailySortDir=-1;}
    dailyPage=1; renderDailyLog();
  });
});

// ── LONG-TERM ─────────────────────────────────────────────────────────────
// granularity state per long-term card: 'month' | 'quarter'
const ltGranState = {};

// Aggregate DAILY (full history, ignores filter) into monthly or quarterly buckets.
// Returns [{label, value: avg, n}, ...]
function aggregateLT(field, gran) {
  const buckets = {};
  DAILY.forEach(d => {
    if (d[field] == null) return;
    const key = gran === 'quarter'
      ? `${d.date.slice(0,4)}-Q${Math.ceil(parseInt(d.date.slice(5,7))/3)}`
      : d.date.slice(0,7);   // YYYY-MM
    if (!buckets[key]) buckets[key] = [];
    buckets[key].push(d[field]);
  });
  return Object.keys(buckets).sort().map(k => ({
    label: k,
    value: buckets[k].reduce((s,v)=>s+v,0) / buckets[k].length,
    n:     buckets[k].length,
  }));
}

function drawLT(m) {
  const pts = aggregateLT(m.field, ltGranState[m.id]);
  AR.lineChart('#ltchart-'+m.id, {
    x: pts.map(p => p.label), y: pts.map(p => p.value),
    color: m.color, height: 160, yFmt: v => v.toFixed(m.decimals),
    tip: i => `<b>${AR.fmtDate(pts[i].label)}</b><br>${pts[i].value.toFixed(m.decimals)}${m.unit}` +
              ` <span class="dim">· ${pts[i].n} days</span>`,
  });
}

function buildLTCard(m) {
  const { id, title, field, color, unit='', decimals=1 } = m;
  if (!ltGranState[id]) ltGranState[id] = 'month';
  const gran = ltGranState[id];

  const values = aggregateLT(field, gran).map(p => p.value);
  const cur = values.length ? values[values.length-1] : null;
  const mn  = values.length ? Math.min(...values) : null;
  const mx  = values.length ? Math.max(...values) : null;
  const av  = avg(values);

  const curStr = cur!=null ? cur.toFixed(decimals)+unit : '—';
  const subStr = av!=null
    ? `avg ${av.toFixed(decimals)}${unit} · min ${mn.toFixed(decimals)}${unit} · max ${mx.toFixed(decimals)}${unit} · ${values.length} ${gran==='quarter'?'quarters':'months'}`
    : 'No data';

  const granBtns = ['month','quarter'].map(g =>
    `<button class="roll-btn lt-gran-btn${gran===g?' active':''}"
             data-ltcard="${id}" data-gran="${g}">${g==='month'?'Monthly':'Quarterly'}</button>`
  ).join('');

  drawQ.push(() => drawLT(m));

  return `<div class="metric-card">
    <div class="metric-card-header">
      <div>
        <div class="metric-card-title">${title}</div>
        <div class="metric-card-val" style="color:${color}">${curStr}</div>
        <div class="metric-card-sub">${subStr}</div>
      </div>
      <div class="roll-btns">${granBtns}</div>
    </div>
    <div class="chart-area" id="ltchart-${id}"></div>
  </div>`;
}

// granularity button clicks (event delegation)
document.addEventListener('click', e => {
  const b = e.target.closest('.lt-gran-btn');
  if (!b) return;
  const m = LT_METRICS.find(x => x.id === b.dataset.ltcard);
  ltGranState[m.id] = b.dataset.gran;
  b.closest('.metric-card').querySelectorAll('.lt-gran-btn').forEach(rb =>
    rb.classList.toggle('active', rb.dataset.gran === b.dataset.gran)
  );
  drawLT(m);
});

const LT_METRICS = [
  {id:'recovery', title:'Recovery Score',       field:'recovery_score',         color:'var(--green)',  unit:'%',       decimals:0},
  {id:'hrv',      title:'HRV — rMSSD',          field:'hrv_rmssd_ms',           color:'var(--accent)', unit:' ms',     decimals:1},
  {id:'rhr',      title:'Resting Heart Rate',   field:'resting_hr',             color:'var(--blue)',   unit:' bpm',    decimals:0},
  {id:'strain',   title:'Day Strain',            field:'strain',                 color:'var(--yellow)', unit:'',        decimals:1},
  {id:'sleep',    title:'Sleep Performance',     field:'sleep_performance_pct',  color:'var(--purple)', unit:'%',       decimals:0},
  {id:'resp',     title:'Respiratory Rate',      field:'respiratory_rate',       color:'var(--teal)',   unit:' br/min', decimals:2},
  {id:'spo2',     title:'SpO2',                  field:'spo2_pct',               color:'var(--teal)',   unit:'%',       decimals:1},
  {id:'tib',      title:'Time in Bed',           field:'sleep_total_in_bed_min', color:'var(--blue)',   unit:' min',    decimals:0},
  {id:'rem',      title:'REM Sleep',             field:'sleep_rem_min',          color:'var(--purple)', unit:' min',    decimals:0},
  {id:'sws',      title:'Slow-Wave Sleep',       field:'sleep_sws_min',          color:'var(--teal)',   unit:' min',    decimals:0},
  {id:'dist',     title:'Sleep Disturbances',    field:'sleep_disturbances',     color:'var(--red)',    unit:'',        decimals:0},
  {id:'sri',      title:'Sleep Regularity (SRI)',field:'sri',                    color:'var(--accent)', unit:'',        decimals:1},
  {id:'consist',  title:'Sleep Consistency',     field:'sleep_consistency_pct',  color:'var(--green)',  unit:'%',       decimals:0},
];

function renderLongTerm() {
  document.getElementById('longterm-charts').innerHTML =
    LT_METRICS.map(m => buildLTCard(m)).join('');
  renderPhase(true);
}

// ── PHASE TRAJECTORY — sleep duration × SRI ───────────────────────────────
let phaseRAF = null;

function phasePoints() {
  const buckets = {};
  DAILY.forEach(d => {
    const asleep = (d.sleep_light_min!=null || d.sleep_rem_min!=null || d.sleep_sws_min!=null)
      ? ((d.sleep_light_min||0) + (d.sleep_rem_min||0) + (d.sleep_sws_min||0)) / 60
      : null;
    const key = d.date.slice(0,7);
    if (!buckets[key]) buckets[key] = {h:[], s:[]};
    if (asleep != null) buckets[key].h.push(asleep);
    if (d.sri   != null) buckets[key].s.push(d.sri);
  });
  return Object.keys(buckets).sort()
    .map(k => ({label:k, h:avg(buckets[k].h), s:avg(buckets[k].s), n:buckets[k].h.length}))
    .filter(p => p.h != null && p.s != null && p.n >= 5);   // skip mostly-untracked months
}

const PH_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const phFmt = k => PH_MONTHS[+k.slice(5,7)-1] + ' ’' + k.slice(2,4);

function renderPhase(animate) {
  const el = document.getElementById('phase-chart');
  if (!el) return;
  if (phaseRAF) { cancelAnimationFrame(phaseRAF); phaseRAF = null; }

  const pts = phasePoints();
  if (pts.length < 2) { el.innerHTML = '<div class="empty">Not enough data</div>'; return; }
  const N = pts.length;

  // sequential one-hue ramp, oldest → newest (endpoints are theme tokens)
  const css  = getComputedStyle(document.documentElement);
  const ramp = d3.interpolateRgb(css.getPropertyValue('--ramp-from').trim(),
                                 css.getPropertyValue('--ramp-to').trim());

  const W=860, H=420, m={t:26,r:26,b:46,l:52};
  const pad = (a, min) => { const [lo,hi]=d3.extent(a), p=(hi-lo)*0.08||min; return [lo-p, hi+p]; };
  const x = d3.scaleLinear().domain(pad(pts.map(p=>p.h), 0.5)).range([m.l, W-m.r]);
  const y = d3.scaleLinear().domain(pad(pts.map(p=>p.s), 2)).range([H-m.b, m.t]);

  el.innerHTML = '';
  const svg = d3.select(el).append('svg').attr('class','ar-chart').attr('viewBox',`0 0 ${W} ${H}`);
  svg.append('g').attr('class','grid').attr('transform',`translate(0,${H-m.b})`)
    .call(d3.axisBottom(x).tickValues(d3.range(Math.ceil(x.domain()[0]*2)/2, x.domain()[1], 0.5))
      .tickSize(-(H-m.t-m.b)).tickFormat(v => v+'h').tickPadding(8));
  svg.append('g').attr('class','grid').attr('transform',`translate(${m.l},0)`)
    .call(d3.axisLeft(y).ticks(5).tickSize(-(W-m.l-m.r)).tickPadding(6));
  svg.append('text').attr('class','axis-title').attr('x',(m.l+W-m.r)/2).attr('y',H-6)
    .attr('text-anchor','middle').text('Average nightly sleep (hours)');
  svg.append('text').attr('class','axis-title').attr('x',m.l).attr('y',m.t-10)
    .text('Sleep Regularity Index');

  // every element carries the index at which it appears in the draw-on
  svg.append('g').selectAll('line').data(d3.range(1,N)).join('line').attr('class','ph-el')
    .attr('x1',i=>x(pts[i-1].h)).attr('y1',i=>y(pts[i-1].s))
    .attr('x2',i=>x(pts[i].h)).attr('y2',i=>y(pts[i].s))
    .attr('stroke',i=>ramp(i/(N-1))).attr('stroke-width',2).attr('stroke-linecap','round');
  const big = i => pts[i].label.slice(5)==='01' || i===0 || i===N-1;
  svg.append('g').selectAll('circle').data(d3.range(N)).join('circle')
    .attr('class',i=>'ph-el'+(big(i)?' ring':''))
    .attr('cx',i=>x(pts[i].h)).attr('cy',i=>y(pts[i].s))
    .attr('r',i=>big(i)?4.5:2.5).attr('fill',i=>ramp(i/(N-1)));
  svg.append('g').selectAll('text').data(d3.range(N).filter(big)).join('text').attr('class','ph-el ph-lbl')
    .attr('x',i=>x(pts[i].h)).attr('y',i=>y(pts[i].s)-9).attr('text-anchor','middle')
    .text(i => pts[i].label.slice(5)==='01' ? pts[i].label.slice(0,4) : phFmt(pts[i].label));
  const readout = svg.append('text').attr('id','ph-readout').attr('x',W-m.r).attr('y',m.t+4)
    .attr('text-anchor','end');

  // animation — draw on in chronological order
  const els = svg.selectAll('.ph-el');
  const show = k => els.attr('opacity', function(i) { return i <= k ? (this.tagName==='line' ? 0.8 : 1) : 0; });
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!animate || reduced) {
    show(N);
  } else {
    const DUR = 6000; let t0 = null;
    const tick = ts => {
      if (t0 === null) t0 = ts;
      const k = Math.min(1, (ts-t0)/DUR) * (N-1);
      show(k);
      readout.text(phFmt(pts[Math.min(N-1, Math.floor(k))].label));
      if (k < N-1) phaseRAF = requestAnimationFrame(tick);
      else { readout.text(''); phaseRAF = null; }
    };
    show(-1);
    phaseRAF = requestAnimationFrame(tick);
  }

  // hover — nearest month within 24px
  const focus = svg.append('circle').attr('r',7).attr('fill','none').attr('stroke','var(--text)')
    .attr('stroke-width',1.5).style('display','none').style('pointer-events','none');
  svg.on('mousemove', ev => {
    const [mx,my] = d3.pointer(ev);
    let best=null, bd=24;
    pts.forEach(p => {
      const d=Math.hypot(x(p.h)-mx, y(p.s)-my);
      if (d<bd) { bd=d; best=p; }
    });
    if (!best) { focus.style('display','none'); AR.tip.hide(); return; }
    focus.style('display',null).attr('cx',x(best.h)).attr('cy',y(best.s));
    AR.tip.show(`<b>${phFmt(best.label)}</b> · ${best.h.toFixed(2)}h · SRI ${best.s.toFixed(1)}`, ev);
  });
  svg.on('mouseleave', () => { focus.style('display','none'); AR.tip.hide(); });
}

document.addEventListener('click', e => {
  if (e.target.closest('#phase-replay')) renderPhase(true);
});
// the ramp endpoints are theme tokens resolved at draw time → redraw on toggle
document.addEventListener('ar:theme', () => {
  if (activeSection === 'longterm') renderPhase(false);
});

// ── RENDER DISPATCHER ─────────────────────────────────────────────────────
function render() {
  switch(activeSection) {
    case 'dashboard':   renderDashboard(); break;
    case 'recovery':    renderKPI(); renderRecovery(); break;
    case 'hrv':         renderKPI(); renderHRV(); break;
    case 'rhr':         renderKPI(); renderRHR(); break;
    case 'strain':      renderKPI(); renderStrain(); break;
    case 'sleep':       renderKPI(); renderSleep(); break;
    case 'respiratory': renderKPI(); renderResp(); break;
    case 'workouts':    renderKPI(); renderWorkouts(); break;
    case 'log':         renderKPI(); renderDailyLog(); break;
    case 'longterm':    renderLongTerm(); break;
  }
  flushCharts();
}

render();
</script>
</body>
</html>
"""


def build(db_path: str, out_path: str) -> None:
    print(f"Loading data from {db_path} …")
    data = load_data(db_path)

    data_js = (
        f"const DAILY    = {json.dumps(data['daily'],    separators=(',', ':'))};\n"
        f"const WORKOUTS = {json.dumps(data['workouts'], separators=(',', ':'))};\n"
    )

    html = (HTML_TEMPLATE.replace("{data_js}", data_js)
            .replace("{generated}", date.today().isoformat()))
    Path(out_path).write_text(html, encoding="utf-8")
    size = Path(out_path).stat().st_size
    print(f"Written {out_path}  ({size/1024/1024:.2f} MB, "
          f"{len(data['daily'])} days, {len(data['workouts'])} workouts)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate index.html from whoop.db")
    ap.add_argument("--db",  default="whoop.db",   help="SQLite DB path")
    ap.add_argument("--out", default="index.html", help="Output HTML path")
    args = ap.parse_args()
    build(args.db, args.out)


if __name__ == "__main__":
    main()
