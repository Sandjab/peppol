"""Tests for SML lookup and per-SMP coverage aggregation."""
import hashlib
from unittest import mock

import generate_peppol_report as m


class TestCanonicalParticipantId:
    def test_dict_form_returns_scheme_value(self):
        pid = {"scheme": "iso6523-actorid-upis", "value": "0225:920227972"}
        assert m._canonical_participant_id(pid) == "iso6523-actorid-upis::0225:920227972"

    def test_dict_form_is_lowercased(self):
        pid = {"scheme": "ISO6523-actorid-upis", "value": "0225:SIREN:519840501"}
        assert m._canonical_participant_id(pid) == "iso6523-actorid-upis::0225:siren:519840501"

    def test_string_with_scheme_prefix(self):
        s = "iso6523-actorid-upis::0225:siren:519840501"
        assert m._canonical_participant_id(s) == "iso6523-actorid-upis::0225:siren:519840501"

    def test_string_uppercase_is_lowercased(self):
        s = "ISO6523-ACTORID-UPIS::0225:SIREN:519840501"
        assert m._canonical_participant_id(s) == "iso6523-actorid-upis::0225:siren:519840501"

    def test_string_without_scheme_is_rejected(self):
        # Canonical form requires both halves; ambiguous value-only strings
        # are rejected rather than guessed.
        assert m._canonical_participant_id("0225:siren:519840501") is None

    def test_missing_or_empty(self):
        assert m._canonical_participant_id({"scheme": "x", "value": ""}) is None
        assert m._canonical_participant_id({"scheme": "", "value": "x"}) is None
        assert m._canonical_participant_id({"value": "x"}) is None
        assert m._canonical_participant_id({"scheme": "x"}) is None
        assert m._canonical_participant_id({}) is None
        assert m._canonical_participant_id(None) is None  # type: ignore
        assert m._canonical_participant_id("") is None
        assert m._canonical_participant_id("foo::") is None
        assert m._canonical_participant_id("::bar") is None


class TestSmlFqdn:
    def test_format_matches_peppol_spec_full_canonical_form(self):
        """FQDN must be B-{md5_hex_of_lowercase_canonical_id}.{scheme}.{SML zone}.

        Per Peppol Policy for use of Identifiers v4 §4 the MD5 input is the
        full "scheme::value" canonical form, not just the value. The SML
        zone is the in-house OpenPeppol Production zone (post-2026 migration).
        """
        canonical = "iso6523-actorid-upis::0225:920227972"
        expected_hash = hashlib.md5(canonical.encode("utf-8")).hexdigest()
        fqdn = m._sml_fqdn(canonical)
        assert fqdn.lower() == (
            f"b-{expected_hash}.iso6523-actorid-upis."
            f"participant.sml.prod.tech.peppol.org"
        )

    def test_known_hash_for_real_participant(self):
        # Regression test pinning the spec-correct hash for a real French
        # participant — this exact MD5 is what the SML responds to.
        canonical = "iso6523-actorid-upis::0225:920227972"
        fqdn = m._sml_fqdn(canonical)
        assert fqdn == (
            "B-05ae1c242563ef99b43c016365a517a0"
            ".iso6523-actorid-upis.participant.sml.prod.tech.peppol.org"
        )

    def test_scheme_from_input_appears_in_fqdn(self):
        # The DNS scheme label comes from the canonical input, not a hardcoded
        # constant — important if Peppol ever extends to other scheme labels.
        fqdn = m._sml_fqdn("other-scheme::abc")
        assert ".other-scheme." in fqdn


class TestSmpRootFromHostname:
    def test_simple_hostname(self):
        assert m._smp_root_from_hostname("docaposte.fr") == "docaposte.fr"

    def test_subdomain_collapses(self):
        assert m._smp_root_from_hostname("smp-prod.docaposte.fr") == "docaposte.fr"
        assert m._smp_root_from_hostname("ap1.eu-west.example-smp.com") == "example-smp.com"

    def test_trailing_dot_stripped(self):
        assert m._smp_root_from_hostname("smp.example.com.") == "example.com"

    def test_lowercased(self):
        assert m._smp_root_from_hostname("SMP.EXAMPLE.COM") == "example.com"


