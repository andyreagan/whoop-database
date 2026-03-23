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
            ROUND(sleep_total_in_bed_ms / 60000.0, 1)  AS sleep_total_in_bed_min,
            ROUND(sleep_light_ms        / 60000.0, 1)  AS sleep_light_min,
            ROUND(sleep_rem_ms          / 60000.0, 1)  AS sleep_rem_min,
            ROUND(sleep_slow_wave_ms    / 60000.0, 1)  AS sleep_sws_min,
            ROUND(sleep_awake_ms        / 60000.0, 1)  AS sleep_awake_min,
            sleep_disturbances,
            ROUND(sleep_latency_ms      / 60000.0, 1)  AS sleep_latency_min,
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


# ---------------------------------------------------------------------------
# HTML template — single self-contained file
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Andy's WHOOP Dashboard</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg:      #0d0f14;
    --surface: #161921;
    --border:  #252836;
    --accent:  #00d4b4;   /* WHOOP teal */
    --accent2: #00a896;
    --red:     #e63946;
    --yellow:  #f4a261;
    --green:   #43aa8b;
    --blue:    #5fa8d3;
    --purple:  #9d77c9;
    --text:    #e8eaf0;
    --muted:   #7a7f94;
    --radius:  8px;
    --font:    'Inter', system-ui, sans-serif;
  }}
  body {{ font-family: var(--font); background: var(--bg); color: var(--text);
          font-size: 14px; line-height: 1.5; }}
  a {{ color: var(--accent); text-decoration: none; }}

  header {{ background: var(--surface); border-bottom: 1px solid var(--border);
             padding: 12px 24px; display: flex; align-items: center; gap: 16px; }}
  header h1 {{ font-size: 1.2rem; font-weight: 700; color: var(--accent); }}
  header .subtitle {{ color: var(--muted); font-size: 0.85rem; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 20px 16px; }}

  /* filters */
  .filters {{ background: var(--surface); border: 1px solid var(--border);
               border-radius: var(--radius); padding: 16px; margin-bottom: 20px; }}
  .filters h2 {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: .08em;
                  color: var(--muted); margin-bottom: 12px; }}
  .filter-row {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }}
  .filter-group {{ display: flex; flex-direction: column; gap: 4px; }}
  .filter-group label {{ font-size: 0.75rem; color: var(--muted); }}
  select, input[type=date] {{
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 6px; padding: 6px 10px; font-size: 0.85rem; min-width: 130px; cursor: pointer;
  }}
  select:focus, input:focus {{ outline: 2px solid var(--accent); border-color: var(--accent); }}
  .btn {{ background: var(--accent); color: #000; border: none; border-radius: 6px;
           padding: 7px 16px; cursor: pointer; font-size: 0.85rem; font-weight: 700; }}
  .btn:hover {{ background: var(--accent2); }}
  .btn.secondary {{ background: var(--surface); border: 1px solid var(--border);
                    color: var(--text); }}
  .btn.secondary:hover {{ border-color: var(--accent); color: var(--accent); }}

  /* cards */
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(155px, 1fr));
             gap: 12px; margin-bottom: 20px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 14px 16px; }}
  .card .label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: .06em;
                   color: var(--muted); margin-bottom: 4px; }}
  .card .value {{ font-size: 1.5rem; font-weight: 700; }}
  .card .sub   {{ font-size: 0.75rem; color: var(--muted); margin-top: 2px; }}

  /* tabs */
  .tabs {{ display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }}
  .tab {{ padding: 8px 16px; cursor: pointer; border-radius: 6px 6px 0 0;
           font-size: 0.85rem; color: var(--muted); border: 1px solid transparent;
           border-bottom: none; margin-bottom: -1px; }}
  .tab.active {{ background: var(--surface); border-color: var(--border);
                  color: var(--text); font-weight: 600; }}
  .tab:hover:not(.active) {{ color: var(--text); }}
  .panel {{ display: none; }}
  .panel.active {{ display: block; }}

  /* tables */
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  thead th {{ background: var(--surface); color: var(--muted); font-weight: 600;
               text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
               white-space: nowrap; cursor: pointer; user-select: none; }}
  thead th:hover {{ color: var(--text); }}
  thead th.sorted-asc::after  {{ content: ' ↑'; color: var(--accent); }}
  thead th.sorted-desc::after {{ content: ' ↓'; color: var(--accent); }}
  tbody tr {{ border-bottom: 1px solid var(--border); }}
  tbody tr:hover {{ background: var(--surface); }}
  tbody td {{ padding: 7px 10px; white-space: nowrap; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}

  /* charts */
  .chart-wrap {{ background: var(--surface); border: 1px solid var(--border);
                  border-radius: var(--radius); padding: 16px; margin-bottom: 20px; }}
  .chart-wrap h3 {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: .06em;
                     color: var(--muted); margin-bottom: 12px; }}
  .chart-grid {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .chart-grid .chart-wrap {{ flex: 1; min-width: 280px; margin-bottom: 0; }}

  /* sparkline / multi-metric chart */
  .metric-chart {{ width: 100%; overflow-x: auto; }}
  svg.spark {{ display: block; width: 100%; }}

  /* recovery colour bands */
  .rec-high   {{ color: var(--green); }}
  .rec-med    {{ color: var(--yellow); }}
  .rec-low    {{ color: var(--red); }}

  /* sport badge */
  .badge {{ display: inline-block; padding: 1px 8px; border-radius: 99px;
             font-size: 0.7rem; font-weight: 600; background: var(--border);
             color: var(--muted); }}

  /* pagination */
  .pagination {{ display: flex; gap: 6px; align-items: center; margin-top: 12px; flex-wrap: wrap; }}
  .pagination button {{ background: var(--surface); border: 1px solid var(--border);
    color: var(--text); border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 0.8rem; }}
  .pagination button:hover, .pagination button.active {{ border-color: var(--accent); color: var(--accent); }}
  .pagination .info {{ color: var(--muted); font-size: 0.8rem; }}

  .empty {{ padding: 40px; text-align: center; color: var(--muted); }}

  @media (max-width: 600px) {{
    .filter-row {{ flex-direction: column; }}
    select, input {{ min-width: 100%; }}
  }}
</style>
</head>
<body>

<header>
  <div>
    <h1>⚡ WHOOP Dashboard</h1>
    <div class="subtitle" id="header-sub">Loading…</div>
  </div>
</header>

<div class="container">

  <!-- FILTERS -->
  <div class="filters">
    <h2>Date Range</h2>
    <div class="filter-row">
      <div class="filter-group">
        <label>From</label>
        <input type="date" id="f-from">
      </div>
      <div class="filter-group">
        <label>To</label>
        <input type="date" id="f-to">
      </div>
      <div class="filter-group">
        <label>Quick range</label>
        <select id="f-quick">
          <option value="">Custom</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
          <option value="180">Last 180 days</option>
          <option value="365">Last year</option>
          <option value="0">All time</option>
        </select>
      </div>
      <div class="filter-group" style="justify-content:flex-end">
        <button class="btn secondary" id="btn-reset">Reset</button>
      </div>
    </div>
  </div>

  <!-- SUMMARY CARDS -->
  <div class="cards" id="cards"></div>

  <!-- TABS -->
  <div class="tabs">
    <div class="tab active" data-tab="trends">Trends</div>
    <div class="tab" data-tab="weekly">Weekly</div>
    <div class="tab" data-tab="sleep">Sleep</div>
    <div class="tab" data-tab="workouts">Workouts</div>
    <div class="tab" data-tab="daily">Daily Log</div>
  </div>

  <!-- TRENDS PANEL -->
  <div class="panel active" id="panel-trends">
    <div class="chart-wrap">
      <h3>Recovery Score — 7-day rolling average</h3>
      <div id="chart-recovery" class="metric-chart"></div>
    </div>
    <div class="chart-grid">
      <div class="chart-wrap">
        <h3>HRV (rMSSD ms) — 7-day rolling avg</h3>
        <div id="chart-hrv" class="metric-chart"></div>
      </div>
      <div class="chart-wrap">
        <h3>Resting Heart Rate (bpm) — 7-day rolling avg</h3>
        <div id="chart-rhr" class="metric-chart"></div>
      </div>
    </div>
    <div class="chart-grid">
      <div class="chart-wrap">
        <h3>Day Strain — 28-day rolling avg</h3>
        <div id="chart-strain" class="metric-chart"></div>
      </div>
      <div class="chart-wrap">
        <h3>Respiratory Rate (breaths/min) — 7-day rolling avg</h3>
        <div id="chart-resp" class="metric-chart"></div>
      </div>
    </div>
  </div>

  <!-- WEEKLY PANEL -->
  <div class="panel" id="panel-weekly">
    <div class="chart-wrap">
      <h3>Weekly Averages</h3>
      <div id="chart-weekly-strain" class="metric-chart"></div>
    </div>
    <div class="table-wrap">
      <table id="weekly-table">
        <thead><tr>
          <th>Week</th>
          <th class="num">Avg Strain</th>
          <th class="num">Avg Recovery</th>
          <th class="num">Avg HRV (ms)</th>
          <th class="num">Avg RHR</th>
          <th class="num">Avg Sleep %</th>
          <th class="num">Workouts</th>
        </tr></thead>
        <tbody id="weekly-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- SLEEP PANEL -->
  <div class="panel" id="panel-sleep">
    <div class="chart-grid">
      <div class="chart-wrap">
        <h3>Sleep Performance % — 7-day rolling avg</h3>
        <div id="chart-sleep-perf" class="metric-chart"></div>
      </div>
      <div class="chart-wrap">
        <h3>Total Time in Bed (hours)</h3>
        <div id="chart-sleep-tib" class="metric-chart"></div>
      </div>
    </div>
    <div class="chart-wrap">
      <h3>Sleep Stage Breakdown — weekly averages (hours)</h3>
      <div id="chart-sleep-stages" class="metric-chart"></div>
    </div>
    <div class="table-wrap" style="margin-top:20px">
      <table>
        <thead><tr>
          <th>Month</th>
          <th class="num">Avg Perf %</th>
          <th class="num">Avg In Bed</th>
          <th class="num">Avg Light</th>
          <th class="num">Avg REM</th>
          <th class="num">Avg SWS</th>
          <th class="num">Avg Resp Rate</th>
          <th class="num">Avg Disturbances</th>
        </tr></thead>
        <tbody id="sleep-monthly-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- WORKOUTS PANEL -->
  <div class="panel" id="panel-workouts">
    <div class="chart-wrap">
      <h3>Workouts by sport (count)</h3>
      <div id="chart-sport-counts" class="metric-chart"></div>
    </div>
    <div class="table-wrap">
      <table id="wkt-table">
        <thead><tr>
          <th data-col="date">Date</th>
          <th data-col="sport_name">Sport</th>
          <th data-col="strain" class="num">Strain</th>
          <th data-col="avg_hr" class="num">Avg HR</th>
          <th data-col="max_hr" class="num">Max HR</th>
          <th data-col="kilojoules" class="num">kJ</th>
          <th data-col="distance_meter" class="num">Dist (km)</th>
          <th class="num">Z1</th>
          <th class="num">Z2</th>
          <th class="num">Z3</th>
          <th class="num">Z4</th>
          <th class="num">Z5</th>
        </tr></thead>
        <tbody id="wkt-tbody"></tbody>
      </table>
    </div>
    <div class="pagination" id="wkt-pagination"></div>
  </div>

  <!-- DAILY LOG PANEL -->
  <div class="panel" id="panel-daily">
    <div class="table-wrap">
      <table id="daily-table">
        <thead><tr>
          <th data-col="date">Date</th>
          <th data-col="strain" class="num">Strain</th>
          <th data-col="recovery_score" class="num">Recovery</th>
          <th data-col="hrv_rmssd_ms" class="num">HRV (ms)</th>
          <th data-col="resting_hr" class="num">RHR</th>
          <th data-col="respiratory_rate" class="num">Resp</th>
          <th data-col="spo2_pct" class="num">SpO2 %</th>
          <th data-col="sleep_performance_pct" class="num">Sleep %</th>
          <th data-col="sleep_total_in_bed_min" class="num">In Bed</th>
          <th data-col="sleep_rem_min" class="num">REM</th>
          <th data-col="sleep_sws_min" class="num">SWS</th>
          <th data-col="workout_count" class="num">Workouts</th>
        </tr></thead>
        <tbody id="daily-tbody"></tbody>
      </table>
    </div>
    <div class="pagination" id="daily-pagination"></div>
  </div>

</div>

<script>
// ── DATA ──────────────────────────────────────────────────────────────────
{data_js}

// ── HELPERS ───────────────────────────────────────────────────────────────
const fmtMin = m => {{
  if (m == null) return '—';
  const h = Math.floor(m / 60), mn = Math.round(m % 60);
  return h ? `${{h}}h ${{String(mn).padStart(2,'0')}}m` : `${{mn}}m`;
}};
const fmtN1  = v => v == null ? '—' : Number(v).toFixed(1);
const fmtN0  = v => v == null ? '—' : Math.round(v);
const fmtKm  = m => m == null ? '—' : (m / 1000).toFixed(1) + ' km';
const isoWeek = d => {{
  const dt = new Date(d + 'T12:00:00');
  const jan4 = new Date(dt.getFullYear(), 0, 4);
  const w = Math.ceil(((dt - jan4) / 86400000 + jan4.getDay() + 1) / 7);
  const y = w === 0 ? dt.getFullYear()-1 : (w > 52 && dt.getMonth()===0 ? dt.getFullYear()-1 : dt.getFullYear());
  return `${{y}}-W${{String(w).padStart(2,'0')}}`;
}};
const isoMonth = d => d ? d.slice(0,7) : null;
const avg = arr => arr.length ? arr.reduce((s,v)=>s+v,0)/arr.length : null;
const rolling = (data, field, n) => {{
  const out = [];
  for (let i = 0; i < data.length; i++) {{
    const slice = data.slice(Math.max(0, i-n+1), i+1).map(d=>d[field]).filter(v=>v!=null);
    out.push(slice.length ? slice.reduce((s,v)=>s+v,0)/slice.length : null);
  }}
  return out;
}};
const recClass = v => v >= 67 ? 'rec-high' : v >= 34 ? 'rec-med' : 'rec-low';

// ── STATE ─────────────────────────────────────────────────────────────────
let filtered      = [...DAILY];
let filtWorkouts  = [...WORKOUTS];
let dailySortCol  = 'date', dailySortDir = -1;
let wktSortCol    = 'date', wktSortDir   = -1;
let dailyPage = 1, wktPage = 1;
const PAGE = 60;

// ── FILTER ────────────────────────────────────────────────────────────────
const fromInput  = document.getElementById('f-from');
const toInput    = document.getElementById('f-to');
const quickSel   = document.getElementById('f-quick');

function applyFilters() {{
  const from = fromInput.value;
  const to   = toInput.value;
  filtered     = DAILY.filter(d => (!from || d.date >= from) && (!to || d.date <= to));
  filtWorkouts = WORKOUTS.filter(w => {{
    const d = (w.start||'').slice(0,10);
    return (!from || d >= from) && (!to || d <= to);
  }});
  dailyPage = wktPage = 1;
  render();
}}

quickSel.addEventListener('change', () => {{
  const v = quickSel.value;
  if (v === '') return;
  if (v === '0') {{ fromInput.value=''; toInput.value=''; }}
  else {{
    const to = new Date(); to.setHours(23,59,59);
    const fr = new Date(); fr.setDate(fr.getDate() - parseInt(v));
    fromInput.value = fr.toISOString().slice(0,10);
    toInput.value   = to.toISOString().slice(0,10);
  }}
  applyFilters();
}});
fromInput.addEventListener('change', () => {{ quickSel.value=''; applyFilters(); }});
toInput.addEventListener('change',   () => {{ quickSel.value=''; applyFilters(); }});
document.getElementById('btn-reset').addEventListener('click', () => {{
  fromInput.value=''; toInput.value=''; quickSel.value='';
  filtered = [...DAILY]; filtWorkouts = [...WORKOUTS];
  dailyPage = wktPage = 1;
  render();
}});

// ── SVG SPARKLINE ─────────────────────────────────────────────────────────
function sparkline(values, labels, opts={{}}) {{
  const W = 900, H = opts.h || 120;
  const pad = {{t:20, r:8, b:28, l:42}};
  const w = W - pad.l - pad.r, h = H - pad.t - pad.b;
  const valid = values.filter(v=>v!=null);
  if (!valid.length) return '<p style="color:var(--muted);padding:8px">No data</p>';

  const mn = opts.min ?? Math.min(...valid);
  const mx = opts.max ?? Math.max(...valid);
  const range = mx - mn || 1;

  const px = i  => pad.l + (i / (values.length - 1)) * w;
  const py = v  => pad.t + h - ((v - mn) / range * h);

  // Line path (skip nulls)
  let d = '', prev = null;
  values.forEach((v, i) => {{
    if (v == null) {{ prev = null; return; }}
    const x = px(i), y = py(v);
    d += prev == null ? `M${{x.toFixed(1)}},${{y.toFixed(1)}}` : `L${{x.toFixed(1)}},${{y.toFixed(1)}}`;
    prev = i;
  }});

  // Area fill
  let area = d;
  const lastIdx = values.reduceRight((acc, v, i) => acc < 0 && v != null ? i : acc, -1);
  const firstIdx = values.findIndex(v => v != null);
  if (firstIdx >= 0 && lastIdx >= 0) {{
    area += `L${{px(lastIdx).toFixed(1)}},${{(pad.t+h).toFixed(1)}}` +
            `L${{px(firstIdx).toFixed(1)}},${{(pad.t+h).toFixed(1)}}Z`;
  }}

  // Y axis ticks
  const tickCount = 4;
  const yTicks = Array.from({{length: tickCount+1}}, (_,i) => mn + (range * i / tickCount));
  const yTicksSvg = yTicks.map(v => {{
    const y = py(v).toFixed(1);
    return `<line x1="${{pad.l}}" x2="${{W-pad.r}}" y1="${{y}}" y2="${{y}}" stroke="var(--border)" stroke-width="1"/>
            <text x="${{pad.l-4}}" y="${{y}}" text-anchor="end" dominant-baseline="middle"
                  font-size="10" fill="var(--muted)">${{opts.fmt ? opts.fmt(v) : v.toFixed(1)}}</text>`;
  }}).join('');

  // X axis labels — show ~8 evenly spaced
  const labelStep = Math.max(1, Math.floor(labels.length / 8));
  const xLabels = labels.map((l, i) => {{
    if (i % labelStep !== 0 && i !== labels.length-1) return '';
    const x = px(i).toFixed(1);
    return `<text x="${{x}}" y="${{H - 6}}" text-anchor="middle" font-size="10" fill="var(--muted)">${{l}}</text>`;
  }}).join('');

  const color = opts.color || 'var(--accent)';

  return `<svg class="spark" viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none" style="height:${{H}}px">
    <defs>
      <linearGradient id="grad${{opts.id||''}}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${{color}}" stop-opacity="0.3"/>
        <stop offset="100%" stop-color="${{color}}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${{yTicksSvg}}
    <path d="${{area}}" fill="url(#grad${{opts.id||''}})" />
    <path d="${{d}}" fill="none" stroke="${{color}}" stroke-width="1.5" stroke-linejoin="round"/>
    ${{xLabels}}
  </svg>`;
}}

// bar chart (horizontal) for sport counts
function hbarChart(labels, values, color) {{
  const max = Math.max(...values, 1);
  const rowH = 28, pad = 140;
  const W = 700, H = rowH * labels.length;
  const bars = labels.map((l, i) => {{
    const bw = (values[i] / max * (W - pad - 40)).toFixed(1);
    const y = i * rowH;
    return `<text x="${{pad - 6}}" y="${{y + rowH/2}}" text-anchor="end" dominant-baseline="middle"
                  font-size="11" fill="var(--text)">${{l}}</text>
            <rect x="${{pad}}" y="${{y+4}}" width="${{bw}}" height="${{rowH-8}}" rx="3"
                  fill="${{color}}" opacity="0.8"/>
            <text x="${{pad + parseFloat(bw) + 6}}" y="${{y + rowH/2}}" dominant-baseline="middle"
                  font-size="11" fill="var(--muted)">${{values[i]}}</text>`;
  }}).join('');
  return `<svg viewBox="0 0 ${{W}} ${{H}}" style="width:100%;height:${{H}}px">
    ${{bars}}
  </svg>`;
}}

// stacked bar chart for sleep stages
function stackedBarChart(weeks, stages, colors, stageLabels) {{
  const W = 900, H = 160;
  const pad = {{t:20, r:8, b:28, l:42}};
  const w = W - pad.l - pad.r, h = H - pad.t - pad.b;
  const maxVal = Math.max(...weeks.map(wk => stages.reduce((s, st) => s + (wk[st]||0), 0)), 1);
  const barW   = Math.max(2, w / weeks.length - 1);

  const bars = weeks.map((wk, i) => {{
    const x = pad.l + i / weeks.length * w;
    let y = pad.t + h;
    return stages.map((st, si) => {{
      const val = wk[st] || 0;
      const bh  = val / maxVal * h;
      y -= bh;
      return `<rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{barW.toFixed(1)}}"
                    height="${{bh.toFixed(1)}}" fill="${{colors[si]}}" opacity="0.85"/>`;
    }}).join('');
  }}).join('');

  // x labels
  const step = Math.max(1, Math.floor(weeks.length / 8));
  const xlbls = weeks.map((wk, i) => {{
    if (i % step !== 0) return '';
    const x = pad.l + i / weeks.length * w;
    return `<text x="${{x.toFixed(1)}}" y="${{H-6}}" font-size="10" fill="var(--muted)">${{wk.week}}</text>`;
  }}).join('');

  // y ticks
  const yticks = [0, 2, 4, 6, 8].map(v => {{
    const y = pad.t + h - (v / maxVal * h);
    return `<line x1="${{pad.l}}" x2="${{W-pad.r}}" y1="${{y.toFixed(1)}}" y2="${{y.toFixed(1)}}"
                  stroke="var(--border)" stroke-width="1"/>
            <text x="${{pad.l-4}}" y="${{y.toFixed(1)}}" text-anchor="end" dominant-baseline="middle"
                  font-size="10" fill="var(--muted)">${{v}}h</text>`;
  }}).join('');

  // legend
  const legend = stages.map((st, si) =>
    `<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;font-size:.75rem">
      <span style="width:10px;height:10px;border-radius:2px;background:${{colors[si]}};display:inline-block"></span>
      ${{stageLabels[si]}}
    </span>`).join('');

  return `<div style="margin-bottom:8px">${{legend}}</div>
    <svg viewBox="0 0 ${{W}} ${{H}}" style="width:100%;height:${{H}}px">
      ${{yticks}}${{bars}}${{xlbls}}
    </svg>`;
}}

// ── RENDER FUNCTIONS ──────────────────────────────────────────────────────

function renderCards() {{
  const n = filtered.length;
  const rec  = filtered.map(d=>d.recovery_score).filter(v=>v!=null);
  const hrv  = filtered.map(d=>d.hrv_rmssd_ms).filter(v=>v!=null);
  const rhr  = filtered.map(d=>d.resting_hr).filter(v=>v!=null);
  const str  = filtered.map(d=>d.strain).filter(v=>v!=null);
  const sp   = filtered.map(d=>d.sleep_performance_pct).filter(v=>v!=null);
  const resp = filtered.map(d=>d.respiratory_rate).filter(v=>v!=null);
  const wkts = filtWorkouts.length;

  const dates = filtered.map(d=>d.date).filter(Boolean).sort();
  document.getElementById('header-sub').textContent =
    n ? `${{n}} days · ${{dates[0]}} → ${{dates[dates.length-1]}}` : 'No data';

  const defs = [
    ['Avg Recovery',  rec.length  ? avg(rec).toFixed(0)  + '%' : '—', ''],
    ['Avg HRV',       hrv.length  ? avg(hrv).toFixed(1)  + ' ms' : '—', ''],
    ['Avg RHR',       rhr.length  ? avg(rhr).toFixed(0)  + ' bpm' : '—', ''],
    ['Avg Strain',    str.length  ? avg(str).toFixed(1)  : '—', ''],
    ['Sleep Perf',    sp.length   ? avg(sp).toFixed(0)   + '%' : '—', ''],
    ['Resp Rate',     resp.length ? avg(resp).toFixed(2) + ' br/min' : '—', ''],
    ['Days',          n, ''],
    ['Workouts',      wkts, ''],
  ];
  document.getElementById('cards').innerHTML = defs.map(([l,v]) =>
    `<div class="card"><div class="label">${{l}}</div><div class="value">${{v}}</div></div>`
  ).join('');
}}

function renderTrends() {{
  const dates  = filtered.map(d => d.date);
  const labels = dates;

  document.getElementById('chart-recovery').innerHTML = sparkline(
    rolling(filtered, 'recovery_score', 7), labels,
    {{id:'rec', color:'var(--green)', fmt: v => v.toFixed(0), h:130}});

  document.getElementById('chart-hrv').innerHTML = sparkline(
    rolling(filtered, 'hrv_rmssd_ms', 7), labels,
    {{id:'hrv', color:'var(--accent)', fmt: v => v.toFixed(0), h:110}});

  document.getElementById('chart-rhr').innerHTML = sparkline(
    rolling(filtered, 'resting_hr', 7), labels,
    {{id:'rhr', color:'var(--red)', fmt: v => v.toFixed(0), h:110}});

  document.getElementById('chart-strain').innerHTML = sparkline(
    rolling(filtered, 'strain', 28), labels,
    {{id:'str', color:'var(--yellow)', fmt: v => v.toFixed(1), h:110}});

  document.getElementById('chart-resp').innerHTML = sparkline(
    rolling(filtered, 'respiratory_rate', 7), labels,
    {{id:'resp', color:'var(--blue)', fmt: v => v.toFixed(1), h:110}});
}}

function renderWeekly() {{
  const weeks = {{}};
  filtered.forEach(d => {{
    const w = isoWeek(d.date);
    if (!weeks[w]) weeks[w] = {{week:w, strain:[], rec:[], hrv:[], rhr:[], sleep:[], wkts:0}};
    if (d.strain != null) weeks[w].strain.push(d.strain);
    if (d.recovery_score != null) weeks[w].rec.push(d.recovery_score);
    if (d.hrv_rmssd_ms != null) weeks[w].hrv.push(d.hrv_rmssd_ms);
    if (d.resting_hr != null) weeks[w].rhr.push(d.resting_hr);
    if (d.sleep_performance_pct != null) weeks[w].sleep.push(d.sleep_performance_pct);
    weeks[w].wkts += d.workout_count || 0;
  }});

  const keys  = Object.keys(weeks).sort();
  const wkArr = keys.map(k => ({{
    week: k,
    avgStrain: avg(weeks[k].strain),
    avgRec:    avg(weeks[k].rec),
    avgHrv:    avg(weeks[k].hrv),
    avgRhr:    avg(weeks[k].rhr),
    avgSleep:  avg(weeks[k].sleep),
    wkts:      weeks[k].wkts,
  }}));

  // chart: weekly avg strain bars
  const maxStr = Math.max(...wkArr.map(w=>w.avgStrain||0), 1);
  const W = 900, H = 100, padL = 42, padB = 24, padT = 16;
  const bw = Math.max(2, (W - padL) / wkArr.length - 1);
  const chartH = H - padT - padB;
  const step = Math.max(1, Math.floor(wkArr.length / 10));
  const barsStr = wkArr.map((w, i) => {{
    const bh = ((w.avgStrain||0) / maxStr * chartH);
    const x = padL + i / wkArr.length * (W - padL);
    const y = padT + chartH - bh;
    const lbl = i % step === 0 ? `<text x="${{x.toFixed(1)}}" y="${{H-4}}" font-size="9" fill="var(--muted)">${{w.week.slice(5)}}</text>` : '';
    return `<rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{bw.toFixed(1)}}" height="${{bh.toFixed(1)}}"
                  fill="var(--yellow)" opacity="0.8" rx="2"/>${{lbl}}`;
  }}).join('');
  document.getElementById('chart-weekly-strain').innerHTML =
    `<svg viewBox="0 0 ${{W}} ${{H}}" style="width:100%;height:${{H}}px">${{barsStr}}</svg>`;

  // table
  document.getElementById('weekly-tbody').innerHTML = [...wkArr].reverse().map(w =>
    `<tr>
      <td>${{w.week}}</td>
      <td class="num">${{fmtN1(w.avgStrain)}}</td>
      <td class="num ${{w.avgRec!=null ? recClass(w.avgRec) : ''}}">${{fmtN0(w.avgRec)}}</td>
      <td class="num">${{fmtN1(w.avgHrv)}}</td>
      <td class="num">${{fmtN0(w.avgRhr)}}</td>
      <td class="num">${{fmtN0(w.avgSleep)}}</td>
      <td class="num">${{w.wkts}}</td>
    </tr>`
  ).join('');
}}

function renderSleep() {{
  const labels = filtered.map(d => d.date);

  document.getElementById('chart-sleep-perf').innerHTML = sparkline(
    rolling(filtered, 'sleep_performance_pct', 7), labels,
    {{id:'spp', color:'var(--purple)', fmt: v => v.toFixed(0), h:110}});

  document.getElementById('chart-sleep-tib').innerHTML = sparkline(
    filtered.map(d => d.sleep_total_in_bed_min != null ? d.sleep_total_in_bed_min / 60 : null),
    labels, {{id:'tib', color:'var(--blue)', fmt: v => v.toFixed(1), h:110}});

  // Stacked stage bars by week
  const weeks = {{}};
  filtered.forEach(d => {{
    const w = isoWeek(d.date);
    if (!weeks[w]) weeks[w] = {{week:w, light:[], rem:[], sws:[], awake:[]}};
    if (d.sleep_light_min != null) weeks[w].light.push(d.sleep_light_min);
    if (d.sleep_rem_min   != null) weeks[w].rem.push(d.sleep_rem_min);
    if (d.sleep_sws_min   != null) weeks[w].sws.push(d.sleep_sws_min);
    if (d.sleep_awake_min != null) weeks[w].awake.push(d.sleep_awake_min);
  }});
  const wkArr = Object.keys(weeks).sort().map(k => ({{
    week:  k,
    light: avg(weeks[k].light) != null ? avg(weeks[k].light)/60 : 0,
    rem:   avg(weeks[k].rem)   != null ? avg(weeks[k].rem)/60   : 0,
    sws:   avg(weeks[k].sws)   != null ? avg(weeks[k].sws)/60   : 0,
    awake: avg(weeks[k].awake) != null ? avg(weeks[k].awake)/60 : 0,
  }}));
  document.getElementById('chart-sleep-stages').innerHTML = stackedBarChart(
    wkArr, ['light','rem','sws','awake'],
    ['var(--blue)','var(--purple)','var(--accent)','var(--border)'],
    ['Light','REM','SWS','Awake']
  );

  // Monthly table
  const months = {{}};
  filtered.forEach(d => {{
    const m = isoMonth(d.date);
    if (!months[m]) months[m] = {{perf:[],tib:[],light:[],rem:[],sws:[],resp:[],dist:[]}};
    if (d.sleep_performance_pct   != null) months[m].perf.push(d.sleep_performance_pct);
    if (d.sleep_total_in_bed_min  != null) months[m].tib.push(d.sleep_total_in_bed_min);
    if (d.sleep_light_min         != null) months[m].light.push(d.sleep_light_min);
    if (d.sleep_rem_min           != null) months[m].rem.push(d.sleep_rem_min);
    if (d.sleep_sws_min           != null) months[m].sws.push(d.sleep_sws_min);
    if (d.respiratory_rate        != null) months[m].resp.push(d.respiratory_rate);
    if (d.sleep_disturbances      != null) months[m].dist.push(d.sleep_disturbances);
  }});
  document.getElementById('sleep-monthly-tbody').innerHTML =
    Object.keys(months).sort().reverse().map(m => {{
      const mo = months[m];
      return `<tr>
        <td>${{m}}</td>
        <td class="num">${{fmtN0(avg(mo.perf))}}</td>
        <td class="num">${{fmtMin(avg(mo.tib))}}</td>
        <td class="num">${{fmtMin(avg(mo.light))}}</td>
        <td class="num">${{fmtMin(avg(mo.rem))}}</td>
        <td class="num">${{fmtMin(avg(mo.sws))}}</td>
        <td class="num">${{avg(mo.resp)!=null ? avg(mo.resp).toFixed(2) : '—'}}</td>
        <td class="num">${{fmtN0(avg(mo.dist))}}</td>
      </tr>`;
    }}).join('');
}}

function renderWorkouts() {{
  // Sport counts bar chart
  const counts = {{}};
  filtWorkouts.forEach(w => {{ counts[w.sport_name||'unknown'] = (counts[w.sport_name||'unknown']||0)+1; }});
  const sorted = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,15);
  document.getElementById('chart-sport-counts').innerHTML =
    hbarChart(sorted.map(e=>e[0]), sorted.map(e=>e[1]), 'var(--accent)');

  // Table
  const mul = wktSortDir;
  const sortedWkts = [...filtWorkouts].sort((a,b) => {{
    const av = a[wktSortCol], bv = b[wktSortCol];
    if (av==null && bv==null) return 0;
    if (av==null) return 1; if (bv==null) return -1;
    return av < bv ? -mul : av > bv ? mul : 0;
  }});
  const total = sortedWkts.length;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  wktPage = Math.min(wktPage, pages);
  const slice = sortedWkts.slice((wktPage-1)*PAGE, wktPage*PAGE);

  document.querySelectorAll('#wkt-table thead th[data-col]').forEach(th => {{
    th.classList.remove('sorted-asc','sorted-desc');
    if (th.dataset.col === wktSortCol)
      th.classList.add(wktSortDir===1?'sorted-asc':'sorted-desc');
  }});

  const zFmt = ms => ms == null ? '—' : Math.round(ms/60000)+'m';
  document.getElementById('wkt-tbody').innerHTML = slice.map(w => `<tr>
    <td>${{(w.start||'').slice(0,10)}}</td>
    <td><span class="badge">${{w.sport_name||'—'}}</span></td>
    <td class="num">${{fmtN1(w.strain)}}</td>
    <td class="num">${{fmtN0(w.avg_hr)}}</td>
    <td class="num">${{fmtN0(w.max_hr)}}</td>
    <td class="num">${{fmtN0(w.kilojoules)}}</td>
    <td class="num">${{fmtKm(w.distance_meter)}}</td>
    <td class="num">${{zFmt(w.zone_one_ms)}}</td>
    <td class="num">${{zFmt(w.zone_two_ms)}}</td>
    <td class="num">${{zFmt(w.zone_three_ms)}}</td>
    <td class="num">${{zFmt(w.zone_four_ms)}}</td>
    <td class="num">${{zFmt(w.zone_five_ms)}}</td>
  </tr>`).join('');

  renderPagination('wkt-pagination', wktPage, pages, total, p => {{ wktPage=p; renderWorkouts(); }});
}}

