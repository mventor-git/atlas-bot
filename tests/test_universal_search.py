"""Tests for Universal Search. (mventor-ticket-012)"""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.models.search import SearchQuery, SearchResult
from app.repositories.report_repository import ReportRepository
from app.repositories.search_repository import SearchRepository
from app.services.universal_search_service import UniversalSearchService


class TestUniversalSearchModels:
    """Tests for search dataclasses."""

    def test_search_query_offset(self):
        query = SearchQuery(text="alpha", page=2, page_size=5)
        assert query.offset == 10, f"Expected offset 10, got {query.offset}"

    def test_search_query_normalizes_status_enum(self):
        query = SearchQuery(status=ReportStatus.FINAL)
        assert query.normalized_status == "final"
        assert query.has_criteria is True

    def test_search_result_total_pages(self):
        result = SearchResult(query=SearchQuery(page_size=5), hits=[], total_count=11)
        assert result.total_pages == 3, f"Expected 3 pages, got {result.total_pages}"

    def test_search_result_no_results_message_uses_query_text(self):
        result = SearchResult(query=SearchQuery(text="Alpha"), hits=[], total_count=0)
        assert result.no_results_message == "No results found for 'Alpha'."


class TestSearchRepository:
    """Test suite for SearchRepository."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_search.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def report_repo(self, db_manager: DatabaseManager):
        return ReportRepository(db_manager)

    @pytest.fixture
    def search_repo(self, db_manager: DatabaseManager):
        return SearchRepository(db_manager)

    @pytest.fixture
    def service(self, search_repo: SearchRepository):
        return UniversalSearchService(search_repo)

    @pytest.fixture
    def seeded_reports(self, report_repo: ReportRepository):
        report_1 = Report(
            date="2026-07-10",
            day="Friday",
            status=ReportStatus.DRAFT,
            telegram_user="u1",
        )
        report_1.add_item(ReportItem(
            contractor="Alpha Builders",
            contractor_code="ALP-001",
            type="Civil",
            zone="Zone A",
            workers=10,
            details="Masonry crew",
        ))
        report_1.add_item(ReportItem(
            contractor="Beta Electric",
            contractor_code="BET-002",
            type="Electrical",
            zone="Zone B",
            workers=5,
            details="Cable pulling",
        ))

        report_2 = Report(
            date="2026-07-11",
            day="Saturday",
            status=ReportStatus.FINAL,
            telegram_user="u2",
        )
        report_2.add_item(ReportItem(
            contractor="Alpha",
            contractor_code="ALP-EXACT",
            type="Finishing",
            zone="Zone C",
            workers=18,
            details="Painting and snagging",
        ))

        report_3 = Report(
            date="2026-07-12",
            day="Sunday",
            status=ReportStatus.LOCKED,
            telegram_user="u3",
        )
        report_3.add_item(ReportItem(
            contractor="Gamma Roads",
            contractor_code="GAM-003",
            type="Civil",
            zone="North Yard",
            workers=22,
            details="Road base preparation",
        ))

        report_4 = Report(
            date="2026-07-13",
            day="Monday",
            status=ReportStatus.NO_REPORT,
            telegram_user="u4",
        )

        report_repo.add(report_1)
        report_repo.add(report_2)
        report_repo.add(report_3)
        report_repo.add(report_4)

    def test_search_by_exact_contractor_orders_before_partial(
        self, search_repo: SearchRepository, seeded_reports
    ):
        result = search_repo.search(SearchQuery(text="Alpha"))
        assert result.total_count == 2, f"Expected 2 Alpha matches, got {result.total_count}"
        assert result.hits[0].date == "2026-07-11", "Exact contractor match should rank first"
        assert result.hits[0].matched_contractor == "Alpha"
        assert "contractor" in result.hits[0].matched_fields

    def test_search_by_partial_contractor_name(
        self, search_repo: SearchRepository, seeded_reports
    ):
        result = search_repo.search(SearchQuery(text="builders"))
        assert result.total_count == 1
        assert result.hits[0].date == "2026-07-10"
        assert result.hits[0].matched_contractor == "Alpha Builders"

    def test_search_by_contractor_code(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="GAM-003"))
        assert result.total_count == 1
        assert result.hits[0].matched_contractor_code == "GAM-003"
        assert "contractor_code" in result.hits[0].matched_fields

    def test_search_by_type(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="Civil"))
        assert [hit.date for hit in result.hits] == ["2026-07-12", "2026-07-10"]
        assert all("type" in hit.matched_fields for hit in result.hits)

    def test_search_by_zone(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="North"))
        assert result.total_count == 1
        assert result.hits[0].matched_zone == "North Yard"

    def test_search_by_details(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="masonry"))
        assert result.total_count == 1
        assert result.hits[0].date == "2026-07-10"
        assert "details" in result.hits[0].matched_fields

    def test_search_by_exact_date(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(exact_date="2026-07-11"))
        assert result.total_count == 1
        assert result.hits[0].date == "2026-07-11"
        assert "date" in result.hits[0].matched_fields

    def test_search_by_date_range(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(start_date="2026-07-10", end_date="2026-07-11"))
        assert [hit.date for hit in result.hits] == ["2026-07-11", "2026-07-10"]
        assert result.total_count == 2

    def test_search_by_worker_min_max(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(min_workers=10, max_workers=20))
        assert [hit.date for hit in result.hits] == ["2026-07-11", "2026-07-10"]
        assert all("workers" in hit.matched_fields for hit in result.hits)

    def test_search_by_numeric_worker_text(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="22"))
        assert result.total_count == 1
        assert result.hits[0].date == "2026-07-12"
        assert result.hits[0].matched_contractor == "Gamma Roads"

    def test_search_by_status(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(status=ReportStatus.NO_REPORT))
        assert result.total_count == 1
        assert result.hits[0].date == "2026-07-13"
        assert result.hits[0].status == ReportStatus.NO_REPORT

    def test_combined_text_and_status_filter(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="Alpha", status=ReportStatus.FINAL))
        assert result.total_count == 1
        assert result.hits[0].date == "2026-07-11"

    def test_empty_query_returns_no_results(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery())
        assert result.total_count == 0
        assert result.hits == []

    def test_no_results(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="not-present"))
        assert result.has_results is False
        assert result.total_pages == 1

    def test_pagination(self, search_repo: SearchRepository, seeded_reports):
        result_page_0 = search_repo.search(SearchQuery(start_date="2026-07-10", page=0, page_size=2))
        result_page_1 = search_repo.search(SearchQuery(start_date="2026-07-10", page=1, page_size=2))

        assert result_page_0.total_count == 4
        assert result_page_0.total_pages == 2
        assert [hit.date for hit in result_page_0.hits] == ["2026-07-13", "2026-07-12"]
        assert [hit.date for hit in result_page_1.hits] == ["2026-07-11", "2026-07-10"]

    def test_report_level_deduplication(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="Zone"))
        assert result.total_count == 2, "Report with two matching items should appear once"
        assert [hit.date for hit in result.hits] == ["2026-07-11", "2026-07-10"]

    def test_hit_includes_report_summary(self, search_repo: SearchRepository, seeded_reports):
        result = search_repo.search(SearchQuery(text="Beta"))
        hit = result.hits[0]
        assert hit.contractor_count == 2
        assert hit.total_workers == 15
        assert hit.status == ReportStatus.DRAFT

    def test_service_search_text(self, service: UniversalSearchService, seeded_reports):
        result = service.search_text("Alpha", page_size=1)
        assert result.total_count == 2
        assert result.total_pages == 2
        assert len(result.hits) == 1

    def test_service_convenience_methods(self, service: UniversalSearchService, seeded_reports):
        contractor_hits = service.search_by_contractor("Gamma")
        date_hits = service.search_by_date_range("2026-07-10", "2026-07-11")
        worker_hits = service.search_by_worker_count(20, 30)

        assert [hit.date for hit in contractor_hits] == ["2026-07-12"]
        assert [hit.date for hit in date_hits] == ["2026-07-11", "2026-07-10"]
        assert [hit.date for hit in worker_hits] == ["2026-07-12"]
