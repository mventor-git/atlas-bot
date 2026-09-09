"""PDF generation package (stable import path).

Implementation lives in :mod:`app.libre.pdf` (headless LibreOffice).
This module re-exports the public API so existing imports keep working.
"""

from app.libre.pdf import PDFGenerator, find_soffice

__all__ = ["PDFGenerator", "find_soffice"]
