"""
backfill_dedup.py
Compute canon_url for all existing rows in Supabase, optionally collapse same-URL duplicates.

Usage:
    python backfill_dedup.py                        # Preview (dry run)
    python backfill_dedup.py --apply                # Stamp canon_url only
    python backfill_dedup.py --apply --collapse     # Stamp + merge same-URL dupes
"""

import os, sys, argparse
from datetime import date
from dedup import canonical_url

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
USER_EMAIL = os.environ.get("USER_EMAIL", "")

STATUS_RANK = {"applied": 0, "manual_apply": 1, "skipped": 2, "not_applied": 3}


def get_cloud():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[!] Set SUPABASE_URL and SUPABASE_KEY env vars")
        sys.exit(1)
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        print("[!] pip install supabase")
        sys.exit(1)


def fetch_all(cloud, table="job_listings", select_cols="*", user_id=None):
    page_size = 1000
    all_rows = []
    off = 0
    while True:
        q = cloud.table(table).select(select_cols)
        if user_id:
            q = q.eq("user_id", user_id)
        batch = q.range(off, off + page_size - 1).execute()
        data = batch.data or []
        if not data:
            break
        all_rows.extend(data)
        if len(data) < page_size:
            break
        off += page_size
    return all_rows


def phase_a_stamp(cloud, user_id, dry_run=True):
    """Compute and set canon_url for all rows missing it."""
    rows = fetch_all(cloud, select_cols="job_id, url, canon_url", user_id=user_id)
    to_update = []
    for r in rows:
        if r.get("canon_url"):
            continue
        cu = canonical_url(r.get("url", ""))
        if not cu:
            continue
        to_update.append((cu, r["job_id"]))

    if dry_run:
        print(f"[DRY RUN] Would stamp canon_url on {len(to_update)} rows")
        return []

    # Group same canon_url together for batch updates
    groups = {}
    for cu, jid in to_update:
        groups.setdefault(cu, []).append(jid)

    chunk_size = 50
    done = 0
    for cu, jids in groups.items():
        for i in range(0, len(jids), chunk_size):
            chunk = jids[i:i + chunk_size]
            cloud.table("job_listings").update({"canon_url": cu}).in_("job_id", chunk).execute()
            done += len(chunk)

    print(f"[OK] Stamped canon_url on {done} rows ({len(groups)} unique URLs)")


def phase_b_collapse(cloud, user_id, dry_run=True):
    """Merge same-URL duplicates: keep best status, delete others."""
    rows = fetch_all(cloud, select_cols="job_id, canon_url, status, applied_date, imported_date, fit, scores_json", user_id=user_id)
    url_groups = {}
    for r in rows:
        cu = r.get("canon_url") or ""
        if not cu:
            continue
        url_groups.setdefault(cu, []).append(r)

    actions = []
    for cu, group in url_groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: STATUS_RANK.get(x.get("status", "not_applied"), 99))
        keep = group[0]
        delete_ids = [r["job_id"] for r in group[1:]]
        best_fit = keep.get("fit") or 0
        best_scores = keep.get("scores_json") or ""
        for r in group[1:]:
            if (r.get("fit") or 0) > best_fit:
                best_fit = r["fit"]
                best_scores = r.get("scores_json") or ""
        merge_updates = {}
        if best_fit > (keep.get("fit") or 0):
            merge_updates["fit"] = best_fit
            merge_updates["scores_json"] = best_scores
        actions.append({
            "canon_url": cu,
            "keep_job_id": keep["job_id"],
            "keep_status": keep.get("status"),
            "delete_job_ids": delete_ids,
            "merge_updates": merge_updates,
        })

    if dry_run:
        for a in actions:
            print(f"  [DRY RUN] Keep {a['keep_job_id']} ({a['keep_status']}), delete {a['delete_job_ids']}, merge={a['merge_updates']}")
        print(f"[DRY RUN] {len(actions)} groups would be collapsed ({sum(len(a['delete_job_ids']) for a in actions)} rows deleted)")
        return actions

    for a in actions:
        if a["merge_updates"]:
            cloud.table("job_listings").update(a["merge_updates"]).eq("job_id", a["keep_job_id"]).execute()

    # Batch delete in chunks of 50 to minimize REST calls
    all_delete_ids = []
    for a in actions:
        all_delete_ids.extend(a["delete_job_ids"])
    chunk_size = 50
    for i in range(0, len(all_delete_ids), chunk_size):
        chunk = all_delete_ids[i:i + chunk_size]
        cloud.table("job_listings").delete().in_("job_id", chunk).execute()

    total_deleted = len(all_delete_ids)
    print(f"[OK] Collapsed {len(actions)} groups, deleted {total_deleted} rows")
    return actions


def main():
    parser = argparse.ArgumentParser(description="Backfill canon_url and collapse duplicates")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--collapse", action="store_true", help="Also merge same-URL duplicates")
    parser.add_argument("--user-id", default=USER_EMAIL, help="User email to scope operations to")
    args = parser.parse_args()

    dry_run = not args.apply

    if not args.user_id:
        print("[!] Provide --user-id or set USER_EMAIL env var")
        sys.exit(1)

    cloud = get_cloud()
    print(f"Target user: {args.user_id}")
    print()

    phase_a_stamp(cloud, args.user_id, dry_run=dry_run)
    if args.collapse:
        phase_b_collapse(cloud, args.user_id, dry_run=dry_run)

    if dry_run:
        print()
        print("This was a dry run. Re-run with --apply to make changes.")
        print("  python backfill_dedup.py --apply")
        print("  python backfill_dedup.py --apply --collapse")


if __name__ == "__main__":
    main()
