"""Tests for SVG rendering helpers (axis ticks)."""
import pytest

from generate_peppol_report import _y_ticks


class TestYTicks:
    def test_ticks_clipped_to_range(self):
        # Real-world volumes case: nice step (100k) extends past vmin/vmax
        ticks = _y_ticks(121122, 425203, target=5)
        assert ticks == [200000, 300000, 400000]
        assert all(121122 <= t <= 425203 for t in ticks)

    def test_no_tick_below_vmin(self):
        # Regression: previous version allowed v >= vmin - step/2
        ticks = _y_ticks(50, 200, target=5)
        assert all(t >= 50 for t in ticks)

    def test_no_tick_above_vmax(self):
        ticks = _y_ticks(0, 423, target=5)
        assert all(t <= 423 for t in ticks)

    def test_degenerate_range(self):
        assert _y_ticks(100, 100) == [100]
        assert _y_ticks(200, 100) == [200]

    def test_ratio_panel_unchanged(self):
        # Small float range as used by the ratio panel
        ticks = _y_ticks(30.0, 60.0, target=5)
        assert ticks == [30, 40, 50, 60]
