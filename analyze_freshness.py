"""
analyze_freshness.py
Read-only diagnostic for the "same job re-imported as fresh every day" problem.

It pulls every job_listings row for one user from Supabase, groups rows by three
identity schemes, and reports how much of the inventory (and how much of *today's*
"new" rows) are actually re-rolls of postings that already existed.

Nothing is ever written to the database. The only file written is a snapshot JSON
used by --compare the next day.

Usage:
    python analyze_freshness.py                         # report + write snapshot for today
    python analyze_freshness.py --top 30                # show 30 worst duplicate groups
    python analyze_freshness.py --out snap.json         # custom snapshot path
    python analyze_freshness.py --compare snap_0610.json  # diff today's data vs a prior snapshot

Identity schemes (within-portal only — cross-portal duplicates are intentional):
    id_canon = canon_url                         (when non-empty)
    id_norm  = portal + normalize_job_key(job_id)   (strips prefix + _<page> suffix)
    id_text  = portal + norm(title) + norm(company) (heuristic, report-only)
"""

import os, sys, json, re, argparse
from datetime import date
from collections import defaultdict

from dedup import canonical_url, normalize_job_key

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
USER_EMAIL = os.environ.get("USER_EMAIL", "")

# Columns we need for the analysis (read-only select).
SELECT_COLS = ("job_id, canon_url, portal, track, title, company, location, url, "
               "status, imported_date, applied_date, last_seen_date")

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def get_cloud():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[!] Set SUPABASE_URL and SUPABASE_KEY env vars (run this in D:\\Job).")
        sys.exit(1)
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        print("[!] pip install supabase")
        sys.exit(1)


def fetch_all(cloud, user_id, select_cols=SELECT_COLS, table="job_listings"):
    """Paginated read of every row for a user (mirrors backfill_dedup.fetch_all)."""
    page_size = 1000
    all_rows, off = [], 0
    while True:
        batch = (cloud.table(table).select(select_cols)
                 .eq("user_id", user_id)
                 .range(off, off + page_size - 1).execute())
        data = batch.data or []
        all_rows.extend(data)
        if len(data) < page_size:
            break
        off += page_size
    return all_rows


def _norm_text(s):
    """Lowercase, drop punctuation, collapse whitespace. '' for falsy input."""
    if not s:
        return ""
    s = _PUNCT.sub(" ", str(s).lower())
    return _WS.sub(" ", s).strip()


def _fmt_date(v):
    """Normalize a date/datetime value to a YYYY-MM-DD string ('' if absent)."""
    if not v:
        return ""
    return str(v)[:10]


def id_norm(row):
    """portal + normalized job_id (prefix and _<page> stripped)."""
    portal = (row.get("portal") or "").strip().lower()
    nk = normalize_job_key(row)
    return f"{portal}|{nk}" if nk else ""


def id_text(row):
    """portal + normalized title + normalized company (report-only heuristic)."""
    portal = (row.get("portal") or "").strip().lower()
    t, c = _norm_text(row.get("title")), _norm_text(row.get("company"))
    return f"{portal}|{t}|{c}" if (t or c) else ""


def _earliest_by_identity(rows, key_fn):
    """Map identity -> earliest imported_date string seen for it."""
    earliest = {}
    for r in rows:
        k = key_fn(r)
        if not k:
            continue
        d = _fmt_date(r.get("imported_date"))
        if not d:
            continue
        if k not in earliest or d < earliest[k]:
            earliest[k] = d
    return earliest


