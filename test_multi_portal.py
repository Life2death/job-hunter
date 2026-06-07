"""
Tests for multi_portal_job_hunter.py

Run:  python -m pytest test_multi_portal.py -v
      python -m pytest test_multi_portal.py -v --skip-integration  (skip portal HTTP calls)
"""

import sys, io, json, time, sqlite3
from pathlib import Path
from datetime import date, datetime

# ── ensure we can import the main module ──────────────────────────────────────
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_job(overrides: dict = None) -> dict:
    base = {
        "job_id": "test_1", "portal": "Test", "title": "Test Job",
        "company": "Test Corp", "location": "Mumbai, India",
        "posted_date": "2026-06-05", "salary_min": 5_000_000,
        "salary_max": 7_000_000, "description": "A great job",
        "url": "https://example.com/job/1",
    }
    if overrides:
        base.update(overrides)
    return base


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fix_today(monkeypatch):
    """Pin TODAY to a fixed date so freshness tests are deterministic."""
    import multi_portal_job_hunter as mh
    monkeypatch.setattr(mh, "TODAY", date(2026, 6, 6))


# ══════════════════════════════════════════════════════════════════════════════
#  Unit: score_job
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreJob:
    def _score(self, track, overrides=None):
        from multi_portal_job_hunter import score_job
        return score_job(_make_job(overrides), track)

    # ── SM track ──────────────────────────────────────────────────────────────
    def test_sm_release_train(self):
        _, total = self._score("SM", {"title": "Release Train Engineer"})
        assert total >= 40

    def test_sm_scrum_master(self):
        _, total = self._score("SM", {"title": "Senior Scrum Master"})
        assert total >= 35

    def test_sm_basic(self):
        _, total = self._score("SM", {"title": "Scrum Master"})
        assert total >= 30

    def test_sm_unknown_role_gets_low_score(self):
        _, total = self._score("SM", {"title": "Software Engineer", "salary_min": 0,
                                       "location": "Bhopal", "company": "Unknown"})
        assert total < 35

    # ── PM track ──────────────────────────────────────────────────────────────
    def test_pm_senior_program(self):
        _, total = self._score("PM", {"title": "Senior Program Manager"})
        assert total >= 35

    def test_pm_program_manager(self):
        _, total = self._score("PM", {"title": "Program Manager"})
        assert total >= 30

    def test_pm_project_manager(self):
        _, total = self._score("PM", {"title": "Project Manager"})
        assert total >= 25

    def test_pm_low_role(self):
        _, total = self._score("PM", {"title": "Clerk", "salary_min": 0,
                                       "location": "Bhopal", "company": "Unknown"})
        assert total < 35

    # ── DIR track ─────────────────────────────────────────────────────────────
    def test_dir_vp(self):
        _, total = self._score("DIR", {"title": "VP of Engineering"})
        assert total >= 40

    def test_dir_director(self):
        _, total = self._score("DIR", {"title": "Director of Engineering"})
        assert total >= 35

    def test_dir_cto(self):
        _, total = self._score("DIR", {"title": "CTO"})
        assert total >= 40

    def test_dir_low_role(self):
        _, total = self._score("DIR", {"title": "Intern", "salary_min": 0,
                                        "location": "Bhopal", "company": "Unknown"})
        assert total < 35

    # ── Shared factors ───────────────────────────────────────────────────────
    def test_high_salary_boosts_score(self):
        _, total_high = self._score("PM", {"salary_min": 10_000_000})
        _, total_low  = self._score("PM", {"salary_min": 500_000})
        assert total_high > total_low

    def test_good_location(self):
        _, good = self._score("PM", {"location": "Mumbai, India"})
        _, bad  = self._score("PM", {"location": "Bhopal, India"})
        assert good >= bad

    def test_tier1_company_bonus(self):
        _, t1 = self._score("PM", {"company": "JPMorgan Chase"})
        _, co = self._score("PM", {"company": "Unknown Startup"})
        assert t1 > co


# ══════════════════════════════════════════════════════════════════════════════
#  Unit: freshness
# ══════════════════════════════════════════════════════════════════════════════

