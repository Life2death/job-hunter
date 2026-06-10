"""
test_dedup.py
Unit tests for dedup.py — canonical_url and normalize_job_key.
"""

import pytest
from dedup import canonical_url, normalize_job_key, job_identity


class TestCanonicalUrl:
    def test_strips_www(self):
        assert canonical_url("https://www.naukri.com/job-listings-123") == \
               "https://naukri.com/job-listings-123"

    def test_lowercases_host(self):
        assert canonical_url("https://NAUKRI.com/Job") == \
               "https://naukri.com/Job"

    def test_strips_query_string(self):
        assert canonical_url("https://naukri.com/job?ref=123&page=2") == \
               "https://naukri.com/job"

    def test_strips_fragment(self):
        assert canonical_url("https://naukri.com/job#section") == \
               "https://naukri.com/job"

    def test_strips_trailing_slash(self):
        assert canonical_url("https://naukri.com/job/") == \
               "https://naukri.com/job"

    def test_http_becomes_https_preserved(self):
        assert canonical_url("http://naukri.com/job").startswith("http://")

    def test_empty_string(self):
        assert canonical_url("") == ""

    def test_none_string(self):
        assert canonical_url(None) == ""

    def test_linkedin_url(self):
        assert canonical_url("https://www.linkedin.com/jobs/view/12345") == \
               "https://linkedin.com/jobs/view/12345"

    def test_indeed_url(self):
        assert canonical_url("https://in.indeed.com/viewjob?jk=abc123") == \
               "https://in.indeed.com/viewjob"

    def test_naukri_and_nk_same_url(self):
        """Bare job_id and nk_ prefixed produce same canonical_url."""
        url = "https://www.naukri.com/job-listings-050626014919"
        expected = "https://naukri.com/job-listings-050626014919"
        assert canonical_url(url) == expected


class TestNormalizeJobKey:
    def test_strips_nk_prefix(self):
        assert normalize_job_key({"job_id": "nk_050626014919"}) == "050626014919"

    def test_strips_li_prefix(self):
        assert normalize_job_key({"job_id": "li_abc123"}) == "abc123"

    def test_strips_adz_prefix(self):
        assert normalize_job_key({"job_id": "adz_xyz"}) == "xyz"

    def test_strips_fi_prefix(self):
        assert normalize_job_key({"job_id": "fi_789"}) == "789"

    def test_strips_iim_prefix_and_page(self):
        assert normalize_job_key({"job_id": "iim_123_2"}) == "123"

    def test_iim_without_page(self):
        assert normalize_job_key({"job_id": "iim_456"}) == "456"

    def test_bare_id_unchanged(self):
        assert normalize_job_key({"job_id": "050626014919"}) == "050626014919"

    def test_empty_job_id(self):
        assert normalize_job_key({"job_id": ""}) == ""

    def test_missing_job_id(self):
        assert normalize_job_key({}) == ""


class TestJobIdentity:
    def test_prefers_url(self):
        job = {"url": "https://naukri.com/job-123", "job_id": "nk_123"}
        assert job_identity(job) == "https://naukri.com/job-123"

    def test_falls_back_to_normalized_key(self):
        job = {"url": "", "job_id": "nk_050626014919"}
        assert job_identity(job) == "050626014919"

    def test_bare_and_nk_same_identity_via_url(self):
        """Both produce identical canonical_url → same identity."""
        job_a = {"url": "https://www.naukri.com/job-listings-050626014919", "job_id": "050626014919"}
        job_b = {"url": "https://www.naukri.com/job-listings-050626014919", "job_id": "nk_050626014919"}
        assert job_identity(job_a) == job_identity(job_b)
