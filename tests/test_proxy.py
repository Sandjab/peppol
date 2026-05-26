"""Tests for the proxy parsing/building helpers."""
import pytest

from generate_peppol_report import _build_proxy_url, _normalize_proxy_host


class TestNormalizeProxyHost:
    def test_prepends_http_scheme_when_missing(self):
        assert _normalize_proxy_host("proxy.corp:8080") == "http://proxy.corp:8080"

    def test_preserves_explicit_https(self):
        assert _normalize_proxy_host("https://proxy.corp:8080") == "https://proxy.corp:8080"

    def test_strips_whitespace(self):
        assert _normalize_proxy_host("  proxy.corp:8080  ") == "http://proxy.corp:8080"

    @pytest.mark.parametrize("bad", ["", "   ", "\t"])
    def test_rejects_empty(self, bad):
        with pytest.raises(ValueError, match="vide"):
            _normalize_proxy_host(bad)

    @pytest.mark.parametrize("bad", ["http://", "https://"])
    def test_rejects_missing_host(self, bad):
        with pytest.raises(ValueError, match="hôte"):
            _normalize_proxy_host(bad)

    @pytest.mark.parametrize("bad", [
        "user:pass@proxy.corp:8080",
        "http://user:pass@proxy.corp:8080",
        "user@proxy.corp",
    ])
    def test_rejects_inline_credentials(self, bad):
        with pytest.raises(ValueError, match="credentials inline"):
            _normalize_proxy_host(bad)


class TestBuildProxyUrl:
    def test_no_auth_returns_host_unchanged(self):
        assert _build_proxy_url("http://proxy.corp:8080", "", "") == "http://proxy.corp:8080"

    def test_user_and_password(self):
        assert _build_proxy_url("http://h:8080", "alice", "s3cret") == "http://alice:s3cret@h:8080"

    def test_user_only_omits_colon(self):
        # regression: previous version produced 'http://alice:@h:8080'
        assert _build_proxy_url("http://h:8080", "alice", "") == "http://alice@h:8080"

    def test_password_without_user_dropped(self):
        # invariant enforced upstream; helper must not invent a leading ':'
        assert _build_proxy_url("http://h:8080", "", "secret") == "http://h:8080"

    def test_url_encodes_special_chars(self):
        url = _build_proxy_url("http://h:8080", "u@x", "p:!/")
        assert url == "http://u%40x:p%3A%21%2F@h:8080"

    def test_preserves_https_scheme(self):
        assert _build_proxy_url("https://h:443", "a", "b") == "https://a:b@h:443"
