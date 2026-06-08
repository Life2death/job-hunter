"""
web_app.py
Flask web service for interactive job report.
Serves the ranked job table with click-to-apply tracking.
Deployed on Render free tier. Reads/writes to Supabase.
"""

import os, sys
from datetime import date, timedelta

sys.stdout = sys.stderr  # no encoding issues on Render
TODAY = date.today()

from flask import Flask, jsonify, request
app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
_cloud = None

def get_cloud():
    global _cloud
    if _cloud is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            return None
        try:
            from supabase import create_client
            _cloud = create_client(SUPABASE_URL, SUPABASE_KEY)
        except ImportError:
            return None
    return _cloud


TABS_HTML = """<div class="tabs">
  <a class="tab" href="/">Dashboard</a>
  <a class="tab tab-active" href="/jobs">Job Queue</a>
</div>"""

BASE_CSS = """<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font: 14px/1.5 system-ui, sans-serif; background: #f5f5f5; padding: 20px; }
  h1 { margin-bottom: 12px; }
  .tabs { display: flex; gap: 0; margin-bottom: 16px; border-bottom: 2px solid #1565c0; }
  .tab { padding: 8px 20px; text-decoration: none; color: #555; background: #eee;
         border-radius: 4px 4px 0 0; font-weight: 600; margin-right: 4px; }
  .tab:hover { background: #ddd; }
  .tab-active { background: #1565c0; color: #fff; }
  .tab-active:hover { background: #0d47a1; }
</style>"""


def generate_html(track="", portal="", status="", min_fit=0, applied_date=""):
    qs_parts = []
    if track: qs_parts.append(f"track={track}")
    if portal: qs_parts.append(f"portal={portal}")
    if status: qs_parts.append(f"status={status}")
    if min_fit: qs_parts.append(f"min_fit={min_fit}")
    if applied_date: qs_parts.append(f"applied_date={applied_date}")
    qs = "&".join(qs_parts)
    api_url = f"/api/jobs?{qs}" if qs else "/api/jobs"

    hint = ""
    if applied_date:
        hint = f"""<div style="background:#e3f2fd;padding:8px 14px;border-radius:4px;margin-bottom:12px;font-size:13px;">
  Showing jobs applied on <strong>{applied_date}</strong>
  <a href="/jobs" style="margin-left:12px;color:#1565c0;">Clear filter</a>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Queue - Multi Portal</title>
<script src="https://cdn.jsdelivr.net/npm/ag-grid-community@30.2.1/dist/ag-grid-community.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@30.2.1/styles/ag-grid.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@30.2.1/styles/ag-theme-alpine.min.css">
{BASE_CSS}
<style>
  #jobGrid {{ height: calc(100vh - 120px); width: 100%; }}
  .ag-theme-alpine {{ --ag-font-size: 13px; }}
  .fit-high {{ color: #1a7d1a !important; font-weight: 700 !important; }}
  .fit-mid  {{ color: #b8860b !important; }}
  .fit-low  {{ color: #999 !important; }}
  .status-not_applied {{ color: #d32f2f !important; }}
  .status-applied {{ color: #1a7d1a !important; }}
  .status-skipped {{ color: #999 !important; }}
  .status-not_interested {{ color: #999 !important; }}
</style>
</head>
<body>
{TABS_HTML}
<h1>Job Queue</h1>
{hint}
<div id="jobGrid" class="ag-theme-alpine"></div>
<script>
var gridOptions = {{
  columnDefs: [
    {{ field: 'fit', width: 60, sortable: true, filter: 'agNumberColumnFilter',
       cellClassRules: {{ 'fit-high': p => p.value >= 60, 'fit-mid': p => p.value >= 40 }} }},
    {{ field: 'freshness', width: 90, sortable: true, filter: 'agSetColumnFilter' }},
    {{ field: 'imported_date', width: 110, sortable: true, filter: 'agSetColumnFilter', headerName: 'Sourced' }},
    {{ field: 'track', width: 80, sortable: true, filter: 'agSetColumnFilter' }},
    {{ field: 'portal', width: 100, sortable: true, filter: 'agSetColumnFilter' }},
    {{ field: 'title', flex: 1, sortable: true, filter: 'agTextColumnFilter', minWidth: 200 }},
    {{ field: 'company', width: 180, sortable: true, filter: 'agTextColumnFilter' }},
    {{ field: 'location', width: 200, sortable: true, filter: 'agTextColumnFilter' }},
    {{ field: 'status', width: 150, sortable: true, filter: 'agSetColumnFilter',
       cellClassRules: {{ 'status-applied': p => p.data.status === 'applied' }},
       valueGetter: function(p) {{
         if (p.data.status === 'applied' && p.data.applied_date) return 'applied ' + p.data.applied_date;
         return p.data.status || 'not_applied';
       }}
    }},
    {{ headerName: 'Flags', width: 160, filter: 'agTextColumnFilter',
       valueGetter: function(p) {{
         try {{ var sj = JSON.parse(p.data.scores_json || '{{}}'); return (sj.f || []).join(', '); }} catch(e) {{ return ''; }}
       }}
    }},
    {{ headerName: 'URL', field: 'url', width: 300, sortable: false, filter: false,
       cellRenderer: function(params) {{
         var a = document.createElement('a');
         a.href = params.value || '#';
         a.target = '_blank';
         a.textContent = (params.value || '').slice(0, 60) + ((params.value || '').length > 60 ? '...' : '');
         a.className = 'job-link';
         a.addEventListener('click', function(e) {{
           e.preventDefault();
           fetch('/apply/' + encodeURIComponent(params.data.job_id), {{ method: 'POST' }})
             .then(function() {{ window.open(params.value, '_blank'); }})
             .catch(function() {{ window.open(params.value, '_blank'); }});
         }});
         return a;
       }}
    }}
  ],
  defaultColDef: {{ resizable: true }},
  rowData: null,
  postSortRows: function(params) {{
    var rows = params.nodes;
    rows.sort(function(a, b) {{
      var aApplied = a.data.status === 'applied' ? 1 : 0;
      var bApplied = b.data.status === 'applied' ? 1 : 0;
      if (aApplied !== bApplied) return aApplied - bApplied;
      return b.data.fit - a.data.fit;
    }});
  }},
  pagination: true,
  paginationPageSize: 200,
  paginationPageSizeSelector: [100, 200, 500],
  animateRows: true,
  enableCellTextSelection: true,
  ensureDomOrder: true,
}};

var gridDiv = document.getElementById('jobGrid');
new agGrid.Grid(gridDiv, gridOptions);

fetch('{api_url}')
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{ gridOptions.api.setRowData(data); }})
  .catch(function(e) {{ console.error('Failed to load jobs', e); }});
</script>
</body>
</html>"""