class TestParticipantSmpRoot:
    def test_returns_none_on_gaierror(self, monkeypatch):
        def fake_lookup(name):
            raise __import__("socket").gaierror("no such name")
        monkeypatch.setattr(m.socket, "gethostbyname_ex", fake_lookup)
        result = m.participant_smp_root({"scheme": "iso6523-actorid-upis",
                                         "value": "0225:siren:000000000"})
        assert result is None

    def test_returns_root_on_successful_cname(self, monkeypatch):
        def fake_lookup(name):
            return ("smp-prod.docaposte.fr", [], ["1.2.3.4"])
        monkeypatch.setattr(m.socket, "gethostbyname_ex", fake_lookup)
        result = m.participant_smp_root("iso6523-actorid-upis::0225:siren:1")
        assert result == "docaposte.fr"

    def test_drops_results_still_inside_sml_zone(self, monkeypatch):
        # If the CNAME chain didn't escape the SML zone, we don't have a real
        # SMP hostname.
        def fake_lookup(name):
            return (f"b-deadbeef.{m.SML_SCHEME_LABEL}.{m.SML_BASE_DOMAIN}", [], [])
        monkeypatch.setattr(m.socket, "gethostbyname_ex", fake_lookup)
        result = m.participant_smp_root("iso6523-actorid-upis::0225:siren:2")
        assert result is None

    def test_invalid_participant_id_returns_none_without_dns(self, monkeypatch):
        # Should not even attempt DNS when participant value is invalid.
        called = {"hit": False}
        def fake_lookup(name):
            called["hit"] = True
            return ("x", [], [])
        monkeypatch.setattr(m.socket, "gethostbyname_ex", fake_lookup)
        assert m.participant_smp_root({}) is None
        assert called["hit"] is False


