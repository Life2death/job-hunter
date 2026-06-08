"""
web_app.py
Flask web service for interactive job report.
Serves the ranked job table with click-to-apply tracking.
Deployed on Render free tier. Reads/writes to Supabase.
"""

import os, sys
from datetime import date

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


def generate_html(track="", portal="", status="", min_fit=0):
    qs_parts = []
    if track: qs_parts.append(f"track={track}")
    if portal: qs_parts.append(f"portal={portal}")
    if status: qs_parts.append(f"status={status}")
    if min_fit: qs_parts.append(f"min_fit={min_fit}")
    qs = "&".join(qs_parts)
    api_url = f"/api/jobs?{qs}" if qs else "/api/jobs"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Queue - Multi Portal</title>
<script src="https://cdn.jsdelivr.net/npm/ag-grid-community@30.2.1/dist/ag-grid-community.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@30.2.1/styles/ag-grid.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@30.2.1/styles/ag-theme-alpine.min.css">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font: 14px/1.5 system-ui, sans-serif; background: #f5f5f5; padding: 20px; }}
  h1 {{ margin-bottom: 12px; }}
  #jobGrid {{ height: calc(100vh - 80px); width: 100%; }}
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
<h1>Job Queue</h1>
<div id="jobGrid" class="ag-theme-alpine"></div>
<script>
var gridOptions = {{
  columnDefs: [
    {{ field: 'fit', width: 60, sortable: true, filter: 'agNumberColumnFilter',
       cellClassRules: {{ 'fit-high': p => p.value >= 60, 'fit-mid': p => p.value >= 40 }} }},
    {{ field: 'freshness', width: 90, sortable: true, filter: 'agSetColumnFilter' }},
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


@app.route("/api/jobs")
def api_jobs():
    cloud = get_cloud()
    if not cloud:
        return jsonify({"error": "Supabase not configured"}), 500

    track = request.args.get("track")
    portal = request.args.get("portal")
    status = request.args.get("status")
    min_fit = request.args.get("min_fit", 0, type=int)

    query = cloud.table("job_listings").select("*")
    if track:
        query = query.eq("track", track)
    if portal:
        query = query.eq("portal", portal)
    if status:
        query = query.eq("status", status)
    query = query.gte("fit", min_fit).order("fit", desc=True)

    result = query.execute()
    return jsonify(result.data if result else [])


@app.route("/")
def report():
    cloud = get_cloud()
    if not cloud:
        return "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY.", 500

    track = request.args.get("track", "")
    portal = request.args.get("portal", "")
    status = request.args.get("status", "")
    min_fit = request.args.get("min_fit", 0, type=int)

    html = generate_html(track=track, portal=portal, status=status, min_fit=min_fit)
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
