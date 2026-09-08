"""Search repository for Universal Search. (mventor-ticket-012)"""

from app.database.manager import DatabaseManager
from app.models.database import ReportStatus
from app.models.search import SearchHit, SearchQuery, SearchResult


class SearchRepository:
    """Runs SQL-based searches across reports and report items."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the search repository.

        Args:
            db_manager: Database manager used for parameterized SQLite queries.
        """
        self._db = db_manager

    def search(self, query: SearchQuery) -> SearchResult:
        """Execute a paginated universal search.

        Args:
            query: Structured search criteria.

        Returns:
            SearchResult containing report-level hits and total count.
        """
        if not query.has_criteria:
            return SearchResult(query=query, hits=[], total_count=0)

        total_count = self.count(query)
        if total_count == 0:
            return SearchResult(query=query, hits=[], total_count=0)

        select_sql, select_params = self._build_select(query)
        where_sql, where_params = self._build_where(query)
        limit = max(1, query.page_size)
        offset = query.offset

        rows = self._db.execute(
            f"""
            SELECT
                r.id AS report_id,
                r.date,
                r.day,
                r.status,
                (SELECT COUNT(*) FROM report_items all_items WHERE all_items.report_id = r.id) AS contractor_count,
                COALESCE((SELECT SUM(COALESCE(all_items.workers, 0))
                          FROM report_items all_items
                          WHERE all_items.report_id = r.id), 0) AS total_workers,
                {select_sql}
            FROM reports r
            LEFT JOIN report_items ri ON ri.report_id = r.id
            {where_sql}
            GROUP BY r.id
            ORDER BY relevance_score DESC, r.date DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(select_params + where_params + [limit, offset]),
        ).fetchall()

        hits = [self._row_to_hit(row, query) for row in rows]
        return SearchResult(query=query, hits=hits, total_count=total_count)

    def count(self, query: SearchQuery) -> int:
        """Count report-level matches for the query."""
        if not query.has_criteria:
            return 0

        where_sql, where_params = self._build_where(query)
        row = self._db.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM (
                SELECT r.id
                FROM reports r
                LEFT JOIN report_items ri ON ri.report_id = r.id
                {where_sql}
                GROUP BY r.id
            ) matches
            """,
            tuple(where_params),
        ).fetchone()
        return row["cnt"] if row else 0

    def _build_select(self, query: SearchQuery) -> tuple[str, list[object]]:
        """Build relevance and match summary SQL."""
        text = query.normalized_text.lower()
        params: list[object] = []

        if not text:
            return (
                "10 AS relevance_score, "
                "0 AS matched_date, 0 AS matched_status, 0 AS matched_day, "
                "0 AS matched_contractor, 0 AS matched_contractor_code, "
                "0 AS matched_type, 0 AS matched_zone, 0 AS matched_details, "
                "0 AS matched_workers, "
                "NULL AS matched_contractor_name, NULL AS matched_code_value, "
                "NULL AS matched_type_value, NULL AS matched_zone_value",
                params,
            )

        like = f"%{text}%"
        worker_text = text if text.isdigit() else "__no_worker_match__"

        score_params = [
            text, text, text, text, text, text, worker_text,
            like, like, like, like, like, like, like, like,
        ]
        matched_params = [
            like, like, like, like, like, like, like, like,
            worker_text,
            like, worker_text,
            like,
            like,
            like,
        ]
        params.extend(score_params + matched_params)

        return (
            """
            MAX(CASE
                WHEN LOWER(r.date) = ? THEN 100
                WHEN LOWER(r.status) = ? THEN 90
                WHEN LOWER(ri.contractor) = ? THEN 80
                WHEN LOWER(ri.contractor_code) = ? THEN 80
                WHEN LOWER(ri.type) = ? THEN 70
                WHEN LOWER(ri.zone) = ? THEN 70
                WHEN CAST(ri.workers AS TEXT) = ? THEN 65
                WHEN LOWER(r.date) LIKE ? THEN 50
                WHEN LOWER(r.status) LIKE ? THEN 45
                WHEN LOWER(ri.contractor) LIKE ? THEN 40
                WHEN LOWER(ri.contractor_code) LIKE ? THEN 40
                WHEN LOWER(ri.type) LIKE ? THEN 35
                WHEN LOWER(ri.zone) LIKE ? THEN 35
                WHEN LOWER(ri.details) LIKE ? THEN 25
                WHEN LOWER(r.day) LIKE ? THEN 20
                ELSE 0
            END) AS relevance_score,
            MAX(CASE WHEN LOWER(r.date) LIKE ? THEN 1 ELSE 0 END) AS matched_date,
            MAX(CASE WHEN LOWER(r.status) LIKE ? THEN 1 ELSE 0 END) AS matched_status,
            MAX(CASE WHEN LOWER(r.day) LIKE ? THEN 1 ELSE 0 END) AS matched_day,
            MAX(CASE WHEN LOWER(ri.contractor) LIKE ? THEN 1 ELSE 0 END) AS matched_contractor,
            MAX(CASE WHEN LOWER(ri.contractor_code) LIKE ? THEN 1 ELSE 0 END) AS matched_contractor_code,
            MAX(CASE WHEN LOWER(ri.type) LIKE ? THEN 1 ELSE 0 END) AS matched_type,
            MAX(CASE WHEN LOWER(ri.zone) LIKE ? THEN 1 ELSE 0 END) AS matched_zone,
            MAX(CASE WHEN LOWER(ri.details) LIKE ? THEN 1 ELSE 0 END) AS matched_details,
            MAX(CASE WHEN CAST(ri.workers AS TEXT) = ? THEN 1 ELSE 0 END) AS matched_workers,
            MAX(CASE WHEN LOWER(ri.contractor) LIKE ? OR CAST(ri.workers AS TEXT) = ? THEN ri.contractor END)
                AS matched_contractor_name,
            MAX(CASE WHEN LOWER(ri.contractor_code) LIKE ? THEN ri.contractor_code END) AS matched_code_value,
            MAX(CASE WHEN LOWER(ri.type) LIKE ? THEN ri.type END) AS matched_type_value,
            MAX(CASE WHEN LOWER(ri.zone) LIKE ? THEN ri.zone END) AS matched_zone_value
            """,
            params,
        )

    def _build_where(self, query: SearchQuery) -> tuple[str, list[object]]:
        """Build WHERE clause and parameters for the query."""
        conditions: list[str] = []
        params: list[object] = []
        text = query.normalized_text.lower()

        if text:
            like = f"%{text}%"
            worker_text = text if text.isdigit() else "__no_worker_match__"
            conditions.append(
                "(" 
                "LOWER(r.date) LIKE ? OR LOWER(r.day) LIKE ? OR "
                "LOWER(r.status) LIKE ? OR LOWER(ri.contractor) LIKE ? OR "
                "LOWER(ri.contractor_code) LIKE ? OR LOWER(ri.type) LIKE ? OR "
                "LOWER(ri.zone) LIKE ? OR LOWER(ri.details) LIKE ? OR "
                "CAST(ri.workers AS TEXT) = ?"
                ")"
            )
            params.extend([like, like, like, like, like, like, like, like, worker_text])

        if query.exact_date:
            conditions.append("r.date = ?")
            params.append(query.exact_date)
        if query.start_date:
            conditions.append("r.date >= ?")
            params.append(query.start_date)
        if query.end_date:
            conditions.append("r.date <= ?")
            params.append(query.end_date)
        if query.normalized_status:
            conditions.append("r.status = ?")
            params.append(query.normalized_status)
        if query.min_workers is not None:
            conditions.append("ri.workers >= ?")
            params.append(query.min_workers)
        if query.max_workers is not None:
            conditions.append("ri.workers <= ?")
            params.append(query.max_workers)

        if not conditions:
            return "", params
        return "WHERE " + " AND ".join(conditions), params

    def _row_to_hit(self, row, query: SearchQuery) -> SearchHit:
        """Map a database row to SearchHit."""
        matched_fields = self._matched_fields(row, query)
        return SearchHit(
            report_id=row["report_id"],
            date=row["date"],
            day=row["day"],
            status=ReportStatus(row["status"]),
            contractor_count=row["contractor_count"] or 0,
            total_workers=row["total_workers"] or 0,
            relevance_score=row["relevance_score"] or 0,
            matched_fields=matched_fields,
            matched_contractor=row["matched_contractor_name"],
            matched_contractor_code=row["matched_code_value"],
            matched_type=row["matched_type_value"],
            matched_zone=row["matched_zone_value"],
        )

    @staticmethod
    def _matched_fields(row, query: SearchQuery) -> list[str]:
        """Build a stable list of fields that matched."""
        fields: list[str] = []
        flag_map = [
            ("matched_date", "date"),
            ("matched_status", "status"),
            ("matched_day", "day"),
            ("matched_contractor", "contractor"),
            ("matched_contractor_code", "contractor_code"),
            ("matched_type", "type"),
            ("matched_zone", "zone"),
            ("matched_details", "details"),
            ("matched_workers", "workers"),
        ]
        for column, field in flag_map:
            if row[column]:
                fields.append(field)

        if query.exact_date or query.start_date or query.end_date:
            fields.append("date")
        if query.normalized_status:
            fields.append("status")
        if query.min_workers is not None or query.max_workers is not None:
            fields.append("workers")

        return list(dict.fromkeys(fields))
