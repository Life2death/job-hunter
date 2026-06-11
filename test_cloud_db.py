"""
test_cloud_db.py
Regression tests for CloudDB.save_results — the daily extraction must never
overwrite a user-set status (applied/manual_apply/...) on re-scraped jobs.

Uses a fake in-memory Supabase client; no network.
"""

from datetime import date

import pytest
from cloud_db import CloudDB, LOOKUP_CHUNK


# ─── Fake Supabase client ────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, data):
        self.data = data


class FakeSelect:
    def __init__(self, table, cols):
        self.table = table
        self.filters = []   # (op, col, val)

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        self.table.log.append(("lookup_chunk", col, len(vals)))
        return self

    def execute(self):
        if self.table.fail_lookups:
            raise RuntimeError("simulated lookup failure")
        out = []
        for row in self.table.store.values():
            ok = True
            for op, col, val in self.filters:
                if op == "eq" and row.get(col) != val:
                    ok = False
                elif op == "in" and row.get(col) not in val:
                    ok = False
            if ok:
                out.append(dict(row))
        return FakeResp(out)


class FakeInsert:
    def __init__(self, table, row):
        self.table = table
        self.row = row

    def execute(self):
        jid = self.row["job_id"]
        if jid in self.table.store:
            raise RuntimeError(f"duplicate key value violates unique constraint: {jid}")
        self.table.store[jid] = dict(self.row)
        self.table.log.append(("insert", jid))
        return FakeResp([self.row])


class FakeUpdate:
    def __init__(self, table, data):
        self.table = table
        self.data = data
        self.filters = []

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def execute(self):
        updated = []
        for row in self.table.store.values():
            if all(row.get(col) == val for col, val in self.filters):
                row.update(self.data)
                updated.append(dict(row))
        self.table.log.append(("update", self.data, dict(self.filters)))
        return FakeResp(updated)


class FakeTable:
    def __init__(self):
        self.store = {}      # job_id -> row dict
        self.log = []
        self.fail_lookups = False

    def select(self, cols):
        return FakeSelect(self, cols)

    def insert(self, row):
        return FakeInsert(self, row)

    def update(self, data):
        return FakeUpdate(self, data)


class FakeClient:
    def __init__(self):
        self._table = FakeTable()

    def table(self, name):
        return self._table


USER = "vikram.panmand@gmail.com"


def make_db():
    db = CloudDB.__new__(CloudDB)   # bypass __init__ (env vars / real client)
    db.user_id = USER
    db.client = FakeClient()
    return db, db.client._table


def scraped_job(jid="nk_190526028484", fit=80,
                url="https://www.naukri.com/job-listings-190526028484"):
    return {
        "job_id": jid,
        "track": "director",
        "portal": "Naukri",
        "title": "Director Engineering",
        "company": "Acme",
        "location": "Mumbai",
        "url": url,
        "fit": fit,
    }


def seed_applied(table, jid="nk_190526028484",
                 canon_url="https://naukri.com/job-listings-190526028484"):
    table.store[jid] = {
        "job_id": jid,
        "user_id": USER,
        "canon_url": canon_url,
        "status": "applied",
        "applied_date": "2026-06-10",
        "imported_date": "2026-06-09",
        "last_seen_date": "2026-06-10",
        "fit": 75,
        "scores_json": "{}",
    }


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestStatusPreservation:
    def test_rescraped_applied_job_keeps_status(self):
        db, table = make_db()
        seed_applied(table)
        db.save_results([scraped_job()])
        row = table.store["nk_190526028484"]
        assert row["status"] == "applied"
        assert row["applied_date"] == "2026-06-10"
        assert row["imported_date"] == "2026-06-09"
        assert row["last_seen_date"] == str(date.today())
        assert not any(op == "insert" for op, *_ in table.log)

    def test_update_payload_never_contains_user_columns(self):
        db, table = make_db()
        seed_applied(table)
        db.save_results([scraped_job()])
        updates = [data for op, data, *_ in table.log if op == "update"]
        assert updates, "expected an update for the existing row"
        for data in updates:
            assert "status" not in data
            assert "applied_date" not in data
            assert "imported_date" not in data

    def test_job_id_fallback_match_keeps_status(self):
        """Existing row has no canon_url (pre-backfill) — matched by job_id."""
        db, table = make_db()
        seed_applied(table, canon_url="")
        db.save_results([scraped_job()])
        assert table.store["nk_190526028484"]["status"] == "applied"
        assert not any(op == "insert" for op, *_ in table.log)


class TestFailClosed:
    def test_lookup_failure_aborts_save(self):
        db, table = make_db()
        seed_applied(table)
        table.fail_lookups = True
        saved = db.save_results([scraped_job(), scraped_job("nk_999", url="https://naukri.com/job-listings-999")])
        assert saved == 0
        writes = [e for e in table.log if e[0] in ("insert", "update")]
        assert writes == []
        assert table.store["nk_190526028484"]["status"] == "applied"

    def test_insert_conflict_does_not_overwrite(self):
        """Row exists but both lookups missed it (race) — insert fails, row untouched."""
        db, table = make_db()
        seed_applied(table)
        # Hide the row from lookups by mismatching user_id
        table.store["nk_190526028484"]["user_id"] = "someone@else.com"
        db.save_results([scraped_job()])
        assert table.store["nk_190526028484"]["status"] == "applied"


class TestChunkingAndInsert:
    def test_lookups_are_chunked(self):
        db, table = make_db()
        n = LOOKUP_CHUNK * 2 + 5
        jobs = [scraped_job(f"nk_{i}", url=f"https://naukri.com/job-listings-{i}")
                for i in range(n)]
        db.save_results(jobs)
        chunks = [size for op, col, size in
                  [e for e in table.log if e[0] == "lookup_chunk"]]
        assert chunks, "expected chunked lookups"
        assert all(size <= LOOKUP_CHUNK for size in chunks)
        assert len([s for s in chunks if s == LOOKUP_CHUNK]) >= 2

    def test_new_job_inserted_as_not_applied(self):
        db, table = make_db()
        saved = db.save_results([scraped_job("nk_new", url="https://naukri.com/job-listings-new")])
        assert saved == 1
        row = table.store["nk_new"]
        assert row["status"] == "not_applied"
        assert row["user_id"] == USER
        assert row["imported_date"] == row["last_seen_date"]