function renderDailyLog() {{
  const mul = dailySortDir;
  const sortedDaily = [...filtered].sort((a,b) => {{
    const av = a[dailySortCol], bv = b[dailySortCol];
    if (av==null && bv==null) return 0;
    if (av==null) return 1; if (bv==null) return -1;
    return av < bv ? -mul : av > bv ? mul : 0;
  }});
  const total = sortedDaily.length;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  dailyPage = Math.min(dailyPage, pages);
  const slice = sortedDaily.slice((dailyPage-1)*PAGE, dailyPage*PAGE);

  document.querySelectorAll('#daily-table thead th[data-col]').forEach(th => {{
    th.classList.remove('sorted-asc','sorted-desc');
    if (th.dataset.col === dailySortCol)
      th.classList.add(dailySortDir===1?'sorted-asc':'sorted-desc');
  }});

  document.getElementById('daily-tbody').innerHTML = slice.map(d => `<tr>
    <td>${{d.date}}</td>
    <td class="num">${{fmtN1(d.strain)}}</td>
    <td class="num ${{d.recovery_score!=null?recClass(d.recovery_score):''}}">
      ${{d.recovery_score!=null ? Math.round(d.recovery_score)+'%' : '—'}}
    </td>
    <td class="num">${{fmtN1(d.hrv_rmssd_ms)}}</td>
    <td class="num">${{fmtN0(d.resting_hr)}}</td>
    <td class="num">${{d.respiratory_rate!=null ? d.respiratory_rate.toFixed(2) : '—'}}</td>
    <td class="num">${{d.spo2_pct!=null ? d.spo2_pct.toFixed(1)+'%' : '—'}}</td>
    <td class="num">${{d.sleep_performance_pct!=null ? Math.round(d.sleep_performance_pct)+'%' : '—'}}</td>
    <td class="num">${{fmtMin(d.sleep_total_in_bed_min)}}</td>
    <td class="num">${{fmtMin(d.sleep_rem_min)}}</td>
    <td class="num">${{fmtMin(d.sleep_sws_min)}}</td>
    <td class="num">${{d.workout_count||'—'}}</td>
  </tr>`).join('');

  renderPagination('daily-pagination', dailyPage, pages, total, p => {{ dailyPage=p; renderDailyLog(); }});
}}

