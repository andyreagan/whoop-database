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
from pathlib import Path


def load_data(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    daily = con.execute("""
        SELECT
            date,
            strain,
            avg_hr,
            max_hr,
            kilojoules,
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

    return {
        "daily":    [dict(r) for r in daily],
        "workouts": [dict(r) for r in workouts],
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Andy's WHOOP Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --sidebar-w: 220px;
  --bg:        #f4f5f7;
  --surface:   #ffffff;
  --sidebar-bg:#111827;
  --sidebar-text:#9ca3af;
  --sidebar-active:#ffffff;
  --sidebar-hover:#1f2937;
  --border:    #e5e7eb;
  --text:      #111827;
  --muted:     #6b7280;
  --accent:    #ec4899;   /* fitIQ-ish pink */
  --teal:      #06b6d4;
  --green:     #10b981;
  --yellow:    #f59e0b;
  --red:       #ef4444;
  --purple:    #8b5cf6;
  --blue:      #3b82f6;
  --radius:    10px;
  --shadow:    0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
  --font:      'Inter', system-ui, -apple-system, sans-serif;
}
body { font-family: var(--font); background: var(--bg); color: var(--text);
       font-size: 14px; line-height: 1.5; display: flex; min-height: 100vh; }

/* ── SIDEBAR ── */
.sidebar {
  width: var(--sidebar-w); background: var(--sidebar-bg); color: var(--sidebar-text);
  display: flex; flex-direction: column; position: fixed; top: 0; left: 0;
  height: 100vh; overflow-y: auto; z-index: 100; flex-shrink: 0;
}
.sidebar-logo {
  padding: 20px 18px 12px; font-size: 1.2rem; font-weight: 800;
  color: var(--accent); letter-spacing: -.02em; flex-shrink: 0;
}
.sidebar-logo span { color: #fff; }
.nav-section { padding: 8px 0; }
.nav-section-title {
  font-size: 0.65rem; text-transform: uppercase; letter-spacing: .1em;
  color: #4b5563; padding: 6px 18px 4px; font-weight: 600;
}
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 18px; cursor: pointer; border-radius: 0;
  font-size: 0.85rem; color: var(--sidebar-text);
  transition: background .12s, color .12s;
  white-space: nowrap;
}
.nav-item:hover { background: var(--sidebar-hover); color: #e5e7eb; }
.nav-item.active { color: var(--sidebar-active); font-weight: 600; background: #1f2937; }
.nav-item .icon { font-size: 1rem; width: 18px; text-align: center; flex-shrink: 0; }

/* ── MAIN ── */
.main { margin-left: var(--sidebar-w); flex: 1; min-width: 0; }
.topbar {
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 14px 28px; display: flex; align-items: center;
  justify-content: space-between; position: sticky; top: 0; z-index: 50;
}
.topbar h1 { font-size: 1rem; font-weight: 700; color: var(--text); }
.topbar .subtitle { font-size: 0.8rem; color: var(--muted); margin-top: 1px; }
.range-btns { display: flex; gap: 4px; }
.range-btn {
  padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600;
  cursor: pointer; border: 1px solid var(--border); background: var(--surface);
  color: var(--muted); transition: all .12s;
}
.range-btn:hover { border-color: var(--accent); color: var(--accent); }
.range-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

.content { padding: 24px 28px; }

/* ── SECTION ── */
.section { display: none; }
.section.active { display: block; }

/* ── SUMMARY STRIP ── */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px; margin-bottom: 24px;
}
.kpi-card {
  background: var(--surface); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 16px;
  border-top: 3px solid transparent;
}
.kpi-card .kpi-label {
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: .07em;
  color: var(--muted); margin-bottom: 4px; font-weight: 600;
}
.kpi-card .kpi-value { font-size: 1.6rem; font-weight: 800; line-height: 1; }
.kpi-card .kpi-sub { font-size: 0.72rem; color: var(--muted); margin-top: 4px; }
.kpi-card.c-recovery  { border-top-color: var(--green); }
.kpi-card.c-hrv       { border-top-color: var(--accent); }
.kpi-card.c-rhr       { border-top-color: var(--blue); }
.kpi-card.c-strain    { border-top-color: var(--yellow); }
.kpi-card.c-sleep     { border-top-color: var(--purple); }
.kpi-card.c-resp      { border-top-color: var(--teal); }
.kpi-card.c-spo2      { border-top-color: var(--teal); }
.kpi-card.c-days      { border-top-color: var(--border); }

/* ── METRIC GRID ── */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}
.metric-grid.wide { grid-template-columns: 1fr; }
.metric-grid.three { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 900px) {
  .metric-grid, .metric-grid.three { grid-template-columns: 1fr; }
}

/* ── METRIC CARD ── */
.metric-card {
  background: var(--surface); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 20px;
}
.metric-card-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 4px; gap: 12px;
}
.metric-card-title {
  font-size: 0.8rem; font-weight: 700; color: var(--text);
  text-transform: uppercase; letter-spacing: .05em;
}
.metric-card-val {
  font-size: 1.8rem; font-weight: 800; line-height: 1.1;
  margin-bottom: 2px;
}
.metric-card-sub { font-size: 0.75rem; color: var(--muted); margin-bottom: 12px; }
.roll-btns { display: flex; gap: 3px; flex-shrink: 0; }
.roll-btn {
  padding: 2px 7px; border-radius: 4px; font-size: 0.68rem; font-weight: 700;
  cursor: pointer; border: 1px solid var(--border); background: transparent;
  color: var(--muted); transition: all .1s;
}
.roll-btn:hover { color: var(--text); border-color: #aaa; }
.roll-btn.active { background: var(--text); color: #fff; border-color: var(--text); }

.chart-area { width: 100%; overflow: hidden; }
svg.linechart { display: block; width: 100%; overflow: visible; }

/* recovery colour */
.col-green  { color: var(--green); }
.col-yellow { color: var(--yellow); }
.col-red    { color: var(--red); }
.col-blue   { color: var(--blue); }
.col-purple { color: var(--purple); }
.col-teal   { color: var(--teal); }
.col-accent { color: var(--accent); }

/* ── TABLES ── */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
thead th {
  background: var(--bg); color: var(--muted); font-weight: 600;
  text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
  white-space: nowrap; cursor: pointer; user-select: none;
}
thead th:hover { color: var(--text); }
thead th.sorted-asc::after  { content: ' ↑'; color: var(--accent); }
thead th.sorted-desc::after { content: ' ↓'; color: var(--accent); }
tbody tr { border-bottom: 1px solid var(--border); }
tbody tr:hover { background: var(--bg); }
tbody td { padding: 7px 10px; white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }

/* recovery badge */
.rec-badge {
  display: inline-block; padding: 1px 8px; border-radius: 99px;
  font-size: 0.75rem; font-weight: 700;
}
.rec-green  { background: #d1fae5; color: #065f46; }
.rec-yellow { background: #fef3c7; color: #92400e; }
.rec-red    { background: #fee2e2; color: #991b1b; }

.badge {
  display: inline-block; padding: 1px 8px; border-radius: 99px;
  font-size: 0.72rem; font-weight: 600;
  background: #f3f4f6; color: #374151;
}

/* pagination */
.pagination { display: flex; gap: 6px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
.pagination button {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text); border-radius: 6px; padding: 4px 10px;
  cursor: pointer; font-size: 0.8rem;
}
.pagination button:hover, .pagination button.active {
  border-color: var(--accent); color: var(--accent);
}
.pagination .info { color: var(--muted); font-size: 0.8rem; }

.empty { padding: 40px; text-align: center; color: var(--muted); }

/* long-term note banner */
.longterm-note {
  background: #eff6ff; border: 1px solid #bfdbfe; border-radius: var(--radius);
  padding: 12px 16px; margin-bottom: 20px; font-size: 0.82rem; color: #1e40af;
  line-height: 1.5;
}

/* sleep stage legend row */
.stage-legend { display: flex; gap: 14px; margin-bottom: 10px; flex-wrap: wrap; }
.stage-dot {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 0.72rem; color: var(--muted);
}
.stage-dot span {
  width: 10px; height: 10px; border-radius: 2px; display: inline-block;
}
</style>
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
      <div class="topbar h1" id="section-title">Dashboard</div>
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
      <div class="longterm-note">
        These charts always show your <strong>complete WHOOP history</strong>,
        independent of the range filter above. Each point is the monthly average.
        Use the granularity toggle on each card to switch between monthly and quarterly.
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
const recClass = v => v >= 67 ? 'rec-green' : v >= 34 ? 'rec-yellow' : 'rec-red';

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

// ── SVG LINE CHART ────────────────────────────────────────────────────────
function lineChart(values, labels, opts={}) {
  const W = 560, H = opts.h || 130;
  const pad = {t:24, r:10, b:28, l:44};
  const w = W-pad.l-pad.r, h = H-pad.t-pad.b;
  const valid = values.filter(v=>v!=null);
  if (!valid.length) return '<div class="empty" style="height:100px">No data in range</div>';

  const mn = opts.min ?? Math.min(...valid);
  const mx = opts.max ?? Math.max(...valid);
  const range = mx-mn || 1;
  const color = opts.color || 'var(--accent)';
  const id = opts.id || Math.random().toString(36).slice(2);

  const px = i => pad.l + i/(Math.max(values.length-1,1)) * w;
  const py = v => pad.t + h - (v-mn)/range * h;

  // Build line path and area path segment-by-segment so gaps don't
  // produce diagonal fills — each contiguous run gets its own closed shape.
  let line = '';
  const areaSegments = [];
  let segLine = '', segFirstX = null, segLastX = null;

  values.forEach((v, i) => {
    if (v == null) {
      // close off any open segment
      if (segLine && segFirstX !== null) {
        areaSegments.push(
          segLine +
          `L${segLastX},${(pad.t+h).toFixed(1)}` +
          `L${segFirstX},${(pad.t+h).toFixed(1)}Z`
        );
        segLine = ''; segFirstX = null; segLastX = null;
      }
      return;
    }
    const x = px(i).toFixed(1), y = py(v).toFixed(1);
    if (segLine === '') {
      line    += `M${x},${y}`;
      segLine  = `M${x},${y}`;
      segFirstX = x;
    } else {
      line    += `L${x},${y}`;
      segLine += `L${x},${y}`;
    }
    segLastX = x;
  });
  // close final segment
  if (segLine && segFirstX !== null) {
    areaSegments.push(
      segLine +
      `L${segLastX},${(pad.t+h).toFixed(1)}` +
      `L${segFirstX},${(pad.t+h).toFixed(1)}Z`
    );
  }
  const area = areaSegments.join(' ');

  // y ticks — 4 levels
  const ticks = 4;
  const yT = Array.from({length:ticks+1},(_,i)=>mn+range*i/ticks);
  const yTsvg = yT.map(v => {
    const y=py(v).toFixed(1);
    const lbl = opts.yFmt ? opts.yFmt(v) : v.toFixed(1);
    return `<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}"
                  stroke="#e5e7eb" stroke-width="1"/>
            <text x="${pad.l-4}" y="${y}" text-anchor="end" dominant-baseline="middle"
                  font-size="10" fill="#9ca3af">${lbl}</text>`;
  }).join('');

  // x labels — ~6 evenly spaced
  const step = Math.max(1,Math.floor(labels.length/6));
  const xLbls = labels.map((l,i) => {
    if(i%step!==0 && i!==labels.length-1) return '';
    return `<text x="${px(i).toFixed(1)}" y="${H-4}" text-anchor="middle"
                  font-size="10" fill="#9ca3af">${l}</text>`;
  }).join('');

  // dots at first and last non-null values
  const firstI = values.findIndex(v => v != null);
  const lastI  = values.reduce((acc, v, i) => v != null ? i : acc, -1);
  const dotSvg = firstI >= 0
    ? `<circle cx="${px(firstI).toFixed(1)}" cy="${py(values[firstI]).toFixed(1)}"
               r="3" fill="${color}"/>
       <circle cx="${px(lastI).toFixed(1)}" cy="${py(values[lastI]).toFixed(1)}"
               r="3.5" fill="${color}"/>` : '';

  return `<svg class="linechart" viewBox="0 0 ${W} ${H}"
               preserveAspectRatio="xMinYMin meet" style="height:${H}px">
    <defs>
      <linearGradient id="g${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.18"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${yTsvg}
    <path d="${area}" fill="url(#g${id})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2"
          stroke-linejoin="round" stroke-linecap="round"/>
    ${dotSvg}
    ${xLbls}
  </svg>`;
}

// ── METRIC CARD BUILDER ───────────────────────────────────────────────────
// opts: { id, title, field, color, unit, decimals, rollWindows, yFmt, extraSvg, h }
function buildMetricCard(data, opts) {
  const { id, title, field, color, unit='', decimals=1,
          rollWindows=[7,14,30,90], h=130 } = opts;

  if (!rollState[id]) rollState[id] = rollWindows[1] || rollWindows[0];

  const raw    = data.map(d => d[field]);
  const labels = data.map(d => d.date ? d.date.slice(5) : '');
  const n      = rollState[id];
  const values = n === 1 ? raw : rolling(data, field, n);

  const valid = raw.filter(v=>v!=null);
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

  const svg = lineChart(values, labels, {
    id, color,
    yFmt: opts.yFmt || (v => v.toFixed(decimals)),
    h
  });

  return `<div class="metric-card">
    <div class="metric-card-header">
      <div>
        <div class="metric-card-title">${title}</div>
        <div class="metric-card-val" style="color:${color}">${curStr}</div>
        <div class="metric-card-sub">${subStr}</div>
      </div>
      <div class="roll-btns">${rollBtns}</div>
    </div>
    <div class="chart-area" id="chart-${id}">${svg}</div>
  </div>`;
}

// Rolling window button clicks (event delegation)
document.addEventListener('click', e => {
  const b = e.target.closest('.roll-btn');
  if (!b) return;
  const cid = b.dataset.card, win = parseInt(b.dataset.win);
  rollState[cid] = win;
  // re-render just this card's chart area
  const el = document.getElementById('chart-'+cid);
  if (!el) return;
  // find parent card, re-render
  b.closest('.metric-card').querySelectorAll('.roll-btn').forEach(rb => {
    rb.classList.toggle('active', parseInt(rb.dataset.win)===win);
  });
  const raw    = filtered.map(d => d[cid] || d[cid.replace('-','_')]);
  // use field mapping
  const field  = CARD_FIELDS[cid];
  const color  = CARD_COLORS[cid];
  const dec    = CARD_DECIMALS[cid] || 1;
  const values = win===1 ? filtered.map(d=>d[field]) : rolling(filtered, field, win);
  const labels = filtered.map(d=>d.date?d.date.slice(5):'');
  el.innerHTML = lineChart(values, labels, {id:cid, color,
    yFmt: v=>v.toFixed(dec), h: parseInt(el.closest('.metric-card').dataset.h)||130});
});

const CARD_FIELDS = {
  recovery:'recovery_score', hrv:'hrv_rmssd_ms', rhr:'resting_hr',
  strain:'strain', 'strain-roll':'strain', sleep:'sleep_performance_pct',
  resp:'respiratory_rate', spo2:'spo2_pct', skin:'skin_temp_celsius',
  tib:'sleep_total_in_bed_min', rem:'sleep_rem_min', sws:'sleep_sws_min',
  light:'sleep_light_min', awake:'sleep_awake_min', latency:'sleep_latency_min',
  dist:'sleep_disturbances',
};
const CARD_COLORS = {
  recovery:'var(--green)', hrv:'var(--accent)', rhr:'var(--blue)',
  strain:'var(--yellow)', 'strain-roll':'var(--yellow)',
  sleep:'var(--purple)', resp:'var(--teal)', spo2:'var(--teal)',
  skin:'var(--accent)', tib:'var(--blue)', rem:'var(--purple)',
  sws:'var(--teal)', light:'var(--blue)', awake:'#9ca3af',
  latency:'var(--yellow)', dist:'var(--red)',
};
const CARD_DECIMALS = {
  recovery:0, hrv:1, rhr:0, strain:1, sleep:0, resp:2, spo2:1,
  skin:1, tib:1, rem:1, sws:1, light:1, awake:1, latency:1, dist:0,
};

// ── KPI STRIP ─────────────────────────────────────────────────────────────
function renderKPI() {
  const rec  = filtered.map(d=>d.recovery_score).filter(v=>v!=null);
  const hrv  = filtered.map(d=>d.hrv_rmssd_ms).filter(v=>v!=null);
  const rhr  = filtered.map(d=>d.resting_hr).filter(v=>v!=null);
  const str  = filtered.map(d=>d.strain).filter(v=>v!=null);
  const sp   = filtered.map(d=>d.sleep_performance_pct).filter(v=>v!=null);
  const resp = filtered.map(d=>d.respiratory_rate).filter(v=>v!=null);
  const spo2 = filtered.map(d=>d.spo2_pct).filter(v=>v!=null);

  const dates = filtered.map(d=>d.date).filter(Boolean).sort();
  document.getElementById('header-sub').textContent =
    dates.length ? `${dates[0]} → ${dates[dates.length-1]} · ${dates.length} days` : 'No data';

  const defs = [
    ['Recovery',    rec.length  ? avg(rec).toFixed(0)+'%' : '—',  'c-recovery',  avg(rec)?.toFixed(0)+'% avg'],
    ['HRV rMSSD',   hrv.length  ? avg(hrv).toFixed(1)+' ms' : '—','c-hrv',       ''],
    ['Resting HR',  rhr.length  ? avg(rhr).toFixed(0)+' bpm' : '—','c-rhr',      ''],
    ['Day Strain',  str.length  ? avg(str).toFixed(1) : '—',       'c-strain',   ''],
    ['Sleep Perf',  sp.length   ? avg(sp).toFixed(0)+'%' : '—',   'c-sleep',    ''],
    ['Resp Rate',   resp.length ? avg(resp).toFixed(2) : '—',      'c-resp',     'br/min'],
    ['SpO2',        spo2.length ? avg(spo2).toFixed(1)+'%' : '—', 'c-spo2',     ''],
    ['Days',        filtered.length, 'c-days', filtWorkouts.length+' workouts'],
  ];
  document.getElementById('kpi-strip').innerHTML = defs.map(([l,v,cls,sub]) =>
    `<div class="kpi-card ${cls}">
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
  const maxC = Math.max(...sorted.map(e=>e[1]),1);
  const rowH = 26, padL = 150, W = 560;
  const H = rowH * sorted.length;
  const bars = sorted.map(([name, n], i) => {
    const bw = ((n/maxC)*(W-padL-50)).toFixed(1);
    const y  = i * rowH;
    return `<text x="${padL-6}" y="${y+rowH/2}" text-anchor="end"
                  dominant-baseline="middle" font-size="12" fill="var(--text)">${name}</text>
            <rect x="${padL}" y="${y+4}" width="${bw}" height="${rowH-8}"
                  rx="3" fill="var(--accent)" opacity="0.75"/>
            <text x="${padL+parseFloat(bw)+6}" y="${y+rowH/2}"
                  dominant-baseline="middle" font-size="11" fill="var(--muted)">${n}</text>`;
  }).join('');
  document.getElementById('sport-bar-chart').innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;max-height:400px">${bars}</svg>`;

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
      ?`<span class="rec-badge ${recClass(d.recovery_score)}">${Math.round(d.recovery_score)}%</span>`
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
// Returns [{label, <field>: avg}, ...]
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

function buildLTCard(opts) {
  const { id, title, field, color, unit='', decimals=1 } = opts;
  if (!ltGranState[id]) ltGranState[id] = 'month';
  const gran = ltGranState[id];

  const pts    = aggregateLT(field, gran);
  const values = pts.map(p => p.value);
  const labels = pts.map(p => p.label);

  const valid = values.filter(v=>v!=null);
  const cur   = valid.length ? valid[valid.length-1] : null;
  const mn    = valid.length ? Math.min(...valid) : null;
  const mx    = valid.length ? Math.max(...valid) : null;
  const av    = valid.length ? valid.reduce((s,v)=>s+v,0)/valid.length : null;

  const curStr = cur!=null ? cur.toFixed(decimals)+unit : '—';
  const subStr = av!=null
    ? `avg ${av.toFixed(decimals)}${unit} · min ${mn.toFixed(decimals)}${unit} · max ${mx.toFixed(decimals)}${unit} · ${pts.length} ${gran==='quarter'?'quarters':'months'}`
    : 'No data';

  const granBtns = ['month','quarter'].map(g =>
    `<button class="roll-btn lt-gran-btn${ltGranState[id]===g?' active':''}"
             data-ltcard="${id}" data-gran="${g}">${g==='month'?'Monthly':'Quarterly'}</button>`
  ).join('');

  const svg = lineChart(values, labels, {
    id: 'lt-'+id, color,
    yFmt: opts.yFmt || (v => v.toFixed(decimals)),
    h: 160,
  });

  return `<div class="metric-card">
    <div class="metric-card-header">
      <div>
        <div class="metric-card-title">${title}</div>
        <div class="metric-card-val" style="color:${color}">${curStr}</div>
        <div class="metric-card-sub">${subStr}</div>
      </div>
      <div class="roll-btns">${granBtns}</div>
    </div>
    <div class="chart-area" id="ltchart-${id}">${svg}</div>
  </div>`;
}

// granularity button clicks (event delegation)
document.addEventListener('click', e => {
  const b = e.target.closest('.lt-gran-btn');
  if (!b) return;
  const id = b.dataset.ltcard, gran = b.dataset.gran;
  ltGranState[id] = gran;
  b.closest('.metric-card').querySelectorAll('.lt-gran-btn').forEach(rb =>
    rb.classList.toggle('active', rb.dataset.gran === gran)
  );
  const el = document.getElementById('ltchart-'+id);
  if (!el) return;
  const pts    = aggregateLT(CARD_FIELDS[id], gran);
  const values = pts.map(p => p.value);
  const labels = pts.map(p => p.label);
  const color  = CARD_COLORS[id];
  const dec    = CARD_DECIMALS[id] || 1;
  el.innerHTML = lineChart(values, labels, {id:'lt-'+id, color, yFmt:v=>v.toFixed(dec), h:160});
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
];

function renderLongTerm() {
  document.getElementById('longterm-charts').innerHTML =
    LT_METRICS.map(m => buildLTCard(m)).join('');
}

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

    html = HTML_TEMPLATE.replace("{data_js}", data_js)
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
