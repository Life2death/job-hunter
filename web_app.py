"""
web_app.py
Flask web service for interactive job report with multi-user auth.
Serves dashboard + AG Grid job table with click-to-apply tracking.
Users sign up with email/password, admin approves access.
"""
import os, sys
from datetime import date, timedelta
sys.stdout = sys.stderr
TODAY = date.today()

import requests as http_requests
from flask import Flask, jsonify, request, session, redirect
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

_cloud = None
_admin_cloud = None

def get_cloud():
    global _cloud
    if _cloud is None and SUPABASE_URL and SUPABASE_KEY:
        try:
            from supabase import create_client
            _cloud = create_client(SUPABASE_URL, SUPABASE_KEY)
        except ImportError:
            pass
    return _cloud

def get_admin_cloud():
    global _admin_cloud
    if _admin_cloud is None and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            from supabase import create_client
            _admin_cloud = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        except ImportError:
            pass
    return _admin_cloud

def uid():
    return session.get("email", "")

def _fetch_all(cloud, user_id, select_cols, status_filter=None):
    page_size = 1000
    all_rows = []
    off = 0
    while True:
        q = cloud.table("job_listings").select(select_cols)
        if user_id is not None:
            q = q.eq("user_id", user_id)
        if status_filter is not None:
            if isinstance(status_filter, list):
                q = q.in_("status", status_filter)
            else:
                q = q.eq("status", status_filter)
        batch = q.range(off, off + page_size - 1).execute()
        data = batch.data or []
        if not data:
            break
        all_rows.extend(data)
        if len(data) < page_size:
            break
        off += page_size
    return all_rows


def _fetch_count(cloud, user_id, status_filter=None):
    return len(_fetch_all(cloud, user_id, "job_id", status_filter))


def auto_confirm_email(user_id):
    if not SUPABASE_SERVICE_KEY or not SUPABASE_URL:
        return False
    try:
        headers = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        }
        url = f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}"
        r = http_requests.put(url, json={"email_confirm": True}, headers=headers, timeout=10)
        return r.ok
    except Exception:
        return False

PUBLIC_ENDPOINTS = ["login", "signup"]

@app.before_request
def check_auth():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if "user_id" not in session:
        return redirect("/login")

AUTH_CSS = """<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font: 14px/1.5 system-ui, sans-serif; background: #f5f5f5; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; }
  .auth-box { background: #fff; padding: 32px; border-radius: 8px; box-shadow: 0 1px 8px rgba(0,0,0,.1);
              width: 360px; }
  .auth-box h1 { font-size: 20px; margin-bottom: 20px; text-align: center; }
  .auth-box label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 4px; }
  .auth-box input { width: 100%; padding: 8px 10px; border: 1px solid #ccc; border-radius: 4px;
                    margin-bottom: 14px; font-size: 14px; }
  .auth-box button { width: 100%; padding: 10px; background: #1565c0; color: #fff; border: none;
                     border-radius: 4px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .auth-box button:hover { background: #0d47a1; }
  .auth-box .link { text-align: center; margin-top: 14px; font-size: 13px; }
  .auth-box .error { color: #d32f2f; font-size: 13px; margin-bottom: 10px; text-align: center; }
</style>"""

LOGIN_FORM = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Login - Job Hunter</title>""" + AUTH_CSS + """</head><body>
<div class="auth-box">
  <h1>Job Hunter</h1>
  <p id="error" class="error"></p>
  <form method="POST" onsubmit="return validate()">
    <label for="email">Email</label>
    <input id="email" name="email" type="email" required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" required>
    <button type="submit">Log In</button>
  </form>
  <div class="link">Don't have an account? <a href="/signup">Sign up</a></div>