class TestFreshness:
    def _fr(self, s):
        from multi_portal_job_hunter import freshness
        return freshness(s)

    def test_empty(self):
        tag, pen, age = self._fr("")
        assert tag == "UNKNOWN" and pen == -10

    def test_iso_date_today(self):
        tag, _, age = self._fr("2026-06-06")
        assert tag == "FRESH" and age == 0

    def test_iso_date_fresh(self):
        tag, _, age = self._fr("2026-06-04")
        assert tag == "FRESH" and age <= 3

    def test_iso_date_aging(self):
        tag, _, age = self._fr("2026-06-01")
        assert tag == "AGING" and 3 < age <= 7

    def test_iso_date_stale(self):
        tag, pen, age = self._fr("2026-05-01")
        assert tag == "STALE" and pen is None

    def test_human_just_now(self):
        tag, pen, age = self._fr("Just posted")
        assert tag == "FRESH" and age == 0

    def test_human_yesterday(self):
        tag, pen, age = self._fr("Yesterday")
        assert tag == "FRESH" and age == 1

    def test_human_3_days(self):
        tag, _, age = self._fr("3 days ago")
        assert tag in ("FRESH", "AGING")

    def test_human_2_weeks(self):
        tag, pen, age = self._fr("2 weeks ago")
        assert tag in ("AGING", "STALE")

    def test_human_month(self):
        tag, pen, age = self._fr("1 month ago")
        assert tag == "STALE" and pen is None and age == 99

    def test_unknown(self):
        tag, pen, age = self._fr("xyzzy")
        assert tag == "UNKNOWN" and pen == -10

    def test_int_timestamp(self):
        ts = int(datetime(2026, 6, 4).timestamp() * 1000)
        tag, _, age = self._fr(str(ts))
        assert tag == "FRESH" and age <= 3


# ══════════════════════════════════════════════════════════════════════════════
#  Unit: _cookie
# ══════════════════════════════════════════════════════════════════════════════

class TestCookie:
    def test_returns_empty_when_no_file_no_config(self, monkeypatch, tmp_path):
        import multi_portal_job_hunter as mh
        monkeypatch.setattr(mh, "__file__", str(tmp_path / "dummy.py"))
        monkeypatch.setattr("multi_portal_job_hunter.CONFIG", {})
        assert mh._cookie("nonexistent") == ""

    def test_reads_from_dot_file(self, tmp_path):
        dot = tmp_path / ".myportal_cookie.txt"
        dot.write_text("secret123", encoding="utf-8")
        monkeypatch = pytest.MonkeyPatch()
        import multi_portal_job_hunter as mh
        monkeypatch.setattr(mh, "__file__", str(tmp_path / "dummy.py"))
        monkeypatch.setattr("multi_portal_job_hunter.CONFIG", {"cookies": {}})
        assert mh._cookie("myportal") == "secret123"
        monkeypatch.undo()

    def test_strips_prefix_1st(self, tmp_path):
        dot = tmp_path / ".myportal_cookie.txt"
        dot.write_text("1st --realvalue", encoding="utf-8")
        monkeypatch = pytest.MonkeyPatch()
        import multi_portal_job_hunter as mh
        monkeypatch.setattr(mh, "__file__", str(tmp_path / "dummy.py"))
        monkeypatch.setattr("multi_portal_job_hunter.CONFIG", {"cookies": {}})
        assert mh._cookie("myportal") == "realvalue"
        monkeypatch.undo()

    def test_strips_prefix_dash(self, tmp_path):
        dot = tmp_path / ".myportal_cookie.txt"
        dot.write_text("--realvalue", encoding="utf-8")
        monkeypatch = pytest.MonkeyPatch()
        import multi_portal_job_hunter as mh
        monkeypatch.setattr(mh, "__file__", str(tmp_path / "dummy.py"))
        monkeypatch.setattr("multi_portal_job_hunter.CONFIG", {"cookies": {}})
        assert mh._cookie("myportal") == "realvalue"
        monkeypatch.undo()


