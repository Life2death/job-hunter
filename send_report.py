"""
send_report.py
Generates and sends an HTML email with today's job breakdown, last 7 days
summary, and applied stats after each pipeline run.

Environment variables required:
  SUPABASE_URL, SUPABASE_KEY, USER_EMAIL
  EMAIL_ADDRESS, SMTP_PASSWORD
"""

import os, smtplib, json
from datetime import date, timedelta, timezone, datetime
from collections import defaultdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from settings import PORTAL_DISPLAY, TRACKS

TODAY = date.today()
TODAY_STR = TODAY.isoformat()


def get_supabase():
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")
    from supabase import create_client
    return create_client(url, key)


def fetch_all(supabase, user_id, select_cols, filters=None):
    """Paginated fetch from job_listings. filters is a list of (method, args) tuples."""
    page_size = 1000
    all_rows = []
    offset = 0
    while True:
        q = supabase.table("job_listings").select(select_cols)
        if user_id:
            q = q.eq("user_id", user_id)
        if filters:
            for method, args in filters:
                q = getattr(q, method)(*args)
        q = q.is_("hidden", "null")
        batch = q.range(offset, offset + page_size - 1).execute()
        data = batch.data or []
        if not data:
            break
        all_rows.extend(data)
        if len(data) < page_size:
            break
        offset += page_size
    return all_rows


def build_breakdown(rows):
    """Aggregate rows into portal x track grid."""
    grid = defaultdict(lambda: defaultdict(int))
    for r in rows:
        portal = PORTAL_DISPLAY.get(r.get("portal", ""), r.get("portal", ""))
        track = r.get("track", "")
        if track in TRACKS:
            grid[portal][track] += 1
    return grid


