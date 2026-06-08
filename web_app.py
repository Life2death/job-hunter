"""
web_app.py
Flask web service for interactive job report.
Serves the ranked job table with click-to-apply tracking.
Deployed on Render free tier. Reads/writes to Supabase.
"""

import os, sys, json
from pathlib import Path
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


def _build_qs(page, track, portal, status, min_fit):
    import urllib.parse
    parts = [("page", str(page)), ("min_fit", str(min_fit))]
    if track:
        parts.append(("track", track))
    if portal:
        parts.append(("portal", portal))
    if status:
        parts.append(("status", status))
    return urllib.parse.urlencode(parts)

def _sel(a, b):
    return " selected" if a == b else ""

def generate_html(jobs, page=1, per_page=200, total=0, track="", portal="", status="", min_fit=0):
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    qs = lambda p: _build_qs(p, track, portal, status, min_fit)

    rows_html = ""
    for j in jobs:
        job_id = j.get("job_id", "")
        jtrack = j.get("track", "")
        jportal = j.get("portal", "")
        title = j.get("title", "")
        company = j.get("company", "")
        location = j.get("location", "")
        salary = j.get("salary", "")
        url = j.get("url", "") or ""
        fit = j.get("fit", 0)
        freshness = j.get("freshness", "")
        jstatus = j.get("status", "not_applied")
        applied_date = j.get("applied_date", "") or ""

        fit_cls = "fit-high" if fit >= 60 else ("fit-mid" if fit >= 40 else "fit-low")
        url_display = url[:60] + "..." if len(url) > 60 else url
        status_display = f"applied {applied_date}" if jstatus == "applied" and applied_date else jstatus
        flags = ""
        raw_sj = j.get("scores_json", "")
        if raw_sj:
            try:
                parsed = json.loads(raw_sj)
                if isinstance(parsed, dict) and "f" in parsed and parsed["f"]:
                    flags = ", ".join(parsed["f"])
            except Exception:
                pass

        rows_html += f"""
        <tr data-job-id="{job_id}">
          <td>{fit}</td>
          <td>{freshness}</td>
          <td>{jtrack}</td>
          <td>{jportal}</td>
          <td>{title}</td>
          <td>{company}</td>
          <td>{location}</td>
          <td><span class="status-{jstatus}">{status_display}</span></td>
          <td class="flags-cell">{flags}</td>
          <td class="url-cell"><a class="job-link" href="{url}" target="_blank" title="{url}">{url_display}</a></td>
        </tr>"""

    start = (page - 1) * per_page + 1
    end = min(page * per_page, total)
    showing = f"Showing {start}&ndash;{end} of {total}" if total else f"{len(jobs)} jobs"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job Queue - Multi Portal</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font: 14px/1.5 system-ui, sans-serif; background: #f5f5f5; padding: 20px; }}
  h1 {{ margin-bottom: 12px; }}
  .filters {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }}
  .filters label {{ font-weight: 600; }}
  .filters select, .filters input {{ padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; }}
  .filters .count {{ margin-left: auto; color: #555; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #eee; white-space: nowrap; }}
  th {{ background: #f0f0f0; user-select: none; position: sticky; top: 0; }}
  tr:hover {{ background: #fafafa; }}
  .fit-high {{ color: #1a7d1a; font-weight: 700; }}
  .fit-mid  {{ color: #b8860b; }}
  .fit-low  {{ color: #999; }}
  .status-not_applied {{ color: #d32f2f; }}
  .status-applied {{ color: #1a7d1a; }}
  .status-skipped {{ color: #999; }}
  .status-not_interested {{ color: #999; }}
  .url-cell {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; }}
  .url-cell a {{ color: #1565c0; text-decoration: none; }}
  .url-cell a:hover {{ text-decoration: underline; cursor: pointer; }}
  .flags-cell {{ max-width: 160px; font-size: 11px; color: #c62828; white-space: normal; }}
  .pagination {{ display: flex; justify-content: center; align-items: center; gap: 8px; margin-top: 16px; }}
  .pagination a, .pagination span {{ padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; text-decoration: none; color: #333; background: #fff; font-size: 14px; }}
  .pagination a:hover:not(.disabled) {{ background: #e0e0e0; }}
  .pagination .disabled {{ color: #aaa; cursor: default; border-color: #eee; }}
  .pagination .current {{ background: #1565c0; color: #fff; border-color: #1565c0; font-weight: 600; }}
</style>
</head>
<body>
<h1>Job Queue</h1>
<div class="filters">
  <label>Track: <select id="fTrack" onchange="goFilter()"><option value=""{_sel(track, "")}>All</option><option value="SM"{_sel(track, "SM")}>SM</option><option value="PM"{_sel(track, "PM")}>PM</option><option value="DIR"{_sel(track, "DIR")}>DIR</option></select></label>
  <label>Portal: <select id="fPortal" onchange="goFilter()"><option value=""{_sel(portal, "")}>All</option><option value="LinkedIn"{_sel(portal, "LinkedIn")}>LinkedIn</option><option value="Adzuna"{_sel(portal, "Adzuna")}>Adzuna</option><option value="Foundit"{_sel(portal, "Foundit")}>Foundit</option><option value="IIMJobs"{_sel(portal, "IIMJobs")}>IIMJobs</option><option value="Naukri"{_sel(portal, "Naukri")}>Naukri</option></select></label>
  <label>Status: <select id="fStatus" onchange="goFilter()"><option value=""{_sel(status, "")}>All</option><option value="not_applied"{_sel(status, "not_applied")}>not_applied</option><option value="applied"{_sel(status, "applied")}>applied</option><option value="skipped"{_sel(status, "skipped")}>skipped</option><option value="not_interested"{_sel(status, "not_interested")}>not_interested</option></select></label>
  <label>Min Fit: <input id="fMinFit" type="number" value="{min_fit}" style="width:60px" onchange="goFilter()"></label>
  <span class="count" id="jobCount">{showing}</span>
</div>
<table>
<thead><tr>
  <th>Fit</th>
  <th>Fresh</th>
  <th>Track</th>
  <th>Portal</th>
  <th>Title</th>
  <th>Company</th>
  <th>Location</th>
  <th>Status</th>
  <th>Flags</th>
  <th>URL</th>
</tr></thead>
<tbody id="tbody">{rows_html}</tbody>
</table>
<div class="pagination">
  <a href="/?{qs(1)}" class="{'disabled' if page <= 1 else ''}">&laquo; First</a>
  <a href="/?{qs(page if page <= 1 else page - 1)}" class="{'disabled' if page <= 1 else ''}">&#8249; Prev</a>
  <span class="current">Page {page} of {total_pages}</span>
  <a href="/?{qs(page if page >= total_pages else page + 1)}" class="{'disabled' if page >= total_pages else ''}">Next &#8250;</a>
  <a href="/?{qs(total_pages)}" class="{'disabled' if page >= total_pages else ''}">Last &raquo;</a>
</div>
<script>
function attachApplyHandler() {{
  document.querySelectorAll('.job-link').forEach(function(link) {{
    link.addEventListener('click', function(e) {{
      e.preventDefault();
      var jobId = this.closest('tr').dataset.jobId;
      var url = this.href;
      var row = this.closest('tr');
      var statusCell = row.cells[7];
      fetch('/apply/' + encodeURIComponent(jobId), {{ method: 'POST' }})
        .then(function(r) {{
          if (r.ok) {{
            statusCell.innerHTML = '<span class=\"status-applied\">applied ' + new Date().toISOString().slice(0,10) + '</span>';
          }}
          window.open(url, '_blank');
        }})
        .catch(function() {{
          window.open(url, '_blank');
        }});
    }});
  }});
}}
attachApplyHandler();

function goFilter() {{
  var params = new URLSearchParams();
  params.set('track', document.getElementById('fTrack').value);
  params.set('portal', document.getElementById('fPortal').value);
  params.set('status', document.getElementById('fStatus').value);
  params.set('min_fit', document.getElementById('fMinFit').value);
  params.set('page', '1');
  window.location = '/?' + params.toString();
}}
</script>
</body>
</html>"""


@app.route("/")
def report():
    cloud = get_cloud()
    if not cloud:
        return "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY.", 500

    track = request.args.get("track")
    portal = request.args.get("portal")
    status = request.args.get("status")
    min_fit = request.args.get("min_fit", 0, type=int)
    page = request.args.get("page", 1, type=int)
    per_page = 200

    def apply_filters(q):
        if track:
            q = q.eq("track", track)
        if portal:
            q = q.eq("portal", portal)
        if status:
            q = q.eq("status", status)
        return q.gte("fit", min_fit)

    count_resp = apply_filters(cloud.table("job_listings").select("*", count="exact")).execute()
    total = getattr(count_resp, 'count', None)
    if total is None:
        total = len(count_resp.data)

    query = apply_filters(cloud.table("job_listings").select("*")).order("fit", desc=True)
    offset = (page - 1) * per_page
    query = query.range(offset, offset + per_page - 1)
    result = query.execute()
    jobs = result.data if result else []

    html = generate_html(jobs, page=page, per_page=per_page, total=total,
                         track=track or "", portal=portal or "",
                         status=status or "", min_fit=min_fit)
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
