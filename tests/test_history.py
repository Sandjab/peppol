"""Tests for history navigation and evolution table."""
from datetime import date

from generate_peppol_report import (
    build_evolution,
    closest_run_at_or_before,
    sorted_dates,
)


def _history(*entries):
    """Build a fake history dict from (date_str, ubl_cius_count) pairs."""
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


class TestClosestRunAtOrBefore:
    def test_returns_none_when_empty(self):
        assert closest_run_at_or_before({"runs": {}}, date(2026, 5, 26)) is None

    def test_returns_exact_match(self):
        h = _history(("2026-05-20", 100), ("2026-05-26", 200))
        assert closest_run_at_or_before(h, date(2026, 5, 26)) == date(2026, 5, 26)

    def test_returns_latest_before_target(self):
        h = _history(("2026-05-10", 50), ("2026-05-20", 100), ("2026-05-25", 150))
        assert closest_run_at_or_before(h, date(2026, 5, 26)) == date(2026, 5, 25)

    def test_returns_none_when_target_predates_history(self):
        h = _history(("2026-05-20", 100))
        assert closest_run_at_or_before(h, date(2026, 5, 1)) is None


class TestBuildEvolution:
    def test_empty_history(self):
        assert build_evolution({"runs": {}}, "2026-05-26") == {"rows": [], "refs": {}}

    def test_single_run_no_deltas(self):
        h = _history(("2026-05-26", 100))
        evo = build_evolution(h, "2026-05-26")
        ubl_cius_row = next(r for r in evo["rows"] if r["key"] == "ubl_cius")
        # No earlier runs → all deltas absent
        assert ubl_cius_row["value"] == 100
        assert ubl_cius_row["d1"] == "—"
        assert ubl_cius_row["d7"] == "—"

    def test_continuous_history(self):
        # 8 days of growth; J-1 and J-7 deltas should be exact
        runs = [("2026-05-{:02d}".format(d), 1000 + d * 10) for d in range(19, 27)]
        h = _history(*runs)
        evo = build_evolution(h, "2026-05-26")
        row = next(r for r in evo["rows"] if r["key"] == "ubl_cius")
        # today = 1260, j-1 = 1250, j-7 = 1190
        assert row["value"] == 1260
        assert "10" in row["d1"]  # +10
        assert "70" in row["d7"]  # +70 over 7 days

    def test_history_with_gap(self):
        # Gap: today + entry 3 days ago. J-1 should fall back to the 3-day-old run.
        h = _history(("2026-05-23", 1000), ("2026-05-26", 1300))
        evo = build_evolution(h, "2026-05-26")
        refs = evo["refs"]
        assert refs["j1"]["date"] == "2026-05-23"
        # Real gap is 3 days, nominal was 1 → drift flagged
        assert refs["j1"]["gap_days"] == 3
        assert refs["j1"]["nominal"] == 1
        assert refs["j1"]["drift"] is True
        row = next(r for r in evo["rows"] if r["key"] == "ubl_cius")
        assert "300" in row["d1"]  # delta against the 3-day-old run

    def test_sorted_dates_ordering(self):
        h = _history(("2026-05-26", 1), ("2026-05-20", 1), ("2026-05-24", 1))
        assert sorted_dates(h) == [date(2026, 5, 20), date(2026, 5, 24), date(2026, 5, 26)]