class TestCollectSmpCoverage:
    def _match(self, value: str) -> dict:
        return {"participantID": {"scheme": "iso6523-actorid-upis", "value": value}}

    def test_aggregates_distinct_participants_per_doctype(self, monkeypatch):
        # 4 participants, 2 SMPs:
        #   docaposte.fr serves A & B → on ubl_cius+ubl_ext+facturx
        #   contoso.fr   serves C & D → on ubl_cius only
        smp_map = {
            "iso6523-actorid-upis::0225:siren:a": "docaposte.fr",
            "iso6523-actorid-upis::0225:siren:b": "docaposte.fr",
            "iso6523-actorid-upis::0225:siren:c": "contoso.fr",
            "iso6523-actorid-upis::0225:siren:d": "contoso.fr",
        }
        monkeypatch.setattr(m, "participant_smp_root",
                            lambda v, **kw: smp_map.get(v))
        samples = {
            "ubl_cius": [self._match("0225:siren:a"), self._match("0225:siren:b"),
                         self._match("0225:siren:c"), self._match("0225:siren:d")],
            "ubl_ext":  [self._match("0225:siren:a"), self._match("0225:siren:b")],
            "cii_cius": [],
            "cii_ext":  [],
            "facturx":  [self._match("0225:siren:a"), self._match("0225:siren:b")],
            "cdar":     [],
        }
        result = m.collect_smp_coverage(samples)
        smps = {s["root"]: s for s in result["smps"]}
        assert smps["docaposte.fr"]["total_observed"] == 2
        assert smps["docaposte.fr"]["by_doctype"]["ubl_cius"] == 2
        assert smps["docaposte.fr"]["by_doctype"]["ubl_ext"] == 2
        assert smps["docaposte.fr"]["by_doctype"]["facturx"] == 2
        assert smps["docaposte.fr"]["by_doctype"]["cii_cius"] == 0
        assert smps["docaposte.fr"]["doctypes_covered"] == 3
        assert set(smps["docaposte.fr"]["missing"]) == {"cii_cius", "cii_ext", "cdar"}

        assert smps["contoso.fr"]["total_observed"] == 2
        assert smps["contoso.fr"]["doctypes_covered"] == 1
        assert smps["contoso.fr"]["by_doctype"]["ubl_cius"] == 2
        assert smps["contoso.fr"]["by_doctype"]["ubl_ext"] == 0

    def test_sort_order_by_observed_descending(self, monkeypatch):
        smp_map = {
            "iso6523-actorid-upis::0225:a": "big.fr",
            "iso6523-actorid-upis::0225:b": "big.fr",
            "iso6523-actorid-upis::0225:c": "small.fr",
        }
        monkeypatch.setattr(m, "participant_smp_root",
                            lambda v, **kw: smp_map.get(v))
        samples = {k: [] for k in m.DOCTYPES_FR}
        samples["ubl_cius"] = [self._match("0225:a"), self._match("0225:b"),
                               self._match("0225:c")]
        result = m.collect_smp_coverage(samples)
        roots = [s["root"] for s in result["smps"]]
        assert roots == ["big.fr", "small.fr"]

    def test_unresolved_counted_and_excluded(self, monkeypatch):
        smp_map = {
            "iso6523-actorid-upis::0225:a": "ok.fr",
            "iso6523-actorid-upis::0225:b": None,
        }
        monkeypatch.setattr(m, "participant_smp_root",
                            lambda v, **kw: smp_map.get(v))
        samples = {k: [] for k in m.DOCTYPES_FR}
        samples["ubl_cius"] = [self._match("0225:a"), self._match("0225:b")]
        result = m.collect_smp_coverage(samples)
        assert result["unresolved_count"] == 1
        assert result["total_participants"] == 2
        # Only resolved participants end up in the per-SMP table.
        roots = [s["root"] for s in result["smps"]]
        assert roots == ["ok.fr"]

    def test_empty_samples(self):
        result = m.collect_smp_coverage({k: [] for k in m.DOCTYPES_FR})
        assert result["smps"] == []
        assert result["unresolved_count"] == 0
        assert result["total_participants"] == 0

    def test_returns_sml_zone_and_doh_flag(self, monkeypatch):
        result = m.collect_smp_coverage({k: [] for k in m.DOCTYPES_FR})
        assert result["sml_zone"] == m.SML_BASE_DOMAIN
        assert result["used_doh"] == m.USE_DNS_DOH


