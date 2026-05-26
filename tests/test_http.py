"""Tests for query_directory retry behavior and fr_datetime tz conversion."""
from datetime import datetime, timezone
from unittest import mock

import pytest

import generate_peppol_report as m


class _FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(m, "HTTP_RETRY_BACKOFF_S", (0.0, 0.0, 0.0))


class TestQueryDirectoryRetry:
    def test_5xx_then_success(self):
        calls = {"n": 0}
        def fake_get(url, **kw):
            calls["n"] += 1
            return _FakeResp(503) if calls["n"] < 3 else _FakeResp(200, {"ok": 1})
        with mock.patch.object(m.requests, "get", fake_get):
            assert m.query_directory("urn:foo", country="FR") == {"ok": 1}
        assert calls["n"] == 3

    def test_429_is_retried(self):
        calls = {"n": 0}
        def fake_get(url, **kw):
            calls["n"] += 1
            return _FakeResp(429) if calls["n"] < 2 else _FakeResp(200, {"ok": 1})
        with mock.patch.object(m.requests, "get", fake_get):
            assert m.query_directory("urn:foo", country="FR") == {"ok": 1}
        assert calls["n"] == 2  # retried once before success

    def test_404_is_not_retried(self):
        calls = {"n": 0}
        def fake_get(url, **kw):
            calls["n"] += 1
            return _FakeResp(404)
        with mock.patch.object(m.requests, "get", fake_get):
            with pytest.raises(RuntimeError, match="404"):
                m.query_directory("urn:foo", country="FR")
        assert calls["n"] == 1

    def test_request_exception_is_retried(self):
        calls = {"n": 0}
        def fake_get(url, **kw):
            calls["n"] += 1
            if calls["n"] < 2:
                raise m.requests.ConnectionError("boom")
            return _FakeResp(200, {"ok": 1})
        with mock.patch.object(m.requests, "get", fake_get):
            assert m.query_directory("urn:foo", country="FR") == {"ok": 1}

    def test_exhausts_retries(self):
        calls = {"n": 0}
        def fake_get(url, **kw):
            calls["n"] += 1
            return _FakeResp(503)
        with mock.patch.object(m.requests, "get", fake_get):
            with pytest.raises(RuntimeError):
                m.query_directory("urn:foo", country="FR")
        assert calls["n"] == m.HTTP_RETRY_ATTEMPTS

    def test_malformed_json_is_retried(self):
        # HTTP 200 with invalid body (e.g. truncated, HTML error page from a proxy)
        class BadJSON:
            status_code = 200
            def json(self):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
        calls = {"n": 0}
        def fake_get(url, **kw):
            calls["n"] += 1
            return BadJSON() if calls["n"] < 2 else _FakeResp(200, {"ok": 1})
        with mock.patch.object(m.requests, "get", fake_get):
            assert m.query_directory("urn:foo", country="FR") == {"ok": 1}
        assert calls["n"] == 2


class TestFrDatetime:
    def test_naive_assumed_paris(self):
        # 1er juillet → CEST
        out = m.fr_datetime(datetime(2026, 7, 1, 9, 0))
        assert "CEST" in out and "09:00" in out

    def test_paris_aware_preserved(self):
        out = m.fr_datetime(datetime(2026, 1, 15, 9, 0, tzinfo=m.PARIS_TZ))
        assert "CET" in out and "09:00" in out

    def test_utc_converted_to_paris(self):
        # 07:00 UTC = 09:00 Paris (CEST) le 1er juillet
        out = m.fr_datetime(datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc))
        assert "09:00" in out
        assert "CEST" in out

    def test_utc_converted_in_winter(self):
        # 08:00 UTC = 09:00 Paris (CET) le 15 janvier
        out = m.fr_datetime(datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc))
        assert "09:00" in out
        assert "CET" in out