def analyze(rows, today_str, top_n):
    """Compute overall + per-portal freshness stats and worst duplicate groups."""
    by_portal = defaultdict(list)
    for r in rows:
        by_portal[(r.get("portal") or "?").strip() or "?"].append(r)

    def portal_block(prows):
        total = len(prows)
        canon_keys = {r.get("canon_url") for r in prows if r.get("canon_url")}
        norm_keys = {id_norm(r) for r in prows if id_norm(r)}
        text_keys = {id_text(r) for r in prows if id_text(r)}
        empty_canon = sum(1 for r in prows if not r.get("canon_url"))
        seen_once = sum(1 for r in prows
                        if _fmt_date(r.get("imported_date"))
                        and _fmt_date(r.get("imported_date")) == _fmt_date(r.get("last_seen_date")))

        # false-fresh today: rows imported today whose identity existed before today
        earliest_norm = _earliest_by_identity(prows, id_norm)
        earliest_text = _earliest_by_identity(prows, id_text)
        ff_norm = ff_text = today_new = 0
        for r in prows:
            if _fmt_date(r.get("imported_date")) != today_str:
                continue
            today_new += 1
            kn, kt = id_norm(r), id_text(r)
            if kn and earliest_norm.get(kn, today_str) < today_str:
                ff_norm += 1
            if kt and earliest_text.get(kt, today_str) < today_str:
                ff_text += 1

        return {
            "total_rows": total,
            "distinct_id_norm": len(norm_keys),
            "distinct_id_text": len(text_keys),
            "dup_ratio_text": round(total / len(text_keys), 2) if text_keys else None,
            "empty_canon_url": empty_canon,
            "empty_canon_pct": round(100 * empty_canon / total, 1) if total else 0,
            "seen_once": seen_once,
            "seen_once_pct": round(100 * seen_once / total, 1) if total else 0,
            "today_new_rows": today_new,
            "false_fresh_today_by_norm": ff_norm,
            "false_fresh_today_by_text": ff_text,
        }

    per_portal = {p: portal_block(pr) for p, pr in sorted(by_portal.items())}
    overall = portal_block(rows)

    # Worst duplicate groups by id_text across all portals
    text_groups = defaultdict(list)
    for r in rows:
        k = id_text(r)
        if k:
            text_groups[k].append(r)
    worst = sorted((g for g in text_groups.values() if len(g) > 1),
                   key=len, reverse=True)[:top_n]
    worst_out = []
    for g in worst:
        dates = sorted({_fmt_date(r.get("imported_date")) for r in g if r.get("imported_date")})
        sample = g[0]
        worst_out.append({
            "portal": sample.get("portal"),
            "title": sample.get("title"),
            "company": sample.get("company"),
            "rows": len(g),
            "distinct_import_dates": len(dates),
            "date_span": f"{dates[0]} .. {dates[-1]}" if dates else "",
            "sample_job_ids": [r.get("job_id") for r in g[:4]],
            "sample_urls": [r.get("url") for r in g[:2]],
        })

    return {"overall": overall, "per_portal": per_portal, "worst_dup_groups": worst_out}


def print_report(result, today_str, user):
    o = result["overall"]
    print("=" * 70)
    print(f"FRESHNESS REPORT  user={user}  today={today_str}")
    print("=" * 70)
    print(f"  total rows .............. {o['total_rows']}")
    print(f"  distinct id_norm ........ {o['distinct_id_norm']}")
    print(f"  distinct id_text ........ {o['distinct_id_text']}")
    print(f"  dup ratio (rows/id_text)  {o['dup_ratio_text']}")
    print(f"  empty canon_url ......... {o['empty_canon_url']} ({o['empty_canon_pct']}%)")
    print(f"  seen exactly once ....... {o['seen_once']} ({o['seen_once_pct']}%)  <- re-roll signature")
    print(f"  rows imported TODAY ..... {o['today_new_rows']}")
    print(f"    of which false-fresh (id_norm) .. {o['false_fresh_today_by_norm']}")
    print(f"    of which false-fresh (id_text) .. {o['false_fresh_today_by_text']}")
    print()
    print(f"{'portal':<12}{'rows':>7}{'id_norm':>9}{'id_text':>9}{'dup':>6}"
          f"{'emptyURL%':>10}{'once%':>7}{'newToday':>9}{'ffNorm':>8}{'ffText':>8}")
    for p, s in result["per_portal"].items():
        print(f"{p:<12}{s['total_rows']:>7}{s['distinct_id_norm']:>9}{s['distinct_id_text']:>9}"
              f"{str(s['dup_ratio_text']):>6}{s['empty_canon_pct']:>10}{s['seen_once_pct']:>7}"
              f"{s['today_new_rows']:>9}{s['false_fresh_today_by_norm']:>8}{s['false_fresh_today_by_text']:>8}")
    print()
    print(f"--- top {len(result['worst_dup_groups'])} duplicate groups by id_text ---")
    for w in result["worst_dup_groups"]:
        print(f"  [{w['portal']}] {w['rows']} rows over {w['distinct_import_dates']} dates "
              f"({w['date_span']})  {w['title']} @ {w['company']}")
        print(f"      job_ids: {w['sample_job_ids']}")
    print("=" * 70)


