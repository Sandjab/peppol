"""Tests for participant doctype classification and signature making."""
from generate_peppol_report import classify, make_signature


UBL_CIUS_URN = (
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice"
    "##urn:cen.eu:en16931:2017#compliant#urn:peppol:france:billing:cius:1.0::2.1"
)
UBL_EXT_URN = (
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice"
    "##urn:cen.eu:en16931:2017#conformant#urn:peppol:france:billing:extended:1.0::2.1"
)
CII_CIUS_URN = (
    "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100::CrossIndustryInvoice"
    "##urn:cen.eu:en16931:2017#compliant#urn:peppol:france:billing:cius:1.0::D22B"
)
CII_EXT_URN = (
    "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100::CrossIndustryInvoice"
    "##urn:cen.eu:en16931:2017#conformant#urn:peppol:france:billing:extended:1.0::D22B"
)
FACTURX_URN = (
    "urn:peppol:doctype:pdf+xml##urn:cen.eu:en16931:2017"
    "#conformant#urn:peppol:france:billing:Factur-X:1.0::D22B"
)
CDAR_URN = (
    "urn:un:unece:uncefact:data:standard:CrossDomainAcknowledgementAndResponse:100::"
    "CrossDomainAcknowledgementAndResponse##urn:peppol:france:billing:cdv:1.0::D22B"
)


def _dt(*values):
    return [{"value": v} for v in values]


class TestClassify:
    def test_empty(self):
        f = classify([])
        assert not (f.ubl_cius or f.ubl_ext or f.cii_cius or f.cii_ext
                    or f.facturx or f.cdar or f.bis_inv)

    def test_ubl_cius_only(self):
        f = classify(_dt(UBL_CIUS_URN))
        assert f.ubl_cius and not f.ubl_ext

    def test_ubl_extended(self):
        f = classify(_dt(UBL_EXT_URN))
        assert f.ubl_ext and not f.ubl_cius

    def test_full_french_set(self):
        f = classify(_dt(UBL_CIUS_URN, UBL_EXT_URN, CII_CIUS_URN, CII_EXT_URN,
                         FACTURX_URN, CDAR_URN))
        assert f.ubl_cius and f.ubl_ext and f.cii_cius and f.cii_ext
        assert f.facturx and f.cdar

    def test_ignores_unknown(self):
        f = classify(_dt("urn:something:completely:unrelated"))
        assert not (f.ubl_cius or f.ubl_ext or f.facturx or f.cdar)


class TestMakeSignature:
    def test_empty(self):
        assert make_signature(classify([])) == ()

    def test_canonical_order(self):
        # _TOKEN_MAP fixes the order: ubl_cius, ubl_ext, cii_cius, cii_ext, fx, cdar, ...
        f = classify(_dt(CDAR_URN, FACTURX_URN, UBL_CIUS_URN))
        sig = make_signature(f)
        # UBL-CIUS appears before FX, FX before CDAR (regardless of input order)
        assert sig == ("UBL-CIUS", "FX", "CDAR")

    def test_signature_is_tuple(self):
        assert isinstance(make_signature(classify(_dt(UBL_CIUS_URN))), tuple)