</div>
<script>
function validate() {
  var e = document.getElementById('email').value.trim();
  var p = document.getElementById('password').value;
  if (!e || !p) { document.getElementById('error').textContent = 'Email and password required'; return false; }
  return true;
}
</script>
</body></html>"""

SIGNUP_FORM = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Sign Up - Job Hunter</title>""" + AUTH_CSS + """</head><body>
<div class="auth-box">
  <h1>Create Account</h1>
  <p id="error" class="error"></p>
  <form method="POST" onsubmit="return validate()">
    <label for="email">Email</label>
    <input id="email" name="email" type="email" required>
    <label for="password">Password (min 6 chars)</label>
    <input id="password" name="password" type="password" minlength="6" required>
    <button type="submit">Sign Up</button>
  </form>
  <div class="link">Already have an account? <a href="/login">Log in</a></div>
</div>
<script>
function validate() {
  var e = document.getElementById('email').value.trim();
  var p = document.getElementById('password').value;
  if (!e || !p) { document.getElementById('error').textContent = 'Email and password required'; return false; }
  if (p.length < 6) { document.getElementById('error').textContent = 'Password must be at least 6 characters'; return false; }
  return true;
}
</script>
</body></html>"""

PENDING_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Pending Approval - Job Hunter</title>""" + AUTH_CSS + """</head><body>
<div class="auth-box" style="text-align:center">
  <h1>Pending Approval</h1>
  <p style="margin:16px 0;color:#666">Your account is waiting for admin approval.<br>Check back later.</p>
  <a href="/login" style="color:#1565c0;">Back to Login</a>