function renderPagination(id, page, pages, total, cb) {{
  const el = document.getElementById(id);
  if (pages <= 1) {{ el.innerHTML=''; return; }}
  const btns = [`<span class="info">Page ${{page}} of ${{pages}} (${{total.toLocaleString()}} rows)</span>`];
  if (page > 1) btns.push(`<button onclick="((${{cb}})(${{page-1}}))">‹ Prev</button>`);
  const lo = Math.max(1,page-3), hi = Math.min(pages,page+3);
  for (let p=lo;p<=hi;p++) btns.push(`<button class="${{p===page?'active':''}}" onclick="((${{cb}})(${{p}}))">${{p}}</button>`);
  if (page < pages) btns.push(`<button onclick="((${{cb}})(${{page+1}}))">Next ›</button>`);
  el.innerHTML = btns.join('');
}}

// ── SORT HEADERS ──────────────────────────────────────────────────────────
document.querySelectorAll('#wkt-table thead th[data-col]').forEach(th => {{
  th.addEventListener('click', () => {{
    if (wktSortCol === th.dataset.col) wktSortDir *= -1;
    else {{ wktSortCol = th.dataset.col; wktSortDir = -1; }}
    wktPage = 1; renderWorkouts();
  }});
}});
document.querySelectorAll('#daily-table thead th[data-col]').forEach(th => {{
  th.addEventListener('click', () => {{
    if (dailySortCol === th.dataset.col) dailySortDir *= -1;
    else {{ dailySortCol = th.dataset.col; dailySortDir = -1; }}
    dailyPage = 1; renderDailyLog();
  }});
}});

// ── TABS ──────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {{
  tab.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('panel-'+tab.dataset.tab).classList.add('active');
  }});
}});

// ── RENDER ────────────────────────────────────────────────────────────────
function render() {{
  renderCards();
  renderTrends();
  renderWeekly();
  renderSleep();
  renderWorkouts();
  renderDailyLog();
}}

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

    html = HTML_TEMPLATE.format(data_js=data_js)
    Path(out_path).write_text(html, encoding="utf-8")
    size = Path(out_path).stat().st_size
    print(f"Written {out_path}  ({size/1024/1024:.2f} MB, "
          f"{len(data['daily'])} days, {len(data['workouts'])} workouts)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate index.html from whoop.db")
    ap.add_argument("--db",  default="whoop.db",  help="SQLite DB path")
    ap.add_argument("--out", default="index.html", help="Output HTML path")
    args = ap.parse_args()
    build(args.db, args.out)


if __name__ == "__main__":
    main()