def build_snapshot(rows, today_str, user, result):
    """Identity sets with earliest import date, for next-day --compare."""
    earliest_norm = _earliest_by_identity(rows, id_norm)
    earliest_text = _earliest_by_identity(rows, id_text)
    return {
        "user": user,
        "today": today_str,
        "overall": result["overall"],
        "per_portal": result["per_portal"],
        "id_norm_earliest": earliest_norm,
        "id_text_earliest": earliest_text,
    }


def do_compare(rows, today_str, snap_path):
    with open(snap_path, "r", encoding="utf-8") as f:
        snap = json.load(f)
    prev_norm = snap.get("id_norm_earliest", {})
    prev_text = snap.get("id_text_earliest", {})
    prev_day = snap.get("today", "?")

    new_rows = [r for r in rows if _fmt_date(r.get("imported_date")) == today_str]
    seen_before_norm = sum(1 for r in new_rows if id_norm(r) in prev_norm)
    seen_before_text = sum(1 for r in new_rows if id_text(r) in prev_text)
    genuinely_new = sum(1 for r in new_rows
                        if id_norm(r) not in prev_norm and id_text(r) not in prev_text)

    print("=" * 70)
    print(f"COMPARE  snapshot={prev_day}  ->  today={today_str}")
    print("=" * 70)
    print(f"  rows imported today ............. {len(new_rows)}")
    print(f"  identity already in snapshot:")
    print(f"    by id_norm (job_id) ........... {seen_before_norm}  <- false-fresh (re-roll)")
    print(f"    by id_text (title/company) .... {seen_before_text}")
    print(f"  genuinely new (neither match) ... {genuinely_new}")
    if new_rows:
        pct = round(100 * seen_before_norm / len(new_rows), 1)
        print(f"  => {pct}% of 'new' rows are re-rolls by normalized job_id")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="Read-only job freshness / duplicate diagnostic")
    ap.add_argument("--user-id", default=USER_EMAIL, help="User email (default: USER_EMAIL env)")
    ap.add_argument("--top", type=int, default=20, help="How many worst dup groups to show")
    ap.add_argument("--compare", metavar="SNAPSHOT.json", help="Diff today vs a prior snapshot")
    ap.add_argument("--out", help="Snapshot output path (default: freshness_snapshot_<today>.json)")
    args = ap.parse_args()

    if not args.user_id:
        print("[!] Provide --user-id or set USER_EMAIL env var")
        sys.exit(1)

    today_str = str(date.today())
    cloud = get_cloud()
    print(f"Fetching rows for {args.user_id} ...")
    rows = fetch_all(cloud, args.user_id)
    print(f"Fetched {len(rows)} rows.\n")

    if args.compare:
        do_compare(rows, today_str, args.compare)
        return

    result = analyze(rows, today_str, args.top)
    print_report(result, today_str, args.user_id)

    out = args.out or f"freshness_snapshot_{today_str.replace('-', '')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(build_snapshot(rows, today_str, args.user_id, result), f, indent=2)
    print(f"\nSnapshot written: {out}")
    print(f"Tomorrow after the morning run:  python analyze_freshness.py --compare {out}")


if __name__ == "__main__":
    main()
