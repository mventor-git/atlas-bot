"""PDF generation package for Labor-Report.

Uses Microsoft Excel COM automation (pywin32) to export
filled workbooks directly to PDF, preserving all formatting.
"""

from app.pdf.generator import PDFGenerator

__all__: list[str] = [
    "PDFGenerator",
]