class _FakeDohResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class TestDohResolveCanonical:
    def _match(self, value: str) -> dict:
        return {"participantID": {"scheme": "iso6523-actorid-upis", "value": value}}

    def test_resolves_canonical_from_cname_then_a(self, monkeypatch):
        fqdn = "b-xxx.iso6523-actorid-upis.edelivery.tech.ec.europa.eu"
        payload = {
            "Status": 0,
            "Answer": [
                {"name": f"{fqdn}.", "type": 5, "data": "smp-prod.docaposte.fr."},
                {"name": "smp-prod.docaposte.fr.", "type": 1, "data": "1.2.3.4"},
            ],
        }
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(200, payload))
        # Chain walk: fqdn → CNAME → smp-prod.docaposte.fr.
        assert m._doh_resolve_canonical(fqdn) == "smp-prod.docaposte.fr"

    def test_multi_hop_cname_chain(self, monkeypatch):
        # fqdn → mid.example. → final-smp.example.com.
        fqdn = "b-yyy.iso6523-actorid-upis.edelivery.tech.ec.europa.eu"
        payload = {
            "Status": 0,
            "Answer": [
                {"name": "final-smp.example.com.", "type": 1, "data": "5.6.7.8"},
                {"name": "mid.example.", "type": 5, "data": "final-smp.example.com."},
                {"name": f"{fqdn}.", "type": 5, "data": "mid.example."},
            ],
        }
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(200, payload))
        assert m._doh_resolve_canonical(fqdn) == "final-smp.example.com"

    def test_ignores_unrelated_records(self, monkeypatch):
        # Resolver returned a sibling A record not part of our chain — must
        # not be picked as canonical.
        fqdn = "b-zzz.iso6523-actorid-upis.edelivery.tech.ec.europa.eu"
        payload = {
            "Status": 0,
            "Answer": [
                {"name": f"{fqdn}.", "type": 5, "data": "smp.example.fr."},
                {"name": "smp.example.fr.", "type": 1, "data": "1.1.1.1"},
                # Unrelated record (additional section leak):
                {"name": "unrelated.example.net.", "type": 1, "data": "9.9.9.9"},
            ],
        }
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(200, payload))
        assert m._doh_resolve_canonical(fqdn) == "smp.example.fr"

    def test_cycle_protection(self, monkeypatch):
        # Pathological CNAME loop — must not hang.
        fqdn = "loop.example"
        payload = {
            "Status": 0,
            "Answer": [
                {"name": "loop.example.", "type": 5, "data": "a.example."},
                {"name": "a.example.",    "type": 5, "data": "loop.example."},
            ],
        }
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(200, payload))
        # Returns one of the names in the cycle, doesn't loop forever.
        result = m._doh_resolve_canonical(fqdn)
        assert result in {"loop.example", "a.example"}

    def test_returns_none_on_nxdomain(self, monkeypatch):
        payload = {"Status": 3, "Answer": []}
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(200, payload))
        assert m._doh_resolve_canonical("x.example") is None

    def test_returns_none_on_non_200(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(503, {}))
        assert m._doh_resolve_canonical("x.example") is None

    def test_returns_none_on_request_exception(self, monkeypatch):
        def boom(*a, **kw):
            raise m.requests.ConnectionError("net down")
        monkeypatch.setattr(m.requests, "get", boom)
        assert m._doh_resolve_canonical("x.example") is None

    def test_returns_none_on_malformed_json(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(200, ValueError("bad")))
        assert m._doh_resolve_canonical("x.example") is None

    def test_returns_none_on_empty_answer(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get",
                            lambda *a, **kw: _FakeDohResp(200, {"Status": 0, "Answer": []}))
        assert m._doh_resolve_canonical("x.example") is None


class TestParticipantSmpRootDohPath:
    def test_uses_doh_when_global_is_set(self, monkeypatch):
        # Switch to DoH; ensure socket.gethostbyname_ex is NOT called.
        socket_called = {"hit": False}
        def fake_socket(*a, **kw):
            socket_called["hit"] = True
            raise AssertionError("should not be called when USE_DNS_DOH=True")
        monkeypatch.setattr(m.socket, "gethostbyname_ex", fake_socket)
        monkeypatch.setattr(m, "_doh_resolve_canonical",
                            lambda fqdn, **kw: "smp.docaposte.fr.")
        monkeypatch.setattr(m, "USE_DNS_DOH", True)
        result = m.participant_smp_root("iso6523-actorid-upis::0225:siren:1")
        assert result == "docaposte.fr"
        assert socket_called["hit"] is False

    def test_doh_failure_yields_none(self, monkeypatch):
        monkeypatch.setattr(m, "_doh_resolve_canonical", lambda fqdn, **kw: None)
        monkeypatch.setattr(m, "USE_DNS_DOH", True)
        assert m.participant_smp_root("iso6523-actorid-upis::0225:siren:1") is None

    def test_doh_canonical_in_sml_zone_yields_none(self, monkeypatch):
        # If DoH returns a name still inside the SML zone, it's not a real SMP.
        monkeypatch.setattr(
            m, "_doh_resolve_canonical",
            lambda fqdn, **kw: f"b-deadbeef.{m.SML_SCHEME_LABEL}.{m.SML_BASE_DOMAIN}.",
        )
        monkeypatch.setattr(m, "USE_DNS_DOH", True)
        assert m.participant_smp_root("iso6523-actorid-upis::0225:siren:1") is None
