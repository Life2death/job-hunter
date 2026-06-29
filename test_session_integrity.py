"""
Session integrity regression tests for the blank-dashboard fix.

A session carrying user_id but missing email (stale cookie, partial login)
must never render a silent all-zeros dashboard — it must redirect to /login.

Uses Flask test client with session_transaction() — no network, no DB.
"""
import os
import pytest
from web_app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        yield c


def set_session(client, sess_data):
    """Write session data via session_transaction."""
    with client.session_transaction() as sess:
        sess.update(sess_data)


def clear_session(client):
    with client.session_transaction() as sess:
        sess.clear()


class TestAuthGate:
    """check_auth must bounce partial sessions before they reach any page."""

    def test_full_session_allows_access(self, client):
        """Happy path: user_id + email → auth gate passes."""
        set_session(client, {"user_id": "uid-123", "email": "a@b.com"})
        resp = client.get("/")
        # Dashboard may still error on missing Supabase, but should NOT redirect
        assert resp.status_code != 302
        assert resp.location is None or "login" not in (resp.location or "")

    def test_no_session_redirects_to_login(self, client):
        """No session at all → redirect /login."""
        clear_session(client)
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.location

    def test_partial_session_only_user_id_redirects(self, client):
        """user_id but no email (stale cookie) → redirect /login."""
        set_session(client, {"user_id": "uid-123"})
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.location

    def test_partial_session_clears_cookie(self, client):
        """Partial session is cleared so it doesn't persist."""
        set_session(client, {"user_id": "uid-123"})
        client.get("/")
        with client.session_transaction() as sess:
            assert "user_id" not in sess
            assert "email" not in sess


class TestLoginPage:
    """Login page must NOT trap a partial session."""

    def test_full_session_redirects_away_from_login(self, client):
        """Already logged in → /login redirects to /."""
        set_session(client, {"user_id": "uid-123", "email": "a@b.com"})
        resp = client.get("/login")
        assert resp.status_code == 302
        assert resp.location == "/"

    def test_partial_session_can_reach_login_form(self, client):
        """user_id only (no email) → /login shows form, NOT redirected."""
        set_session(client, {"user_id": "uid-123"})
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "Log In" in resp.data.decode()

    def test_no_session_shows_login_form(self, client):
        """No session at all → /login shows form."""
        clear_session(client)
        resp = client.get("/login")
        assert resp.status_code == 200


class TestEmptyStateBanners:
    """Dashboard banners for unconfigured / empty states."""

    def test_no_supabase_shows_not_configured_banner(self, client):
        """When SUPABASE_URL/KEY are unset, show 'not configured' banner."""
        set_session(client, {"user_id": "uid-123", "email": "a@b.com"})
        resp = client.get("/")
        body = resp.data.decode()
        assert "not configured" in body.lower() or "Database connection" in body

    def test_no_jobs_shows_empty_banner(self, client, monkeypatch):
        """When logged in but no data, show 'no jobs found' banner.

        Monkey-patch get_cloud to return a stub with no rows so we hit
        the 'fetched 0 rows' code path without real Supabase.
        """
        class FakeTable:
            def select(self, cols):
                return self
            def eq(self, col, val):
                return self
            def neq(self, col, val):
                return self
            def order(self, col):
                return self
            def range(self, a, b):
                return self
            def execute(self):
                return type("FakeResp", (), {"data": []})()

        class FakeCloud:
            def table(self, name):
                return FakeTable()

        monkeypatch.setattr("web_app.get_cloud", lambda: FakeCloud())

        set_session(client, {"user_id": "uid-123", "email": "a@b.com"})
        resp = client.get("/")
        body = resp.data.decode()
        assert "no jobs found" in body.lower()


def test_logout_clears_session(client):
    """/logout clears the session entirely."""
    set_session(client, {"user_id": "uid-123", "email": "a@b.com"})
    resp = client.get("/logout")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "user_id" not in sess
        assert "email" not in sess
