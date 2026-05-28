"""Tests for SML lookup and per-SMP coverage aggregation."""
import hashlib
from unittest import mock

import generate_peppol_report as m


class TestExtractParticipantValue:
    def test_dict_form(self):
        pid = {"scheme": "iso6523-actorid-upis", "value": "0225:siren:519840501"}
        assert m._extract_participant_value(pid) == "0225:siren:519840501"

    def test_dict_value_is_lowercased(self):
        pid = {"scheme": "iso6523-actorid-upis", "value": "0225:SIREN:519840501"}
        assert m._extract_participant_value(pid) == "0225:siren:519840501"

    def test_string_with_scheme_prefix(self):
        s = "iso6523-actorid-upis::0225:siren:519840501"
        assert m._extract_participant_value(s) == "0225:siren:519840501"

    def test_string_without_scheme_prefix(self):
        s = "0225:siren:519840501"
        assert m._extract_participant_value(s) == "0225:siren:519840501"

    def test_missing_or_empty(self):
        assert m._extract_participant_value({"scheme": "x", "value": ""}) is None
        assert m._extract_participant_value({}) is None
        assert m._extract_participant_value(None) is None  # type: ignore
        assert m._extract_participant_value("") is None


class TestSmlFqdn:
    def test_format_matches_peppol_spec(self):
        """FQDN must be B-{md5_hex_lowercase}.iso6523-actorid-upis.{SML domain}."""
        value = "0225:siren:519840501"
        expected_hash = hashlib.md5(value.encode("utf-8")).hexdigest()
        fqdn = m._sml_fqdn(value)
        assert fqdn.lower() == (
            f"b-{expected_hash}.iso6523-actorid-upis."
            f"edelivery.tech.ec.europa.eu"
        )

    def test_hash_is_md5_of_lowercase(self):
        # Different cases of the value yield different hashes IF caller forgets
        # to lowercase — but _extract_participant_value() already lowercases.
        # Here we just confirm _sml_fqdn doesn't normalize on its own.
        a = m._sml_fqdn("abc")
        b = m._sml_fqdn("ABC")
        assert a != b


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
            "0225:siren:a": "docaposte.fr",
            "0225:siren:b": "docaposte.fr",
            "0225:siren:c": "contoso.fr",
            "0225:siren:d": "contoso.fr",
        }
        monkeypatch.setattr(m, "participant_smp_root",
                            lambda v, **kw: smp_map[m._extract_participant_value(v)])
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
        smp_map = {"0225:a": "big.fr", "0225:b": "big.fr", "0225:c": "small.fr"}
        monkeypatch.setattr(m, "participant_smp_root",
                            lambda v, **kw: smp_map[m._extract_participant_value(v)])
        samples = {k: [] for k in m.DOCTYPES_FR}
        samples["ubl_cius"] = [self._match(x) for x in smp_map]
        result = m.collect_smp_coverage(samples)
        roots = [s["root"] for s in result["smps"]]
        assert roots == ["big.fr", "small.fr"]

    def test_unresolved_counted_and_excluded(self, monkeypatch):
        smp_map = {"0225:a": "ok.fr", "0225:b": None}
        monkeypatch.setattr(m, "participant_smp_root",
                            lambda v, **kw: smp_map[m._extract_participant_value(v)])
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
