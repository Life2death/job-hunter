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
  .status-skipped {{ color: #999 !important; }}
  .status-not_interested {{ color: #999 !important; }}
</style>
</head>
<body>
{tabs_html("jobs")}
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
            var ___d = params.data;
            fetch('/apply/' + encodeURIComponent(___d.job_id), {{ method: 'POST' }})
              .then(function(r) {{
                if (r.ok) {{
                  var upd = Object.assign({{}}, ___d);
                  upd.status = 'applied';
                  upd.applied_date = new Date().toISOString().slice(0, 10);
                  params.api.applyTransaction({{ update: [upd] }});
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
{tabs_html("dashboard")}
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

    def build_query():
        q = cloud.table("job_listings").select("*").eq("user_id", u)
        if track:
            q = q.eq("track", track)
        if portal:
            q = q.eq("portal", portal)
        if status:
            q = q.eq("status", status)
        if applied_date:
            q = q.eq("applied_date", applied_date)
        return q.gte("fit", min_fit).order("fit", desc=True)

    page_size = 1000
    all_jobs = []
    offset = 0
    while True:
        batch = build_query().range(offset, offset + page_size - 1).execute()
        data = batch.data or []
        if not data:
            break
        all_jobs.extend(data)
        if len(data) < page_size:
            break
        offset += page_size

    return jsonify(all_jobs)


@app.route("/api/jobs/stats")
def api_jobs_stats():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500

    u = uid()
    today_str = str(date.today())
    yesterday_str = str(date.today() - timedelta(days=1))

    result = cloud.table("job_listings").select("applied_date, company, status").eq("status", "applied").eq("user_id", u).execute()
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