</div></body></html>"""


def tabs_html(active="dashboard"):
    dash_cls = "tab tab-active" if active == "dashboard" else "tab"
    jobs_cls = "tab tab-active" if active == "jobs" else "tab"
    admin_cls = "tab tab-active" if active == "admin" else "tab"
    admin_link = f'<a class="{admin_cls}" href="/admin">Admin</a>' if session.get("is_admin") else ""
    return f"""<div class="tabs">
  <a class="{dash_cls}" href="/">Dashboard</a>
  <a class="{jobs_cls}" href="/jobs">Job Queue</a>
  {admin_link}
  <span style="margin-left:auto;font-size:12px;color:#999;padding:8px 0">{session.get("email","")} <a href="/logout" style="color:#999;text-decoration:none;">logout</a></span>
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
  .status-manual_apply {{ color: #e65100 !important; font-weight: 600 !important; }}
  .status-skipped {{ color: #999 !important; }}
  .status-not_interested {{ color: #999 !important; }}
</style>
</head>
<body>
{tabs_html("jobs")}
<div style="display:flex;justify-content:space-between;align-items:flex-start;">
  <div>
    <h1>Job Queue</h1>
    {hint}
  </div>
  <div style="text-align:right;flex-shrink:0;">
    <div id="count-badge" style="font-size:13px;color:#666;white-space:nowrap;"></div>
  </div>
</div>
<div id="load-info" style="font-size:12px;color:#999;margin-bottom:8px;text-align:right;"></div>
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
    {{ field: 'status', width: 180, sortable: true, filter: 'agSetColumnFilter',
       cellClassRules: {{
         'status-applied': p => p.data.status === 'applied',
         'status-manual_apply': p => p.data.status === 'manual_apply'
       }},
       cellRenderer: function(params) {{
         var sel = document.createElement('select');
         sel.style.width = '100%';
         sel.style.border = 'none';
         sel.style.background = 'transparent';
         sel.style.font = 'inherit';
         sel.style.cursor = 'pointer';
         sel.style.outline = 'none';
         var opts = ['not_applied', 'applied', 'manual_apply', 'skipped', 'not_interested'];
         opts.forEach(function(v) {{
           var o = document.createElement('option');
           o.value = v;
           if (v === 'applied' && params.data.applied_date) o.textContent = 'applied ' + params.data.applied_date;
           else if (v === 'manual_apply') o.textContent = 'manual apply';
           else o.textContent = v.replace(/_/g, ' ');
           if (params.value === v) o.selected = true;
           sel.appendChild(o);
         }});
         sel.addEventListener('change', function(e) {{
           e.stopPropagation();
           var ns = sel.value;
           fetch('/api/jobs/' + encodeURIComponent(params.data.job_id) + '/status', {{
             method: 'POST',
             headers: {{'Content-Type': 'application/json'}},
             body: JSON.stringify({{status: ns}})
           }}).then(function(r) {{
             if (r.ok) {{
               var nd = Object.assign({{}}, params.data);
               nd.status = ns;
               if (ns === 'applied') nd.applied_date = new Date().toISOString().slice(0, 10);
               params.node.setData(nd);
             }}
           }}).catch(function() {{}});
         }});
         return sel;
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
            var ___d = params.data;
            fetch('/apply/' + encodeURIComponent(___d.job_id), {{ method: 'POST' }})
              .then(function(r) {{
                if (r.ok) {{
                  var nd = Object.assign({{}}, ___d);
                  nd.status = 'applied';
                  nd.applied_date = new Date().toISOString().slice(0, 10);
                  params.node.setData(nd);
                }}
                window.open(params.value, '_blank');
              }})
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
      var topStatuses = ['not_applied', 'manual_apply'];
      var aPrio = topStatuses.includes(a.data.status) ? 0 : 1;
      var bPrio = topStatuses.includes(b.data.status) ? 0 : 1;
      if (aPrio !== bPrio) return aPrio - bPrio;
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

// Count badge
fetch('/api/jobs/count')
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    document.getElementById('count-badge').textContent = d.to_apply + ' jobs to apply';
  }})
  .catch(function() {{}});

// Lazy load: first 200 rows instantly, rest in background
var baseUrl = '{api_url}';
var sep = baseUrl.indexOf('?') > -1 ? '&' : '?';
var allRows = [];
var totalJobs = 0;

fetch(baseUrl + sep + 'limit=200&offset=0')
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    allRows = data.rows;
    totalJobs = data.total;
    gridOptions.api.setRowData(allRows);
    if (allRows.length < totalJobs) fetchRemaining();
  }})
  .catch(function(e) {{ console.error('Failed to load jobs', e); }});

function fetchRemaining() {{
  var loaded = allRows.length;
  if (loaded >= totalJobs) {{
    document.getElementById('load-info').textContent = '';
    return;
  }}
  var remaining = totalJobs - loaded;
  document.getElementById('load-info').textContent = 'Loading ' + remaining.toLocaleString() + ' more jobs...';
  var page = Math.min(1000, remaining);
  fetch(baseUrl + sep + 'limit=' + page + '&offset=' + loaded)
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      allRows = allRows.concat(data.rows);
      if (allRows.length >= totalJobs) {{
        document.getElementById('load-info').textContent = '';
        gridOptions.api.setRowData(allRows);
      }} else {{
        fetchRemaining();
      }}
    }})
    .catch(function(e) {{ console.error('Failed to load remaining jobs', e); document.getElementById('load-info').textContent = ''; }});
}}
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
  .week-day-num a {{ color: #1565c0; text-decoration:none; }}
  .week-day-num a:hover {{ text-decoration:underline; }}
  .track-breakdown {{ margin-top:4px; border-top:1px solid #d0d8f0; padding-top:4px; font-size:11px; line-height:1.5; color:#555; }}
</style>
</head>
<body>
{tabs_html("dashboard")}
<h1>Dashboard</h1>
<div id="app">
  <div class="cards">
    <div class="card"><div class="card-count" id="countToday">-</div><div class="card-label">Applied Today</div></div>
    <div class="card"><div class="card-count" id="countYesterday">-</div><div class="card-label">Applied Yesterday</div></div>
    <div class="card"><div class="card-count" id="addedToday">-</div><div class="card-label">Added Today</div></div>
    <div class="card"><div class="card-count" id="addedWeek">-</div><div class="card-label">Added This Week</div></div>
    <div class="card"><div class="card-count" id="addedMonth">-</div><div class="card-label">Added This Month</div></div>
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
    document.getElementById('addedToday').textContent = stats.added.today;
    document.getElementById('addedWeek').textContent = stats.added.week;
    document.getElementById('addedMonth').textContent = stats.added.month;

    var weekHtml = '';
    var dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    stats.week.forEach(function(d, i) {{
      var tracksHtml = '';
      var entries = Object.entries(d.tracks || {{}}).sort(function(a, b) {{ return b[1] - a[1]; }});
      entries.forEach(function(t) {{ tracksHtml += '<div>' + t[0] + ' ' + t[1] + '</div>'; }});
      if (!entries.length) tracksHtml = '<div style="color:#bbb;">&ndash;</div>';
      weekHtml += '<div class="week-day"><div class="week-day-name">' + dayNames[i] + '</div>'
               + '<div class="week-day-num"><a href="/jobs?status=applied&applied_date=' + d.date + '" target="_blank">'
               + d.count + '</a></div>'
               + '<div class="track-breakdown">' + tracksHtml + '</div></div>';
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


# ─── API ────────────────────────────────────────────────

@app.route("/api/jobs")
def api_jobs():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500

    u = uid()
    track = request.args.get("track")
    portal = request.args.get("portal")
    status = request.args.get("status")
    min_fit = request.args.get("min_fit", 0, type=int)
    applied_date = request.args.get("applied_date")

    def filtered_query(sel="*", with_count=False):
        q = cloud.table("job_listings").select(sel, count="exact" if with_count else None)
        q = q.eq("user_id", u)
        if track: q = q.eq("track", track)
        if portal: q = q.eq("portal", portal)
        if status: q = q.eq("status", status)
        if applied_date: q = q.eq("applied_date", applied_date)
        return q.gte("fit", min_fit)

    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", 0, type=int)

    if limit is not None:
        batch = filtered_query().order("fit", desc=True).range(offset, offset + limit - 1).execute()
        cnt = filtered_query("job_id", with_count=True).execute()
        total = getattr(cnt, 'count', 0) or 0
        return jsonify({"rows": batch.data or [], "total": total})

    page_size = 1000
    all_jobs = []
    off = 0
    while True:
        batch = filtered_query().order("fit", desc=True).range(off, off + page_size - 1).execute()
        data = batch.data or []
        if not data:
            break
        all_jobs.extend(data)
        if len(data) < page_size:
            break
        off += page_size
    return jsonify(all_jobs)


@app.route("/api/jobs/count")
def api_jobs_count():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500
    u = uid()
    total = _fetch_count(cloud, u)
    applied = _fetch_count(cloud, u, "applied")
    actionable = _fetch_count(cloud, u, ["not_applied", "manual_apply"])
    return jsonify({"total": total, "applied": applied, "to_apply": actionable})


@app.route("/api/debug/stats")
def api_debug_stats():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500
    try:
        all_rows = _fetch_all(cloud, None, "user_id, imported_date")
        null_dates = sum(1 for j in all_rows if j.get("imported_date") is None)
        uid_counts = {}
        for j in all_rows:
            uid = j.get("user_id") or ""
            uid_counts[uid] = uid_counts.get(uid, 0) + 1
        return jsonify({
            "total_all_users": len(all_rows),
            "null_imported_date": null_dates,
            "uid_counts": uid_counts,
            "session_uid": uid(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _fmt(d):
    """Normalize any date-like value to YYYY-MM-DD string.
    Handles date objects, datetime objects, ISO strings with/without time."""
    if not d:
        return ""
    return str(d)[:10]

@app.route("/api/jobs/stats")
def api_jobs_stats():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500

    u = uid()
    today = date.today()
    today_str = str(today)
    yesterday_str = str(today - timedelta(days=1))

    # Applied jobs with track for breakdown
    applied = _fetch_all(cloud, u, "applied_date, company, track, status", "applied")

    # Normalize applied_date to string so comparisons with == always work
    for j in applied:
        j["applied_date"] = _fmt(j.get("applied_date"))

    today_count = sum(1 for j in applied if j.get("applied_date") == today_str)
    yesterday_count = sum(1 for j in applied if j.get("applied_date") == yesterday_str)

    days_since_sunday = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=days_since_sunday)

    # Track counters per day
    track_by_date = {}
    for j in applied:
        ad = j.get("applied_date", "")
        tr = j.get("track", "?") or "?"
        if ad:
            track_by_date.setdefault(ad, {})
            track_by_date[ad][tr] = track_by_date[ad].get(tr, 0) + 1

    week = []
    for i in range(7):
        d = sunday + timedelta(days=i)
        ds = str(d)
        week.append({
            "date": ds,
            "count": sum(1 for j in applied if j.get("applied_date") == ds),
            "tracks": track_by_date.get(ds, {}),
        })

    companies_by_date = {}
    for j in applied:
        ad = j.get("applied_date", "")
        if ad and ad >= str(today - timedelta(days=14)):
            companies_by_date.setdefault(ad, set()).add(j.get("company", ""))

    companies_per_day = [
        {"date": d, "companies": sorted(c)}
        for d, c in sorted(companies_by_date.items(), reverse=True)
    ]

    # All jobs for added counts (by imported_date)
    all_rows = _fetch_all(cloud, u, "imported_date")
    print(f"STATS DEBUG: uid={u!r}, all_rows={len(all_rows)}", flush=True)
    imported_dates = [_fmt(j.get("imported_date")) for j in all_rows if _fmt(j.get("imported_date"))]
    print(f"STATS DEBUG: imported_dates={len(imported_dates)}, today_str[:7]={today_str[:7]!r}", flush=True)
    if imported_dates:
        print(f"STATS DEBUG: sample dates={imported_dates[:5]}, all_June={all(d.startswith(today_str[:7]) for d in imported_dates)}", flush=True)

    added_today = sum(1 for d in imported_dates if d == today_str)
    added_week = sum(1 for d in imported_dates if str(sunday) <= d <= today_str)
    added_month = sum(1 for d in imported_dates if d.startswith(today_str[:7]))

    return jsonify({
        "today": today_count,
        "today_date": today_str,
        "yesterday": yesterday_count,
        "yesterday_date": yesterday_str,
        "week": week,
        "companies_per_day": companies_per_day,
        "added": {
            "today": added_today,
            "week": added_week,
            "month": added_month,
        },
    })


@app.route("/apply/<job_id>", methods=["POST"])
def apply(job_id):
    cloud = get_cloud()
    if not cloud:
        return jsonify({"ok": False, "error": "No Supabase"}), 500

    u = uid()
    today = str(date.today())
    result = cloud.table("job_listings").update({
        "status": "applied",
        "applied_date": today,
    }).eq("job_id", job_id).eq("user_id", u).execute()

    if result.data:
        return jsonify({"ok": True, "job_id": job_id})
    return jsonify({"ok": False}), 404


@app.route("/api/jobs/<job_id>/status", methods=["POST"])
def update_job_status(job_id):
    cloud = get_cloud()
    if not cloud:
        return jsonify({"ok": False, "error": "No Supabase"}), 500

    data = request.get_json(silent=True)
    if not data or "status" not in data:
        return jsonify({"ok": False, "error": "status required"}), 400

    status = data["status"]
    allowed = {"not_applied", "applied", "manual_apply", "skipped", "not_interested"}
    if status not in allowed:
        return jsonify({"ok": False, "error": f"Invalid status: {status}"}), 400

    u = uid()
    update_data = {"status": status}
    if status == "applied":
        update_data["applied_date"] = str(date.today())
    elif status != "applied":
        update_data["applied_date"] = None

    result = cloud.table("job_listings").update(update_data).eq("job_id", job_id).eq("user_id", u).execute()
    if result.data:
        return jsonify({"ok": True, "job_id": job_id, "status": status})
    return jsonify({"ok": False}), 404


@app.route("/status")
def status_summary():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "No Supabase"}), 500
    result = cloud.table("job_listings").select("track, portal, status, count").eq("user_id", uid()).execute()
    return jsonify(result.data or [])


# ─── Page routes ─────────────────────────────────────────

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


# ─── Auth routes ─────────────────────────────────────────

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return SIGNUP_FORM

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        return "Email and password required", 400
    if len(password) < 6:
        return "Password must be at least 6 characters", 400

    cloud = get_cloud()
    if not cloud:
        return "Supabase not configured", 500

    try:
        result = cloud.auth.sign_up({"email": email, "password": password})
    except Exception as e:
        return f"Signup failed: {str(e)}", 400

    if result.user:
        auto_confirm_email(result.user.id)

    admin = get_admin_cloud()
    client = admin or cloud
    try:
        client.table("profiles").insert({"email": email, "approved": False}).execute()
    except Exception:
        pass

    return """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Signed Up - Job Hunter</title>""" + AUTH_CSS + """</head><body>
<div class="auth-box" style="text-align:center">
  <h1>Account Created</h1>
  <p style="margin:16px 0;color:#666">You'll need admin approval before you can log in.</p>
  <a href="/login" style="color:#1565c0;">Go to Login</a>
</div></body></html>"""


def handle_login(result, email):
    user = result.user
    cloud = get_cloud()

    try:
        profile = cloud.table("profiles").select("approved").eq("email", email).execute()
        approved = profile.data and profile.data[0].get("approved")
    except Exception:
        approved = False

    if not approved:
        if email != ADMIN_EMAIL:
            if cloud:
                try:
                    cloud.auth.sign_out()
                except Exception:
                    pass
            return PENDING_PAGE, 403
        admin = get_admin_cloud()
        if admin:
            try:
                prof = admin.table("profiles").select("*").eq("email", email).execute()
                if prof.data:
                    admin.table("profiles").update({"approved": True}).eq("email", email).execute()
                else:
                    admin.table("profiles").insert({"email": email, "approved": True}).execute()
            except Exception:
                pass
        approved = True

    session["user_id"] = user.id
    session["email"] = email
    session["is_admin"] = email == ADMIN_EMAIL
    return redirect("/")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if "user_id" in session:
            return redirect("/")
        return LOGIN_FORM

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password", "")

    cloud = get_cloud()
    if not cloud:
        return "Supabase not configured", 500

    try:
        result = cloud.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        err = str(e)
        if "Email not confirmed" in err:
            # Try to find and auto-confirm the user's email
            admin = get_admin_cloud()
            if admin:
                try:
                    users_resp = admin.table("profiles").select("id").eq("email", email).execute()
                    if not users_resp.data:
                        # Try getting user from auth admin API
                        headers = {
                            "apikey": SUPABASE_SERVICE_KEY,
                            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        }
                        url = f"{SUPABASE_URL}/auth/v1/admin/users"
                        resp = http_requests.get(url, headers=headers, params={"filter": email}, timeout=10)
                        if resp.ok:
                            users = resp.json().get("users", [])
                            for u in users:
                                if u.get("email") == email:
                                    auto_confirm_email(u["id"])
                                    break
                except Exception:
                    pass
                try:
                    result = cloud.auth.sign_in_with_password({"email": email, "password": password})
                    return handle_login(result, email)
                except Exception:
                    pass
            return """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Email Not Confirmed - Job Hunter</title>""" + AUTH_CSS + """</head><body>
<div class="auth-box" style="text-align:center">
  <h1>Email Not Confirmed</h1>
  <p style="margin:16px 0;color:#666">Set <strong>SUPABASE_SERVICE_KEY</strong> in Render env vars to enable auto-confirm,<br>
  or disable 'Confirm email' in Supabase Auth settings.</p>
  <a href="/login" style="color:#1565c0;">Back to Login</a>
</div></body></html>""", 403
        return f"Login failed: {str(e)}", 401

    return handle_login(result, email)


@app.route("/logout")
def logout():
    cloud = get_cloud()
    if cloud:
        try:
            cloud.auth.sign_out()
        except Exception:
            pass
    session.clear()
    return redirect("/login")


# ─── Admin routes ────────────────────────────────────────

def admin_html(pending, approved):
    pending_rows = ""
    for p in (pending or []):
        pending_rows += f"""<tr>
  <td>{p.get("email","")}</td>
  <td>{str(p.get("created_at",""))[:19]}</td>
  <td><form method="POST" action="/admin/approve/{p.get("email","")}" style="display:inline"><button class="btn-approve">Approve</button></form></td>
</tr>"""

    approved_rows = ""
    for p in (approved or []):
        approved_rows += f"<tr><td>{p.get('email','')}</td><td>{str(p.get('created_at',''))[:19]}</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Admin - Job Hunter</title>
<style>
  * {{ box-sizing:border-box;margin:0;padding:0; }}
  body {{ font:14px/1.5 system-ui,sans-serif; background:#f5f5f5; padding:20px; }}
  .tabs {{ display:flex; gap:4px; background:#fff; border-radius:8px 8px 0 0; padding:8px 12px 0; box-shadow:0 1px 4px rgba(0,0,0,.1); }}
  .tab {{ padding:8px 20px; text-decoration:none; color:#555; background:#eee; border-radius:6px 6px 0 0; font-size:13px; }}
  .tab:hover {{ background:#ddd; }}
  .tab-active {{ background:#1565c0; color:#fff; }}
  .tab-active:hover {{ background:#0d47a1; }}
  h1 {{ margin-bottom:16px; }}
  .section {{ background:#fff; border-radius:8px; padding:16px 20px; margin-bottom:16px; box-shadow:0 1px 4px rgba(0,0,0,.1); }}
  .section h2 {{ font-size:16px; margin-bottom:12px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th,td {{ padding:8px 10px; text-align:left; border-bottom:1px solid #eee; }}
  th {{ font-size:12px; text-transform:uppercase; color:#666; }}
  .btn-approve {{ padding:4px 14px; background:#1a7d1a; color:#fff; border:none; border-radius:4px; cursor:pointer; font-size:13px; }}
  .btn-approve:hover {{ background:#145214; }}
  .empty {{ color:#999; font-size:13px; padding:12px 0; }}
  a {{ color:#1565c0; }}
</style></head><body>
{tabs_html("admin")}
<div class="section">
  <h2>Pending Approval</h2>
  <table><thead><tr><th>Email</th><th>Signed Up</th><th>Action</th></tr></thead>
  <tbody>{"<tr><td colspan=3 class=empty>No pending users</td></tr>" if not pending_rows else pending_rows}</tbody></table>
</div>
<div class="section">
  <h2>Approved Users</h2>
  <table><thead><tr><th>Email</th><th>Approved</th></tr></thead>
  <tbody>{"<tr><td colspan=2 class=empty>No approved users</td></tr>" if not approved_rows else approved_rows}</tbody></table>
</div>
</body></html>"""


@app.route("/admin")
def admin_panel():
    if not session.get("is_admin"):
        return "Unauthorized", 403

    admin = get_admin_cloud()
    client = admin or get_cloud()
    if not client:
        return "Supabase not configured", 500

    import traceback
    try:
        pending_resp = client.table("profiles").select("*").eq("approved", False).order("created_at").execute()
        approved_resp = client.table("profiles").select("*").eq("approved", True).order("created_at").limit(50).execute()
    except Exception as e:
        return f"DB Error: {e}", 500

    try:
        html = admin_html(pending_resp.data if pending_resp else [], approved_resp.data if approved_resp else [])
    except Exception as e:
        return f"Template Error: {e}\n{traceback.format_exc()}", 500

    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/admin/approve/<email>", methods=["POST"])
def approve_user(email):
    if not session.get("is_admin"):
        return jsonify({"ok": False}), 403

    admin = get_admin_cloud()
    if not admin:
        return jsonify({"ok": False, "error": "Service key not configured"}), 500

    try:
        admin.table("profiles").update({"approved": True}).eq("email", email).execute()
    except Exception:
        pass
    return redirect("/admin")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
