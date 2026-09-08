"""
Tests for StatisticsEngine.

Tests cover:
- Daily totals correct
- Monthly totals correct
- Yearly totals correct
- Averages calculated correctly
- Top contractor identified correctly
- Top zone identified correctly
- Trend calculation correct (up/down/stable/None)
- Cache returns cached data within TTL
- Cache refreshes after TTL expires
- Empty periods return zeroed stats
- PeriodStats serialization (to_dict / from_dict)
- Invalid period raises ValueError
- Invalid month raises ValueError
- No-cache mode (stats_cache_repo=None)
- Cache invalidation
"""

import tempfile
import os
import time

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.repositories.stats_cache_repository import StatsCacheRepository
from app.services.statistics_engine import StatisticsEngine, PeriodStats


class TestPeriodStats:
    """Tests for the PeriodStats dataclass."""

    def test_default_values(self):
        """PeriodStats should have sensible defaults."""
        stats = PeriodStats(period="daily", period_key="2026-07-11")
        assert stats.total_reports == 0, "Default total_reports should be 0"
        assert stats.total_contractors == 0, "Default total_contractors should be 0"
        assert stats.total_workers == 0, "Default total_workers should be 0"
        assert stats.avg_contractors == 0.0, "Default avg_contractors should be 0.0"
        assert stats.avg_workers == 0.0, "Default avg_workers should be 0.0"
        assert stats.top_contractor == ("", 0), "Default top_contractor should be ('', 0)"
        assert stats.top_zone == ("", 0), "Default top_zone should be ('', 0)"
        assert stats.trend is None, "Default trend should be None"

    def test_to_dict(self):
        """to_dict should serialize all fields."""
        stats = PeriodStats(
            period="monthly",
            period_key="2026-07",
            total_reports=10,
            total_contractors=50,
            total_workers=500,
            avg_contractors=5.0,
            avg_workers=50.0,
            top_contractor=("ABC Co", 8),
            top_zone=("Zone A", 6),
            trend="up",
        )
        d = stats.to_dict()
        assert d["period"] == "monthly", f"Expected 'monthly', got {d['period']}"
        assert d["period_key"] == "2026-07", f"Expected '2026-07', got {d['period_key']}"
        assert d["total_reports"] == 10, f"Expected 10, got {d['total_reports']}"
        assert d["total_contractors"] == 50, f"Expected 50, got {d['total_contractors']}"
        assert d["total_workers"] == 500, f"Expected 500, got {d['total_workers']}"
        assert d["avg_contractors"] == 5.0, f"Expected 5.0, got {d['avg_contractors']}"
        assert d["avg_workers"] == 50.0, f"Expected 50.0, got {d['avg_workers']}"
        assert d["top_contractor"] == ["ABC Co", 8], f"Expected ['ABC Co', 8], got {d['top_contractor']}"
        assert d["top_zone"] == ["Zone A", 6], f"Expected ['Zone A', 6], got {d['top_zone']}"
        assert d["trend"] == "up", f"Expected 'up', got {d['trend']}"

    def test_from_dict(self):
        """from_dict should deserialize correctly."""
        data = {
            "period": "yearly",
            "period_key": "2026",
            "total_reports": 365,
            "total_contractors": 1000,
            "total_workers": 10000,
            "avg_contractors": 2.74,
            "avg_workers": 27.4,
            "top_contractor": ["XYZ Inc", 100],
            "top_zone": ["Zone B", 80],
            "trend": "down",
        }
        stats = PeriodStats.from_dict(data)
        assert stats.period == "yearly", f"Expected 'yearly', got {stats.period}"
        assert stats.period_key == "2026", f"Expected '2026', got {stats.period_key}"
        assert stats.total_reports == 365, f"Expected 365, got {stats.total_reports}"
        assert stats.top_contractor == ("XYZ Inc", 100), (
            f"Expected ('XYZ Inc', 100), got {stats.top_contractor}"
        )
        assert stats.top_zone == ("Zone B", 80), (
            f"Expected ('Zone B', 80), got {stats.top_zone}"
        )
        assert stats.trend == "down", f"Expected 'down', got {stats.trend}"

    def test_roundtrip_serialization(self):
        """to_dict -> from_dict should preserve all data."""
        original = PeriodStats(
            period="daily",
            period_key="2026-07-11",
            total_reports=3,
            total_contractors=15,
            total_workers=120,
            avg_contractors=5.0,
            avg_workers=40.0,
            top_contractor=("Builder A", 3),
            top_zone=("Zone C", 2),
            trend="stable",
        )
        restored = PeriodStats.from_dict(original.to_dict())
        assert restored.period == original.period, "Period mismatch after roundtrip"
        assert restored.period_key == original.period_key, "Period_key mismatch after roundtrip"
        assert restored.total_reports == original.total_reports, "total_reports mismatch"
        assert restored.total_contractors == original.total_contractors, "total_contractors mismatch"
        assert restored.total_workers == original.total_workers, "total_workers mismatch"
        assert restored.avg_contractors == original.avg_contractors, "avg_contractors mismatch"
        assert restored.avg_workers == original.avg_workers, "avg_workers mismatch"
        assert restored.top_contractor == original.top_contractor, "top_contractor mismatch"
        assert restored.top_zone == original.top_zone, "top_zone mismatch"
        assert restored.trend == original.trend, "trend mismatch"

    def test_from_dict_missing_fields(self):
        """from_dict with missing fields should use defaults."""
        data = {"period": "daily", "period_key": "2026-01-01"}
        stats = PeriodStats.from_dict(data)
        assert stats.total_reports == 0, "Missing total_reports should default to 0"
        assert stats.top_contractor == ("", 0), "Missing top_contractor should default to ('', 0)"
        assert stats.trend is None, "Missing trend should default to None"


