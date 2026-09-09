"""Master tables reader (stable import path).

Implementation lives in :mod:`app.libre.tables` (LibreOffice-native).
This module re-exports the public API so existing imports keep working.
"""

from app.libre.tables import TablesReaderError, TablesReaderService

__all__ = ["TablesReaderError", "TablesReaderService"]