def generate_dashboard_html():
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard - Multi Portal</title>
{BASE_CSS}
<style>
  .cards {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .card {{ flex: 1; min-width: 180px; background: #fff; border-radius: 8px; padding: 20px;
           box-shadow: 0 1px 4px rgba(0,0,0,.1); text-align: center; }}
    .card-count {{ font-size: 36px; font-weight: 700; }}
    .card-count a {{ color: #1565c0; text-decoration: none; }}
    .card-count a:hover {{ text-decoration: underline; }}
  .card-label {{ font-size: 13px; color: #666; margin-top: 4px; }}
  .section {{ background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px;
              box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
  .section h2 {{ font-size: 16px; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ font-size: 12px; text-transform: uppercase; color: #666; }}
  .week-grid {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; text-align: center; }}
  .week-day {{ padding: 8px; border-radius: 6px; background: #f0f4ff; }}
  .week-day-name {{ font-size: 11px; text-transform: uppercase; color: #666; }}
  .week-day-num {{ font-size: 24px; font-weight: 700; color: #1565c0; }}
</style>
</head>
<body>
{TABS_HTML}
<h1>Dashboard</h1>
<div id="app">
  <div class="cards">
    <div class="card"><div class="card-count" id="countToday">-</div><div class="card-label">Applied Today</div></div>
    <div class="card"><div class="card-count" id="countYesterday">-</div><div class="card-label">Applied Yesterday</div></div>
  </div>
  <div class="section">
    <h2>This Week (Sun &ndash; Sat)</h2>
    <div class="week-grid" id="weekGrid"></div>
  </div>
  <div class="section">
    <h2>Companies Applied Per Day</h2>
    <table><thead><tr><th>Date</th><th>Companies</th></tr></thead>
    <tbody id="companiesBody"></tbody></table>
  </div>
</div>
<script>
fetch('/api/jobs/stats')
  .then(function(r) {{ return r.json(); }})
  .then(function(stats) {{
    document.getElementById('countToday').innerHTML =
      '<a href="/jobs?status=applied&applied_date=' + stats.today_date + '" target="_blank">' + stats.today + '</a>';
    document.getElementById('countYesterday').innerHTML =
      '<a href="/jobs?status=applied&applied_date=' + stats.yesterday_date + '" target="_blank">' + stats.yesterday + '</a>';

    var weekHtml = '';
    var dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    stats.week.forEach(function(d, i) {{
      weekHtml += '<div class="week-day"><div class="week-day-name">' + dayNames[i] + '</div>'
               + '<div class="week-day-num"><a href="/jobs?status=applied&applied_date=' + d.date + '" target="_blank">'
               + d.count + '</a></div></div>';
    }});
    document.getElementById('weekGrid').innerHTML = weekHtml;

    var companiesHtml = '';
    stats.companies_per_day.forEach(function(d) {{
      companiesHtml += '<tr><td><a href="/jobs?status=applied&applied_date=' + d.date + '" target="_blank">'
                    + d.date + '</a></td><td>' + d.companies.join(', ') + '</td></tr>';
    }});
    document.getElementById('companiesBody').innerHTML = companiesHtml;
  }})
  .catch(function(e) {{ console.error('Failed to load stats', e); }});
</script>
</body>
</html>"""


@app.route("/api/jobs")
def api_jobs():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500

    track = request.args.get("track")
    portal = request.args.get("portal")
    status = request.args.get("status")
    min_fit = request.args.get("min_fit", 0, type=int)
    applied_date = request.args.get("applied_date")

    query = cloud.table("job_listings").select("*")
    if track:
        query = query.eq("track", track)
    if portal:
        query = query.eq("portal", portal)
    if status:
        query = query.eq("status", status)
    if applied_date:
        query = query.eq("applied_date", applied_date)
    query = query.gte("fit", min_fit).order("fit", desc=True)

    result = query.execute()
    return jsonify(result.data if result else [])


@app.route("/api/jobs/stats")
def api_jobs_stats():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500

    today_str = str(date.today())
    yesterday_str = str(date.today() - timedelta(days=1))

    result = cloud.table("job_listings").select("applied_date, company, status").eq("status", "applied").execute()
    jobs = result.data if result else []

    today_count = sum(1 for j in jobs if j.get("applied_date") == today_str)
    yesterday_count = sum(1 for j in jobs if j.get("applied_date") == yesterday_str)

    days_since_sunday = (date.today().weekday() + 1) % 7
    sunday = date.today() - timedelta(days=days_since_sunday)
    week = []
    for i in range(7):
        d = sunday + timedelta(days=i)
        week.append({"date": str(d), "count": sum(1 for j in jobs if j.get("applied_date") == str(d))})

    companies_by_date = {}
    for j in jobs:
        ad = j.get("applied_date", "")
        if ad and ad >= str(date.today() - timedelta(days=14)):
            companies_by_date.setdefault(ad, set()).add(j.get("company", ""))

    companies_per_day = [
        {"date": d, "companies": sorted(c)}
        for d, c in sorted(companies_by_date.items(), reverse=True)
    ]

    return jsonify({
        "today": today_count,
        "today_date": today_str,
        "yesterday": yesterday_count,
        "yesterday_date": yesterday_str,
        "week": week,
        "companies_per_day": companies_per_day,
    })


@app.route("/")
def dashboard():
    html = generate_dashboard_html()
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/jobs")
def report():
    cloud = get_cloud()
    if not cloud:
        return "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY.", 500

    track = request.args.get("track", "")
    portal = request.args.get("portal", "")
    status = request.args.get("status", "")
    min_fit = request.args.get("min_fit", 0, type=int)
    applied_date = request.args.get("applied_date", "")

    html = generate_html(track=track, portal=portal, status=status, min_fit=min_fit, applied_date=applied_date)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/apply/<job_id>", methods=["POST"])
def apply(job_id):
    cloud = get_cloud()
    if not cloud:
        return jsonify({"ok": False, "error": "No Supabase"}), 500

    today = str(date.today())
    result = cloud.table("job_listings").update({
        "status": "applied",
        "applied_date": today,
    }).eq("job_id", job_id).execute()

    if result.data:
        return jsonify({"ok": True, "job_id": job_id})
    return jsonify({"ok": False}), 404


@app.route("/status")
def status_summary():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "No Supabase"}), 500

    result = cloud.table("job_listings").select("track, portal, status, count").execute()
    return jsonify(result.data or [])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