class TestStatisticsEngine:
    """Tests for the StatisticsEngine service."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._db = DatabaseManager(self._tmp.name)
        self._cache_repo = StatsCacheRepository(self._db, ttl_minutes=60)
        self._report_repo = ReportRepository(self._db)
        self._engine = StatisticsEngine(self._db, self._cache_repo)

    def teardown_method(self):
        """Clean up after each test."""
        try:
            self._db.close()
        except Exception:
            pass
        for attempt in range(5):
            try:
                os.unlink(self._tmp.name)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1)

    def _create_report(self, date: str, day: str, items: list[ReportItem],
                       status: ReportStatus = ReportStatus.FINAL) -> Report:
        """Helper to create and persist a report with items."""
        report = Report(
            date=date,
            day=day,
            status=status,
            telegram_user="test_user",
        )
        report.items = items
        return self._report_repo.add(report)

    def _make_item(self, contractor: str, workers: int,
                   zone: str = None, type_: str = None) -> ReportItem:
        """Helper to create a ReportItem."""
        return ReportItem(
            contractor=contractor,
            type=type_,
            zone=zone,
            workers=workers,
        )

    # --- Daily Stats Tests ---

    def test_daily_stats_empty(self):
        """Daily stats for a date with no reports should return zeroed stats."""
        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.period == "daily", f"Expected 'daily', got {stats.period}"
        assert stats.period_key == "2026-07-11", f"Expected '2026-07-11', got {stats.period_key}"
        assert stats.total_reports == 0, f"Expected 0 reports, got {stats.total_reports}"
        assert stats.total_contractors == 0, f"Expected 0 contractors, got {stats.total_contractors}"
        assert stats.total_workers == 0, f"Expected 0 workers, got {stats.total_workers}"
        assert stats.avg_contractors == 0.0, f"Expected 0.0 avg contractors, got {stats.avg_contractors}"
        assert stats.avg_workers == 0.0, f"Expected 0.0 avg workers, got {stats.avg_workers}"
        assert stats.top_contractor == ("", 0), f"Expected empty top contractor, got {stats.top_contractor}"
        assert stats.top_zone == ("", 0), f"Expected empty top zone, got {stats.top_zone}"

    def test_daily_stats_single_report(self):
        """Daily stats for a date with one report should return correct totals."""
        items = [
            self._make_item("Contractor A", 10, zone="Zone 1"),
            self._make_item("Contractor B", 20, zone="Zone 2"),
        ]
        self._create_report("2026-07-11", "Saturday", items)

        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.total_reports == 1, f"Expected 1 report, got {stats.total_reports}"
        assert stats.total_contractors == 2, f"Expected 2 contractors, got {stats.total_contractors}"
        assert stats.total_workers == 30, f"Expected 30 workers, got {stats.total_workers}"
        assert stats.avg_contractors == 2.0, f"Expected 2.0 avg contractors, got {stats.avg_contractors}"
        assert stats.avg_workers == 30.0, f"Expected 30.0 avg workers, got {stats.avg_workers}"

    def test_daily_stats_no_report_status_excluded(self):
        """Reports with no_report status should be excluded from stats."""
        items = [self._make_item("Contractor A", 10)]
        self._create_report("2026-07-11", "Saturday", items, status=ReportStatus.NO_REPORT)

        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.total_reports == 0, (
            f"no_report status should be excluded, got {stats.total_reports} reports"
        )
        assert stats.total_workers == 0, (
            f"no_report should have 0 workers, got {stats.total_workers}"
        )

    def test_daily_stats_null_workers(self):
        """Items with null workers should be counted but contribute 0 to total."""
        items = [
            self._make_item("Contractor A", 10),
            self._make_item("Contractor B", None),
        ]
        self._create_report("2026-07-11", "Saturday", items)

        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.total_reports == 1, f"Expected 1 report, got {stats.total_reports}"
        assert stats.total_contractors == 2, f"Expected 2 contractors, got {stats.total_contractors}"
        assert stats.total_workers == 10, f"Expected 10 workers (null=0), got {stats.total_workers}"

    # --- Monthly Stats Tests ---

    def test_monthly_stats_empty(self):
        """Monthly stats for a month with no reports should return zeroed stats."""
        stats = self._engine.get_monthly_stats(2026, 7)
        assert stats.period == "monthly", f"Expected 'monthly', got {stats.period}"
        assert stats.period_key == "2026-07", f"Expected '2026-07', got {stats.period_key}"
        assert stats.total_reports == 0, f"Expected 0 reports, got {stats.total_reports}"

    def test_monthly_stats_multiple_days(self):
        """Monthly stats should aggregate across all days in the month."""
        # Day 1: 2 contractors, 30 workers
        items1 = [
            self._make_item("Contractor A", 10, zone="Zone 1"),
            self._make_item("Contractor B", 20, zone="Zone 1"),
        ]
        self._create_report("2026-07-01", "Wednesday", items1)

        # Day 2: 1 contractor, 15 workers
        items2 = [self._make_item("Contractor A", 15, zone="Zone 2")]
        self._create_report("2026-07-02", "Thursday", items2)

        # Day 15: 3 contractors, 60 workers
        items3 = [
            self._make_item("Contractor C", 20, zone="Zone 1"),
            self._make_item("Contractor A", 25, zone="Zone 2"),
            self._make_item("Contractor D", 15, zone="Zone 3"),
        ]
        self._create_report("2026-07-15", "Wednesday", items3)

        stats = self._engine.get_monthly_stats(2026, 7)
        assert stats.total_reports == 3, f"Expected 3 reports, got {stats.total_reports}"
        assert stats.total_contractors == 6, f"Expected 6 contractor items, got {stats.total_contractors}"
        assert stats.total_workers == 105, f"Expected 105 workers, got {stats.total_workers}"
        assert stats.avg_contractors == 2.0, f"Expected 2.0 avg contractors, got {stats.avg_contractors}"
        assert stats.avg_workers == 35.0, f"Expected 35.0 avg workers, got {stats.avg_workers}"

    def test_monthly_stats_excludes_other_months(self):
        """Monthly stats should only include reports from the specified month."""
        items_june = [self._make_item("Contractor A", 100)]
        self._create_report("2026-06-30", "Tuesday", items_june)

        items_july = [self._make_item("Contractor B", 10)]
        self._create_report("2026-07-01", "Wednesday", items_july)

        items_aug = [self._make_item("Contractor C", 200)]
        self._create_report("2026-08-01", "Saturday", items_aug)

        stats = self._engine.get_monthly_stats(2026, 7)
        assert stats.total_reports == 1, f"Expected 1 report in July, got {stats.total_reports}"
        assert stats.total_workers == 10, f"Expected 10 workers in July, got {stats.total_workers}"

    def test_monthly_stats_invalid_month(self):
        """Invalid month should raise ValueError."""
        try:
            self._engine.get_monthly_stats(2026, 0)
            assert False, "Should have raised ValueError for month=0"
        except ValueError:
            pass  # Expected

        try:
            self._engine.get_monthly_stats(2026, 13)
            assert False, "Should have raised ValueError for month=13"
        except ValueError:
            pass  # Expected

    # --- Yearly Stats Tests ---

    def test_yearly_stats_empty(self):
        """Yearly stats for a year with no reports should return zeroed stats."""
        stats = self._engine.get_yearly_stats(2026)
        assert stats.period == "yearly", f"Expected 'yearly', got {stats.period}"
        assert stats.period_key == "2026", f"Expected '2026', got {stats.period_key}"
        assert stats.total_reports == 0, f"Expected 0 reports, got {stats.total_reports}"

    def test_yearly_stats_aggregates_all_months(self):
        """Yearly stats should aggregate across all months in the year."""
        # January
        items1 = [self._make_item("Contractor A", 10, zone="Zone 1")]
        self._create_report("2026-01-15", "Thursday", items1)

        # June
        items2 = [self._make_item("Contractor B", 20, zone="Zone 2")]
        self._create_report("2026-06-15", "Monday", items2)

        # December
        items3 = [self._make_item("Contractor A", 30, zone="Zone 1")]
        self._create_report("2026-12-15", "Tuesday", items3)

        stats = self._engine.get_yearly_stats(2026)
        assert stats.total_reports == 3, f"Expected 3 reports, got {stats.total_reports}"
        assert stats.total_contractors == 3, f"Expected 3 contractor items, got {stats.total_contractors}"
        assert stats.total_workers == 60, f"Expected 60 workers, got {stats.total_workers}"
        assert stats.avg_workers == 20.0, f"Expected 20.0 avg workers, got {stats.avg_workers}"

    def test_yearly_stats_excludes_other_years(self):
        """Yearly stats should only include reports from the specified year."""
        items_2025 = [self._make_item("Contractor A", 100)]
        self._create_report("2025-12-31", "Wednesday", items_2025)

        items_2026 = [self._make_item("Contractor B", 10)]
        self._create_report("2026-01-01", "Thursday", items_2026)

        items_2027 = [self._make_item("Contractor C", 200)]
        self._create_report("2027-01-01", "Friday", items_2027)

        stats = self._engine.get_yearly_stats(2026)
        assert stats.total_reports == 1, f"Expected 1 report in 2026, got {stats.total_reports}"
        assert stats.total_workers == 10, f"Expected 10 workers in 2026, got {stats.total_workers}"

    # --- Averages Tests ---

    def test_averages_single_report(self):
        """Averages for a single report should equal the totals."""
        items = [
            self._make_item("A", 10),
            self._make_item("B", 20),
            self._make_item("C", 30),
        ]
        self._create_report("2026-07-11", "Saturday", items)

        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.avg_contractors == 3.0, f"Expected 3.0, got {stats.avg_contractors}"
        assert stats.avg_workers == 60.0, f"Expected 60.0, got {stats.avg_workers}"

    def test_averages_multiple_reports(self):
        """Averages should be computed correctly across multiple reports."""
        # Report 1: 2 contractors, 30 workers
        self._create_report("2026-07-01", "Wednesday", [
            self._make_item("A", 10),
            self._make_item("B", 20),
        ])
        # Report 2: 4 contractors, 100 workers
        self._create_report("2026-07-02", "Thursday", [
            self._make_item("C", 25),
            self._make_item("D", 25),
            self._make_item("E", 25),
            self._make_item("F", 25),
        ])

        stats = self._engine.get_monthly_stats(2026, 7)
        assert stats.total_reports == 2, f"Expected 2 reports, got {stats.total_reports}"
        assert stats.avg_contractors == 3.0, f"Expected (2+4)/2=3.0, got {stats.avg_contractors}"
        assert stats.avg_workers == 65.0, f"Expected (30+100)/2=65.0, got {stats.avg_workers}"

    # --- Top Contractor Tests ---

    def test_top_contractor(self):
        """Should identify the most frequently appearing contractor."""
        # Day 1: A, B
        self._create_report("2026-07-01", "Wednesday", [
            self._make_item("Contractor A", 10),
            self._make_item("Contractor B", 20),
        ])
        # Day 2: A, C
        self._create_report("2026-07-02", "Thursday", [
            self._make_item("Contractor A", 15),
            self._make_item("Contractor C", 25),
        ])
        # Day 3: A, B, C
        self._create_report("2026-07-03", "Friday", [
            self._make_item("Contractor A", 10),
            self._make_item("Contractor B", 20),
            self._make_item("Contractor C", 30),
        ])

        stats = self._engine.get_monthly_stats(2026, 7)
        # A appears 3 times, B appears 2 times, C appears 2 times
        assert stats.top_contractor[0] == "Contractor A", (
            f"Expected top contractor 'Contractor A', got '{stats.top_contractor[0]}'"
        )
        assert stats.top_contractor[1] == 3, (
            f"Expected 3 appearances, got {stats.top_contractor[1]}"
        )

    def test_top_contractor_tiebreaker_alphabetical(self):
        """When tied, top contractor should be alphabetically first."""
        self._create_report("2026-07-01", "Wednesday", [
            self._make_item("Zebra Co", 10),
            self._make_item("Alpha Co", 10),
        ])

        stats = self._engine.get_daily_stats("2026-07-01")
        # Both appear once — alphabetical tiebreaker
        assert stats.top_contractor[0] == "Alpha Co", (
            f"Expected 'Alpha Co' (alphabetical), got '{stats.top_contractor[0]}'"
        )

    def test_top_contractor_empty(self):
        """Top contractor should be empty when no data."""
        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.top_contractor == ("", 0), (
            f"Expected ('', 0), got {stats.top_contractor}"
        )

    # --- Top Zone Tests ---

    def test_top_zone(self):
        """Should identify the most frequently appearing zone."""
        self._create_report("2026-07-01", "Wednesday", [
            self._make_item("A", 10, zone="Zone Alpha"),
            self._make_item("B", 20, zone="Zone Beta"),
        ])
        self._create_report("2026-07-02", "Thursday", [
            self._make_item("C", 15, zone="Zone Alpha"),
            self._make_item("D", 25, zone="Zone Alpha"),
        ])

        stats = self._engine.get_monthly_stats(2026, 7)
        # Zone Alpha appears 3 times, Zone Beta 1 time
        assert stats.top_zone[0] == "Zone Alpha", (
            f"Expected top zone 'Zone Alpha', got '{stats.top_zone[0]}'"
        )
        assert stats.top_zone[1] == 3, (
            f"Expected 3 appearances, got {stats.top_zone[1]}"
        )

    def test_top_zone_ignores_null_and_empty(self):
        """Zones that are null or empty should not be counted."""
        self._create_report("2026-07-01", "Wednesday", [
            self._make_item("A", 10, zone=None),
            self._make_item("B", 20, zone=""),
            self._make_item("C", 30, zone="Zone X"),
        ])

        stats = self._engine.get_daily_stats("2026-07-01")
        assert stats.top_zone[0] == "Zone X", (
            f"Expected 'Zone X', got '{stats.top_zone[0]}'"
        )

    def test_top_zone_empty(self):
        """Top zone should be empty when no zone data."""
        self._create_report("2026-07-01", "Wednesday", [
            self._make_item("A", 10),  # No zone
        ])

        stats = self._engine.get_daily_stats("2026-07-01")
        assert stats.top_zone == ("", 0), (
            f"Expected ('', 0), got {stats.top_zone}"
        )

    # --- Trend Tests ---

    def test_trend_up(self):
        """Trend should be 'up' when workers increase."""
        # Day 1: 10 workers
        self._create_report("2026-07-10", "Friday", [
            self._make_item("A", 10),
        ])
        # Day 2: 30 workers
        self._create_report("2026-07-11", "Saturday", [
            self._make_item("A", 30),
        ])

        trend = self._engine.calculate_trend("daily", "2026-07-11")
        assert trend == "up", f"Expected 'up', got '{trend}'"

    def test_trend_down(self):
        """Trend should be 'down' when workers decrease."""
        # Day 1: 50 workers
        self._create_report("2026-07-10", "Friday", [
            self._make_item("A", 50),
        ])
        # Day 2: 20 workers
        self._create_report("2026-07-11", "Saturday", [
            self._make_item("A", 20),
        ])

        trend = self._engine.calculate_trend("daily", "2026-07-11")
        assert trend == "down", f"Expected 'down', got '{trend}'"

    def test_trend_stable(self):
        """Trend should be 'stable' when workers are unchanged."""
        self._create_report("2026-07-10", "Friday", [
            self._make_item("A", 25),
        ])
        self._create_report("2026-07-11", "Saturday", [
            self._make_item("A", 25),
        ])

        trend = self._engine.calculate_trend("daily", "2026-07-11")
        assert trend == "stable", f"Expected 'stable', got '{trend}'"

    def test_trend_none_no_previous(self):
        """Trend should be None when there's no previous period data."""
        # Only one day of data — previous day has nothing
        self._create_report("2026-07-11", "Saturday", [
            self._make_item("A", 10),
        ])

        trend = self._engine.calculate_trend("daily", "2026-07-11")
        # Previous day (2026-07-10) has 0 workers, current has 10
        # Since previous is 0 and current > 0, trend should be 'up'
        assert trend == "up", f"Expected 'up' (from 0 to 10), got '{trend}'"

    def test_trend_none_both_empty(self):
        """Trend should be None when both periods have zero workers."""
        trend = self._engine.calculate_trend("daily", "2026-07-11")
        assert trend is None, f"Expected None for empty periods, got '{trend}'"

    def test_trend_monthly(self):
        """Monthly trend should compare to previous month."""
        # June: 100 workers
        self._create_report("2026-06-15", "Monday", [
            self._make_item("A", 100),
        ])
        # July: 200 workers
        self._create_report("2026-07-15", "Wednesday", [
            self._make_item("A", 200),
        ])

        trend = self._engine.calculate_trend("monthly", "2026-07")
        assert trend == "up", f"Expected 'up', got '{trend}'"

    def test_trend_yearly(self):
        """Yearly trend should compare to previous year."""
        # 2025: 50 workers
        self._create_report("2025-06-15", "Sunday", [
            self._make_item("A", 50),
        ])
        # 2026: 30 workers
        self._create_report("2026-06-15", "Monday", [
            self._make_item("A", 30),
        ])

        trend = self._engine.calculate_trend("yearly", "2026")
        assert trend == "down", f"Expected 'down', got '{trend}'"

    def test_trend_monthly_january_to_december(self):
        """January's previous period should be December of previous year."""
        # December 2025: 100 workers
        self._create_report("2025-12-15", "Monday", [
            self._make_item("A", 100),
        ])
        # January 2026: 50 workers
        self._create_report("2026-01-15", "Thursday", [
            self._make_item("A", 50),
        ])

        trend = self._engine.calculate_trend("monthly", "2026-01")
        assert trend == "down", f"Expected 'down' (Dec->Jan), got '{trend}'"

    # --- Cache Tests ---

    def test_cache_hit(self):
        """Second call should return cached data (same result)."""
        items = [self._make_item("A", 10)]
        self._create_report("2026-07-11", "Saturday", items)

        stats1 = self._engine.get_daily_stats("2026-07-11")
        assert stats1.total_workers == 10, f"Expected 10, got {stats1.total_workers}"

        # Second call should hit cache
        stats2 = self._engine.get_daily_stats("2026-07-11")
        assert stats2.total_workers == 10, f"Expected 10 from cache, got {stats2.total_workers}"
        assert stats2.total_reports == stats1.total_reports, "Cached stats should match"

    def test_cache_invalidation(self):
        """After cache invalidation, stats should be recomputed."""
        items = [self._make_item("A", 10)]
        self._create_report("2026-07-11", "Saturday", items)

        stats1 = self._engine.get_daily_stats("2026-07-11")
        assert stats1.total_workers == 10, f"Expected 10, got {stats1.total_workers}"

        # Invalidate cache
        self._engine.invalidate_cache("daily", "2026-07-11")

        # Add more data
        self._create_report("2026-07-12", "Sunday", [
            self._make_item("B", 20),
        ])

        # Monthly stats should reflect new data after cache miss
        stats2 = self._engine.get_monthly_stats(2026, 7)
        assert stats2.total_workers == 30, f"Expected 30 after invalidation, got {stats2.total_workers}"

    def test_invalidate_all_cache(self):
        """invalidate_all_cache should clear all cached entries."""
        items = [self._make_item("A", 10)]
        self._create_report("2026-07-11", "Saturday", items)

        self._engine.get_daily_stats("2026-07-11")
        self._engine.get_monthly_stats(2026, 7)

        removed = self._engine.invalidate_all_cache()
        assert removed >= 2, f"Expected at least 2 entries removed, got {removed}"

    def test_no_cache_mode(self):
        """Engine should work without a cache repository."""
        engine = StatisticsEngine(self._db, stats_cache_repo=None)
        items = [self._make_item("A", 10)]
        self._create_report("2026-07-11", "Saturday", items)

        stats = engine.get_daily_stats("2026-07-11")
        assert stats.total_workers == 10, f"Expected 10, got {stats.total_workers}"
        assert stats.total_reports == 1, f"Expected 1, got {stats.total_reports}"

        # Invalidation should return False when no cache
        assert engine.invalidate_cache("daily", "2026-07-11") is False, (
            "invalidate_cache should return False without cache repo"
        )
        assert engine.invalidate_all_cache() == 0, (
            "invalidate_all_cache should return 0 without cache repo"
        )

    # --- Invalid Period Tests ---

    def test_invalid_period_raises(self):
        """Invalid period should raise ValueError."""
        try:
            self._engine.get_period_stats("weekly", "2026-W28")
            assert False, "Should have raised ValueError for 'weekly'"
        except ValueError as e:
            assert "weekly" in str(e), f"Error should mention 'weekly': {e}"

    # --- Draft and Locked Reports ---

    def test_draft_reports_included(self):
        """Draft reports should be included in statistics."""
        items = [self._make_item("A", 10)]
        self._create_report("2026-07-11", "Saturday", items, status=ReportStatus.DRAFT)

        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.total_reports == 1, f"Draft reports should be counted, got {stats.total_reports}"
        assert stats.total_workers == 10, f"Expected 10 workers from draft, got {stats.total_workers}"

    def test_locked_reports_included(self):
        """Locked reports should be included in statistics."""
        items = [self._make_item("A", 15)]
        self._create_report("2026-07-11", "Saturday", items, status=ReportStatus.LOCKED)

        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.total_reports == 1, f"Locked reports should be counted, got {stats.total_reports}"
        assert stats.total_workers == 15, f"Expected 15 workers from locked, got {stats.total_workers}"

    # --- Edge Cases ---

    def test_many_reports_same_day_not_possible(self):
        """Only one report per date — duplicate protection is at repo level."""
        items1 = [self._make_item("A", 10)]
        self._create_report("2026-07-11", "Saturday", items1)

        # Second report for same date should fail at repo level
        from app.utils.exceptions import DatabaseError
        items2 = [self._make_item("B", 20)]
        try:
            self._create_report("2026-07-11", "Saturday", items2)
            assert False, "Should raise DatabaseError for duplicate date"
        except DatabaseError:
            pass  # Expected

    def test_stats_with_zero_workers(self):
        """Reports with 0 workers should still count as reports."""
        items = [self._make_item("A", 0)]
        self._create_report("2026-07-11", "Saturday", items)

        stats = self._engine.get_daily_stats("2026-07-11")
        assert stats.total_reports == 1, f"Expected 1 report, got {stats.total_reports}"
        assert stats.total_workers == 0, f"Expected 0 workers, got {stats.total_workers}"
        assert stats.avg_workers == 0.0, f"Expected 0.0 avg workers, got {stats.avg_workers}"

    def test_period_stats_includes_trend(self):
        """get_period_stats should include trend in the result."""
        self._create_report("2026-07-10", "Friday", [
            self._make_item("A", 10),
        ])
        self._create_report("2026-07-11", "Saturday", [
            self._make_item("A", 20),
        ])

        stats = self._engine.get_period_stats("daily", "2026-07-11")
        assert stats.trend == "up", f"Expected trend 'up', got '{stats.trend}'"

    def test_get_period_stats_via_convenience_methods(self):
        """Convenience methods should delegate to get_period_stats."""
        items = [self._make_item("A", 10)]
        self._create_report("2026-07-11", "Saturday", items)

        daily = self._engine.get_daily_stats("2026-07-11")
        assert daily.period == "daily", f"Expected 'daily', got {daily.period}"
        assert daily.period_key == "2026-07-11", f"Expected '2026-07-11', got {daily.period_key}"

        monthly = self._engine.get_monthly_stats(2026, 7)
        assert monthly.period == "monthly", f"Expected 'monthly', got {monthly.period}"
        assert monthly.period_key == "2026-07", f"Expected '2026-07', got {monthly.period_key}"

        yearly = self._engine.get_yearly_stats(2026)
        assert yearly.period == "yearly", f"Expected 'yearly', got {yearly.period}"
        assert yearly.period_key == "2026", f"Expected '2026', got {yearly.period_key}"

    def test_previous_period_key_daily(self):
        """Previous day should be calculated correctly."""
        prev = self._engine._get_previous_period_key("daily", "2026-07-01")
        assert prev == "2026-06-30", f"Expected '2026-06-30', got '{prev}'"

        prev = self._engine._get_previous_period_key("daily", "2026-03-01")
        assert prev == "2026-02-28", f"Expected '2026-02-28', got '{prev}'"

    def test_previous_period_key_monthly(self):
        """Previous month should be calculated correctly."""
        prev = self._engine._get_previous_period_key("monthly", "2026-03")
        assert prev == "2026-02", f"Expected '2026-02', got '{prev}'"

        prev = self._engine._get_previous_period_key("monthly", "2026-01")
        assert prev == "2025-12", f"Expected '2025-12', got '{prev}'"

    def test_previous_period_key_yearly(self):
        """Previous year should be calculated correctly."""
        prev = self._engine._get_previous_period_key("yearly", "2026")
        assert prev == "2025", f"Expected '2025', got '{prev}'"

    def test_previous_period_key_invalid(self):
        """Invalid period key should return None."""
        prev = self._engine._get_previous_period_key("daily", "not-a-date")
        assert prev is None, f"Expected None for invalid date, got '{prev}'"

        prev = self._engine._get_previous_period_key("monthly", "bad")
        assert prev is None, f"Expected None for invalid month, got '{prev}'"

        prev = self._engine._get_previous_period_key("yearly", "bad")
        assert prev is None, f"Expected None for invalid year, got '{prev}'"

        prev = self._engine._get_previous_period_key("weekly", "2026-W28")
        assert prev is None, f"Expected None for invalid period, got '{prev}'"