# ══════════════════════════════════════════════════════════════════════════════
#  Unit: save_results / MultiPortalDB
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiPortalDB:
    def test_save_and_count(self, tmp_path):
        from multi_portal_job_hunter import MultiPortalDB
        db = MultiPortalDB(tmp_path / "test.db")
        results = [
            {"job_id": "1", "track": "PM", "portal": "LinkedIn",
             "title": "PM", "company": "Co", "location": "Mumbai",
             "salary": "", "posted": "", "url": "", "fit": 50,
             "freshness": "FRESH", "scores_json": "{}"},
        ]
        n = db.save_results(results)
        assert n > 0

    def test_duplicate_ignored(self, tmp_path):
        from multi_portal_job_hunter import MultiPortalDB
        db = MultiPortalDB(tmp_path / "test.db")
        r = {"job_id": "dup1", "track": "PM", "portal": "LinkedIn",
             "title": "PM", "company": "Co", "location": "Mumbai",
             "salary": "", "posted": "", "url": "", "fit": 50,
             "freshness": "FRESH", "scores_json": "{}"}
        assert db.save_results([r]) > 0
        n2 = db.save_results([r])
        assert n2 == 0   # total_changes won't change; OR IGNORE works

    def test_search_filters(self, tmp_path):
        from multi_portal_job_hunter import MultiPortalDB
        db = MultiPortalDB(tmp_path / "test.db")
        base = {"title": "T", "company": "C", "location": "",
                "salary": "", "posted": "", "url": "", "fit": 50,
                "freshness": "FRESH", "scores_json": "{}"}
        db.save_results([dict(base, job_id="a", track="PM", portal="LinkedIn")])
        db.save_results([dict(base, job_id="b", track="DIR", portal="Indeed")])
        db.save_results([dict(base, job_id="c", track="PM", portal="Indeed")])

        # Search PM only
        rows = db.search(track="PM")
        ids = {r[0] for r in rows}
        assert ids == {"a", "c"}

        # Search filtered by portal
        rows = db.search(track="PM", portal="LinkedIn")
        assert len(rows) == 1 and rows[0][0] == "a"


# ══════════════════════════════════════════════════════════════════════════════
#  Integration: portal fetchers (skip with --skip-integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestPortalFetchers:
    """Lightweight sanity checks: fetch 1 page, verify result shape."""

    MIN_KEYS = {"job_id", "portal", "title", "company", "location",
                "posted_date", "salary_min", "salary_max", "description", "url"}

    def _check_result(self, job):
        assert isinstance(job, dict)
        assert self.MIN_KEYS.issubset(job.keys()), f"Missing keys: {self.MIN_KEYS - job.keys()}"
        assert job["job_id"]
        assert job["title"]
        assert job["portal"]

    @pytest.mark.integration
    def test_linkedin(self):
        from multi_portal_job_hunter import fetch_linkedin
        jobs = fetch_linkedin("Scrum Master", "Mumbai", pages=1)
        assert isinstance(jobs, list)
        if jobs:
            self._check_result(jobs[0])

    @pytest.mark.integration
    def test_indeed(self):
        from multi_portal_job_hunter import fetch_indeed
        jobs = fetch_indeed("Scrum Master", "Mumbai", pages=1)
        assert isinstance(jobs, list)
        if jobs:
            self._check_result(jobs[0])

    @pytest.mark.integration
    def test_foundit(self):
        from multi_portal_job_hunter import fetch_foundit
        jobs = fetch_foundit("Scrum Master", "Mumbai", pages=1)
        assert isinstance(jobs, list)
        if jobs:
            self._check_result(jobs[0])

    @pytest.mark.integration
    def test_iimjobs(self):
        from multi_portal_job_hunter import fetch_iimjobs
        jobs = fetch_iimjobs("Product Manager", "Mumbai", pages=1)
        assert isinstance(jobs, list)
        if jobs:
            self._check_result(jobs[0])


# ══════════════════════════════════════════════════════════════════════════════
#  Smoke: run(RUN) with test_mode produces correct structure
# ══════════════════════════════════════════════════════════════════════════════

class TestPipeline:
    @pytest.mark.integration
    def test_run_returns_ranked_results(self):
        from multi_portal_job_hunter import run
        results = run(["SM"], ["linkedin"], test_mode=True)
        assert isinstance(results, list)
        if results:
            assert "fit" in results[0]
            assert "track" in results[0]
            # verify sorted descending
            fits = [r["fit"] for r in results]
            assert fits == sorted(fits, reverse=True)