def build_html(subject, today_data, week_data, applied_data, top_jobs):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows_html = ""
    track_totals = defaultdict(int)
    portal_totals = defaultdict(int)
    grand = 0
    portals_sorted = sorted(today_data.keys())
    for portal in portals_sorted:
        rows_html += "<tr>"
        rows_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;font-weight:600">{portal}</td>'
        p_total = 0
        for t in TRACKS:
            c = today_data[portal].get(t, 0)
            rows_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;text-align:center">{c}</td>'
            p_total += c
            track_totals[t] += c
        rows_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;text-align:center;font-weight:700">{p_total}</td>'
        rows_html += "</tr>"
        grand += p_total

    rows_html += '<tr style="background:#f0f4ff;font-weight:700">'
    rows_html += '<td style="padding:4px 10px;border-bottom:2px solid #4a90d9">Total</td>'
    for t in TRACKS:
        rows_html += f'<td style="padding:4px 10px;border-bottom:2px solid #4a90d9;text-align:center">{track_totals[t]}</td>'
    rows_html += f'<td style="padding:4px 10px;border-bottom:2px solid #4a90d9;text-align:center">{grand}</td>'
    rows_html += "</tr>"

    top_html = ""
    if top_jobs:
        for i, j in enumerate(top_jobs[:10], 1):
            top_html += "<tr>"
            c = "#fafafa" if i % 2 == 0 else "#fff"
            fit_cls = "color:#1a7d1a;font-weight:700" if j.get("fit", 0) >= 60 else ("color:#b8860b" if j.get("fit", 0) >= 40 else "color:#999")
            top_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;text-align:center;background:{c};{fit_cls}">{j.get("fit", 0)}</td>'
            top_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;background:{c}">{j.get("title","")[:50]}</td>'
            top_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;background:{c}">{j.get("company","")}</td>'
            top_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;background:{c}">{j.get("portal","")}</td>'
            top_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;background:{c}">{j.get("track","")}</td>'
            top_html += "</tr>"

    def _track_total(grid, track):
        return sum(p.get(track, 0) for p in grid.values())

    week_html = ""
    week_total = 0
    week_track_totals = {t: 0 for t in TRACKS}
    if week_data:
        for day_label, day_grid in sorted(week_data.items()):
            week_html += "<tr>"
            day_track = {t: _track_total(day_grid, t) for t in TRACKS}
            d_total = sum(day_track.values())
            week_total += d_total
            week_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;font-weight:600">{day_label}</td>'
            for t in TRACKS:
                week_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;text-align:center">{day_track[t]}</td>'
                week_track_totals[t] += day_track[t]
            week_html += f'<td style="padding:4px 10px;border-bottom:1px solid #e0e0e0;text-align:center;font-weight:700">{d_total}</td>'
            week_html += "</tr>"
        week_html += '<tr style="background:#f0f4ff;font-weight:700">'
        week_html += '<td style="padding:4px 10px;border-bottom:2px solid #4a90d9">7-Day Total</td>'
        for t in TRACKS:
            week_html += f'<td style="padding:4px 10px;border-bottom:2px solid #4a90d9;text-align:center">{week_track_totals[t]}</td>'
        week_html += f'<td style="padding:4px 10px;border-bottom:2px solid #4a90d9;text-align:center">{week_total}</td>'
        week_html += "</tr>"

    applied_today, applied_week = applied_data

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font:14px/1.5 system-ui,-apple-system,sans-serif;background:#f5f6f8;margin:0;padding:0;color:#333; }}
  .container {{ max-width:700px;margin:20px auto;background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.08);overflow:hidden; }}
  .header {{ background:linear-gradient(135deg,#1a237e,#283593);color:#fff;padding:28px 30px; }}
  .header h1 {{ margin:0;font-size:22px;font-weight:600; }}
  .header p {{ margin:6px 0 0;opacity:0.85;font-size:13px; }}
  .section {{ padding:20px 30px;border-bottom:1px solid #e8e8e8; }}
  .section:last-child {{ border-bottom:none; }}
  .section h2 {{ margin:0 0 12px;font-size:16px;color:#1a237e; }}
  table {{ width:100%;border-collapse:collapse;font-size:13px; }}
  th {{ background:#f0f4ff;padding:6px 10px;text-align:center;border-bottom:2px solid #4a90d9;font-weight:600;color:#1a237e;font-size:12px;text-transform:uppercase;letter-spacing:0.5px; }}
  th:first-child {{ text-align:left; }}
  .stats-grid {{ display:grid;grid-template-columns:1fr 1fr;gap:12px; }}
  .stat-card {{ background:#f8faff;border-radius:6px;padding:12px 16px;border:1px solid #e0e8f0; }}
  .stat-card .num {{ font-size:22px;font-weight:700;color:#1a237e; }}
  .stat-card .label {{ font-size:12px;color:#666;margin-top:2px; }}
  .footer {{ padding:20px 30px;text-align:center;font-size:12px;color:#888; }}
  .footer a {{ color:#283593;text-decoration:none;font-weight:600; }}
  .empty {{ color:#999;font-style:italic;padding:12px 0;text-align:center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{chr(128188)} Job Hunter Report</h1>
    <p>{subject} &middot; {now_utc}</p>
  </div>

  <div class="section">
    <h2>TODAY'S FETCH &mdash; by Portal &times; Track</h2>
    <table>
      <thead><tr>
        <th style="text-align:left">Portal</th>
        <th>SM</th><th>PM</th><th>DIR</th><th>Total</th>
      </tr></thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>"""

    if top_jobs:
        html += f"""
  <div class="section">
    <h2>TOP FRESHEST JOBS TODAY</h2>
    <table>
      <thead><tr>
        <th>Fit</th><th style="text-align:left">Title</th><th style="text-align:left">Company</th><th>Portal</th><th>Track</th>
      </tr></thead>
      <tbody>
        {top_html}
      </tbody>
    </table>
  </div>"""

    if week_data:
        html += f"""
  <div class="section">
    <h2>LAST 7 DAYS &mdash; New Jobs by Track</h2>
    <table>
      <thead><tr>
        <th style="text-align:left">Date</th><th>SM</th><th>PM</th><th>DIR</th><th>Total</th>
      </tr></thead>
      <tbody>
        {week_html}
      </tbody>
    </table>
  </div>"""

    applied_str = f"""
    <div class="stat-card"><div class="num">{applied_today}</div><div class="label">Applied Today</div></div>
    <div class="stat-card"><div class="num">{applied_week}</div><div class="label">Applied This Week</div></div>
    <div class="stat-card"><div class="num">{grand}</div><div class="label">New Jobs Today</div></div>
    <div class="stat-card"><div class="num">{week_total}</div><div class="label">New Jobs 7 Days</div></div>"""

    html += f"""
  <div class="section">
    <h2>SUMMARY</h2>
    <div class="stats-grid">
      {applied_str}
    </div>
  </div>

  <div class="footer">
    <p><a href="https://job-hunter-x5l1.onrender.com">Open Dashboard &rarr;</a></p>
    <p style="margin-top:8px">This email was sent automatically after the pipeline run.</p>
  </div>
</div>
</body>
</html>"""
    return html


def send_email(subject, html_body):
    sender = os.environ.get("EMAIL_ADDRESS", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if not sender or not password:
        print("[mail] EMAIL_ADDRESS and SMTP_PASSWORD required")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Job Hunter Bot <{sender}>"
    msg["To"] = sender
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [sender], msg.as_string())
        server.quit()
        print(f"[mail] Email sent to {sender}")
        return True
    except Exception as e:
        print(f"[mail] Failed to send: {e}")
        return False


def main():
    user_id = os.environ.get("USER_EMAIL", "")
    if not user_id:
        print("[report] USER_EMAIL is required")
        return

    supabase = get_supabase()

    applied_today = 0
    applied_week = 0
    top_jobs = []

    try:
        today_rows = fetch_all(supabase, user_id, "portal,track,fit,title,company",
                               filters=[("eq", ("imported_date", TODAY_STR))])
    except Exception as e:
        print(f"[report] Failed to fetch today's data: {e}")
        today_rows = []

    try:
        week_ago = TODAY - timedelta(days=6)
        week_rows = fetch_all(supabase, user_id, "imported_date,track,portal",
                              filters=[("gte", ("imported_date", week_ago.isoformat()))])

        applied_rows = fetch_all(supabase, user_id, "status,applied_date",
                                 filters=[("eq", ("status", "applied"))])
        applied_today = sum(1 for r in applied_rows if r.get("applied_date") == TODAY_STR)
        applied_week = sum(1 for r in applied_rows
                          if r.get("applied_date", "") >= week_ago.isoformat()
                          and r.get("applied_date", "") <= TODAY_STR)
    except Exception as e:
        print(f"[report] Failed to fetch week/applied data: {e}")
        week_rows = []

    today_data = build_breakdown(today_rows)

    try:
        sorted_today = sorted(today_rows, key=lambda x: x.get("fit", 0) or 0, reverse=True)
        top_jobs = sorted_today[:10]
    except Exception:
        pass

    week_data = {}
    if week_rows:
        by_day = defaultdict(list)
        for r in week_rows:
            d = r.get("imported_date", "")
            if d:
                by_day[d].append(r)
        for day_label in sorted(by_day.keys()):
            week_data[day_label] = build_breakdown(by_day[day_label])

    total_today = sum(
        today_data[p][t]
        for p in today_data
        for t in TRACKS
    )

    subject = f"Job Hunter Report \u2014 {TODAY_STR} \u2014 {total_today} new jobs, {applied_today} applied"

    html = build_html(subject, today_data, week_data, (applied_today, applied_week), top_jobs)
    send_email(subject, html)
    print(f"[report] Done. {total_today} new jobs, {applied_today} applied")


if __name__ == "__main__":
    main()
