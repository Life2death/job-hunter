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


def generate_html(jobs):
    rows_html = ""
    for j in jobs:
        job_id = j.get("job_id", "")
        track = j.get("track", "")
        portal = j.get("portal", "")
        title = j.get("title", "")
        company = j.get("company", "")
        location = j.get("location", "")
        salary = j.get("salary", "")
        url = j.get("url", "") or ""
        fit = j.get("fit", 0)
        freshness = j.get("freshness", "")
        status = j.get("status", "not_applied")
        applied_date = j.get("applied_date", "") or ""

        fit_cls = "fit-high" if fit >= 60 else ("fit-mid" if fit >= 40 else "fit-low")
        url_display = url[:60] + "..." if len(url) > 60 else url
        status_display = f"applied {applied_date}" if status == "applied" and applied_date else status
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
          <td>{track}</td>
          <td>{portal}</td>
          <td>{title}</td>
          <td>{company}</td>
          <td>{location}</td>
          <td><span class="status-{status}">{status_display}</span></td>
          <td class="flags-cell">{flags}</td>
          <td class="url-cell"><a class="job-link" href="{url}" target="_blank" title="{url}">{url_display}</a></td>
        </tr>"""

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
  th {{ background: #f0f0f0; cursor: pointer; user-select: none; position: sticky; top: 0; }}
  th:hover {{ background: #e0e0e0; }}
  th::after {{ content: " \\25B4\\25BE"; font-size: 9px; color: #999; }}
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
</style>
</head>
<body>
<h1>Job Queue</h1>
<div class="filters">
  <label>Track: <select id="fTrack"><option value="">All</option><option>SM</option><option>PM</option><option>DIR</option></select></label>
  <label>Portal: <select id="fPortal"><option value="">All</option><option>LinkedIn</option><option>Adzuna</option><option>Foundit</option><option>IIMJobs</option><option>Naukri</option></select></label>
  <label>Status: <select id="fStatus"><option value="">All</option><option>not_applied</option><option>applied</option><option>skipped</option><option>not_interested</option></select></label>
  <label>Min Fit: <input id="fMinFit" type="number" value="0" style="width:60px"></label>
  <span class="count" id="jobCount"></span>
</div>
<table>
<thead><tr>
  <th data-col="fit" data-num="1">Fit</th>
  <th data-col="freshness">Fresh</th>
  <th data-col="track">Track</th>
  <th data-col="portal">Portal</th>
  <th data-col="title">Title</th>
  <th data-col="company">Company</th>
  <th data-col="location">Location</th>
  <th data-col="status">Status</th>
  <th data-col="flags">Flags</th>
  <th>URL</th>
</tr></thead>
<tbody id="tbody">{rows_html}</tbody>
</table>
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

const tbody = document.getElementById('tbody');
const allRows = Array.from(tbody.querySelectorAll('tr'));
let sortCol = 'fit', sortDesc = true;

function filterAndSort() {{
  const fTrack = document.getElementById('fTrack').value;
  const fPortal = document.getElementById('fPortal').value;
  const fStatus = document.getElementById('fStatus').value;
  const fMin = parseInt(document.getElementById('fMinFit').value) || 0;
  let filtered = allRows.filter(r => {{
    const c = r.cells;
    return (!fTrack || c[2].textContent === fTrack) &&
           (!fPortal || c[3].textContent === fPortal) &&
           (!fStatus || c[7].textContent.startsWith(fStatus)) &&
           (parseInt(c[0].textContent) >= fMin);
  }});
  const colIdx = {{fit:0, freshness:1, track:2, portal:3, title:4, company:5, location:6, status:7, flags:8}}[sortCol];
  filtered.sort((a,b) => {{
    const va = a.cells[colIdx].textContent, vb = b.cells[colIdx].textContent;
    const na = parseFloat(va), nb = parseFloat(vb);
    const cmp = isNaN(na) || isNaN(nb) ? va.localeCompare(vb) : na - nb;
    return sortDesc ? -cmp : cmp;
  }});
  tbody.innerHTML = '';
  filtered.forEach(r => tbody.appendChild(r));
  document.getElementById('jobCount').textContent = filtered.length + ' jobs';
}}
document.querySelectorAll('.filters select, .filters input').forEach(el => el.addEventListener('change', filterAndSort));
document.querySelectorAll('th').forEach(th => th.addEventListener('click', () => {{
  if (th.dataset.col) {{
    if (sortCol === th.dataset.col) sortDesc = !sortDesc;
    else {{ sortCol = th.dataset.col; sortDesc = true; }}
    filterAndSort();
  }}
}}));
filterAndSort();
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
    min_fit = request.args.get("min_fit", 0, type=int)

    query = cloud.table("job_listings").select("*")
    if track:
        query = query.eq("track", track)
    if portal:
        query = query.eq("portal", portal)
    query = query.gte("fit", min_fit).order("fit", desc=True)

    result = query.execute()
    jobs = result.data if result else []
    html = generate_html(jobs)
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
