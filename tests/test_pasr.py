"""Tests for the PASR countdown context builder."""
from datetime import date

import generate_peppol_report as m
from generate_peppol_report import (
    PASR_DEADLINE,
    UNIVERSE_CENTRAL_DIRECTORY,
    UNIVERSE_VAT_ENTITIES,
    build_pasr_context,
)


def _history(*entries):
    """Build a fake history dict from (date_str, ubl_cius_count) pairs.

    Other doctypes track ubl_cius so that max() == ubl_cius.
    """
    runs = {}
    for d, ubl_cius in entries:
        runs[d] = {
            "fetched_at": f"{d}T09:00:00+02:00",
            "counts_fr": {
                "ubl_cius": ubl_cius,
                "ubl_ext": ubl_cius // 3,
                "cii_cius": ubl_cius,
                "cii_ext": ubl_cius // 3,
                "facturx": ubl_cius * 95 // 100,
                "cdar": ubl_cius,
            },
        }
    return {"schema_version": 1, "runs": runs}


class TestBuildPasrContext:
    def test_constants_are_sane(self):
        assert PASR_DEADLINE == date(2026, 9, 1)
        assert UNIVERSE_VAT_ENTITIES == 4_500_000
        assert UNIVERSE_CENTRAL_DIRECTORY == 600_000

    def test_days_remaining_positive(self):
        h = _history(("2026-05-28", 50_000))
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["days_remaining"] == (date(2026, 9, 1) - date(2026, 5, 28)).days
        assert ctx["is_past_deadline"] is False

    def test_past_deadline(self):
        h = _history(("2026-09-15", 1_000_000))
        ctx = build_pasr_context(h, date(2026, 9, 15))
        assert ctx["days_remaining"] < 0
        assert ctx["is_past_deadline"] is True
        # No required velocity when deadline is past.
        assert ctx["velocity_required_central"] is None
        assert ctx["velocity_required_vat"] is None

    def test_peppol_count_is_max_of_doctypes(self):
        # Build a snapshot where one doctype has a much higher count: the
        # builder must report that as the entity-count lower bound.
        h = {
            "schema_version": 1,
            "runs": {
                "2026-05-28": {
                    "fetched_at": "2026-05-28T09:00:00+02:00",
                    "counts_fr": {
                        "ubl_cius": 1_000,
                        "ubl_ext": 100,
                        "cii_cius": 1_500,  # max
                        "cii_ext": 50,
                        "facturx": 900,
                        "cdar": 200,
                    },
                }
            },
        }
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["peppol_count"] == 1_500

    def test_velocity_observed_over_7_days(self):
        # 8 days of linear growth: +100 entities/day on ubl_cius (=max).
        runs = [(f"2026-05-{d:02d}", 1000 + (d - 20) * 100) for d in range(20, 28)]
        h = _history(*runs)
        ctx = build_pasr_context(h, date(2026, 5, 27))
        # today = 1700, J-7 = 1000 → 700 / 7 = 100/day
        assert ctx["velocity_observed_7d"] == 100.0

    def test_velocity_observed_none_when_no_history(self):
        h = _history(("2026-05-28", 50_000))
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["velocity_observed_7d"] is None

    def test_velocity_observed_uses_real_gap_when_history_has_holes(self):
        # Reference point at J-10, current at today → +600/10 = 60/day.
        # build_pasr_context uses closest_run_at_or_before(today - 7d), so it
        # picks the run 10 days back and reports the actual per-day rate over
        # the realized window (10 days), not the nominal 7.
        h = _history(("2026-05-18", 1_000), ("2026-05-28", 1_600))
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["velocity_observed_7d"] == 60.0

    def test_velocity_observed_none_when_no_run_before_jminus7(self):
        # Only a run 3 days ago: no point at or before J-7 → conservative None
        # rather than extrapolating from a short window.
        h = _history(("2026-05-25", 1_000), ("2026-05-28", 1_600))
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["velocity_observed_7d"] is None

    def test_velocity_required_central(self):
        # 60 000 entities, 600 days remaining → (600k - 60k)/600 = 900/day.
        h = _history(("2025-01-09", 60_000))
        ctx = build_pasr_context(h, date(2025, 1, 9))
        # 2025-01-09 → 2026-09-01 = 600 days
        days = (PASR_DEADLINE - date(2025, 1, 9)).days
        expected = (UNIVERSE_CENTRAL_DIRECTORY - 60_000) / days
        assert abs(ctx["velocity_required_central"] - expected) < 1e-9

    def test_velocity_required_zero_when_already_above_target(self):
        h = _history(("2026-05-28", 1_000_000))  # already above central target
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["velocity_required_central"] == 0.0
        # But still positive for VAT universe
        assert ctx["velocity_required_vat"] > 0

    def test_pct_central_and_vat(self):
        h = _history(("2026-05-28", 60_000))
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["pct_central"] == 10.0  # 60k / 600k
        assert abs(ctx["pct_vat"] - (60_000 / 4_500_000 * 100)) < 1e-9  # 60k / 4.5M

    def test_empty_history_today_yields_zero_count(self):
        # Today has no run: builder still returns a valid context.
        h = {"schema_version": 1, "runs": {}}
        ctx = build_pasr_context(h, date(2026, 5, 28))
        assert ctx["peppol_count"] == 0
        assert ctx["velocity_observed_7d"] is None
