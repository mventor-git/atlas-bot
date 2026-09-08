"""
Repository for report version management. (NEW v2.0)

Every report revision creates a version snapshot for recovery.
Versions are numbered sequentially and store the full report
state as JSON for single-row restore without complex joins.
"""

import json
from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus, ReportVersion
from app.repositories.base import BaseRepository
from app.repositories.report_repository import ReportRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class VersionRepository(BaseRepository[ReportVersion]):
    """Repository for report version snapshots.

    Creates a JSON snapshot of the full report state each time
    a report is finalized. Enables restore to any previous version.

    Usage:
        repo = VersionRepository(db_manager, report_repo)
        version = repo.create_version(report, "user123")
        versions = repo.get_versions(report_id)
    """

    def __init__(
        self, db_manager: DatabaseManager, report_repo: ReportRepository
    ) -> None:
        """Initialize the repository.

        Args:
            db_manager: The database manager instance.
            report_repo: Report repository for restore operations.
        """
        self._db = db_manager
        self._report_repo = report_repo

    # --- Version creation ---

    def create_version(
        self,
        report: Report,
        telegram_user: Optional[str] = None,
        change_summary: Optional[str] = None,
    ) -> ReportVersion:
        """Create a version snapshot of the report.

        Serializes the full report state (including items) to JSON
        and stores it with the next version number.

        Args:
            report: The report to snapshot.
            telegram_user: Who triggered this version.
            change_summary: Description of what changed.

        Returns:
            The created ReportVersion.

        Raises:
            DatabaseError: If the report has no ID.
        """
        if report.id is None:
            raise DatabaseError("Cannot create version for a report without an ID.")

        # Get next version number
        last_version = self._db.execute(
            "SELECT MAX(version_number) as max_ver FROM report_versions WHERE report_id = ?",
            (report.id,),
        ).fetchone()
        next_version = (last_version["max_ver"] or 0) + 1

        # Serialize report state to JSON
        snapshot = self._serialize_report(report)

        version = ReportVersion(
            report_id=report.id,
            version_number=next_version,
            snapshot=snapshot,
            created_at=datetime.now().isoformat(),
            created_by=telegram_user,
            change_summary=change_summary,
        )
        result = self.add(version)
        logger.info(
            "Version %d created for report %s (date=%s)",
            next_version, report.id, report.date,
        )
        return result

    def restore_version(self, version_id: int, telegram_user: str) -> Report:
        """Restore a previous version as a new draft.

        Deserializes the snapshot and creates a new draft report
        for the same date (the existing report is not modified).

        Args:
            version_id: ID of the version to restore.
            telegram_user: Who is restoring this version.

        Returns:
            A new draft Report with the version's data.

        Raises:
            DatabaseError: If the version is not found or restore fails.
        """
        version = self.get_by_id(version_id)
        if version is None:
            raise DatabaseError(f"Version with id {version_id} not found.")

        report = self._deserialize_report(version.snapshot)
        report.id = None  # Force new report
        report.status = ReportStatus.DRAFT
        report.telegram_user = telegram_user
        report.created_at = datetime.now().isoformat()
        report.updated_at = datetime.now().isoformat()
        report.finalized_at = None
        report.locked_at = None
        report.locked_by = None
        report.source_date = report.date  # Track original date

        logger.info(
            "Version %d restored as draft for report date=%s",
            version_id, report.date,
        )
        return report

    # --- Query methods ---

    def get_versions(self, report_id: int) -> list[ReportVersion]:
        """Get all versions for a report, ordered by version number.

        Args:
            report_id: The report ID.

        Returns:
            List of ReportVersion objects, oldest first.
        """
        rows = self._db.execute(
            """SELECT * FROM report_versions
               WHERE report_id = ?
               ORDER BY version_number ASC""",
            (report_id,),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_latest_version(self, report_id: int) -> Optional[ReportVersion]:
        """Get the latest version for a report.

        Args:
            report_id: The report ID.

        Returns:
            The most recent ReportVersion, or None.
        """
        row = self._db.execute(
            """SELECT * FROM report_versions
               WHERE report_id = ?
               ORDER BY version_number DESC
               LIMIT 1""",
            (report_id,),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def prune_versions(self, report_id: int, max_versions: int) -> int:
        """Remove oldest versions exceeding the limit.

        Args:
            report_id: The report ID.
            max_versions: Maximum number of versions to keep.

        Returns:
            Number of versions removed.
        """
        # Count current versions
        count_row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM report_versions WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        count = count_row["cnt"] if count_row else 0

        if count <= max_versions:
            return 0

        to_delete = count - max_versions
        # Delete oldest versions
        self._db.execute(
            """DELETE FROM report_versions
               WHERE id IN (
                   SELECT id FROM report_versions
                   WHERE report_id = ?
                   ORDER BY version_number ASC
                   LIMIT ?
               )""",
            (report_id, to_delete),
        )
        self._db.commit()
        logger.info(
            "Pruned %d versions for report %s (kept %d)",
            to_delete, report_id, max_versions,
        )
        return to_delete

    # --- BaseRepository implementation ---

    def get_by_id(self, entity_id: int) -> Optional[ReportVersion]:
        row = self._db.execute(
            "SELECT * FROM report_versions WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self) -> list[ReportVersion]:
        rows = self._db.execute(
            "SELECT * FROM report_versions ORDER BY report_id, version_number"
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def add(self, entity: ReportVersion) -> ReportVersion:
        cursor = self._db.execute(
            """INSERT INTO report_versions
               (report_id, version_number, snapshot, created_at, created_by, change_summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entity.report_id,
                entity.version_number,
                entity.snapshot,
                entity.created_at,
                entity.created_by,
                entity.change_summary,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def update(self, entity: ReportVersion) -> ReportVersion:
        if entity.id is None:
            raise DatabaseError("Cannot update a ReportVersion without an ID.")
        self._db.execute(
            """UPDATE report_versions
               SET snapshot=?, change_summary=?
               WHERE id=?""",
            (entity.snapshot, entity.change_summary, entity.id),
        )
        self._db.commit()
        return entity

    def delete(self, entity_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM report_versions WHERE id = ?", (entity_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM report_versions"
        ).fetchone()
        return row["cnt"] if row else 0

    # --- Private ---

    @staticmethod
    def _serialize_report(report: Report) -> str:
        """Serialize a Report to JSON string.

        Args:
            report: The report to serialize.

        Returns:
            JSON string of the report state.
        """
        data = {
            "date": report.date,
            "day": report.day,
            "status": report.status.value,
            "telegram_user": report.telegram_user,
            "pdf_path": report.pdf_path,
            "excel_path": report.excel_path,
            "preview_pdf_path": report.preview_pdf_path,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
            "source_date": report.source_date,
            "items": [
                {
                    "contractor": item.contractor,
                    "type": item.type,
                    "zone": item.zone,
                    "workers": item.workers,
                    "details": item.details,
                    "contractor_code": item.contractor_code,
                }
                for item in report.items
            ],
        }
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _deserialize_report(snapshot: str) -> Report:
        """Deserialize a JSON string to a Report.

        Args:
            snapshot: JSON string of the report state.

        Returns:
            Report object with items.
        """
        data = json.loads(snapshot)
        items = [
            ReportItem(
                contractor=item["contractor"],
                type=item.get("type"),
                zone=item.get("zone"),
                workers=item.get("workers"),
                details=item.get("details"),
                contractor_code=item.get("contractor_code"),
            )
            for item in data.get("items", [])
        ]
        return Report(
            date=data["date"],
            day=data["day"],
            status=ReportStatus(data.get("status", "draft")),
            telegram_user=data.get("telegram_user"),
            pdf_path=data.get("pdf_path"),
            excel_path=data.get("excel_path"),
            preview_pdf_path=data.get("preview_pdf_path"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            source_date=data.get("source_date"),
            items=items,
        )

    @staticmethod
    def _row_to_model(row) -> ReportVersion:
        return ReportVersion(
            id=row["id"],
            report_id=row["report_id"],
            version_number=row["version_number"],
            snapshot=row["snapshot"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            change_summary=row["change_summary"],
        )
