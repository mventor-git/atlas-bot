"""Atlas-Bot document engine: LibreOffice-native .ots/.ods handling (odfpy).

No Excel anywhere. Templates are filled by DOM manipulation, PDFs are
rendered by headless ``soffice``.
"""

from app.libre.contractor_report import ContractorReportFiller
from app.libre.filler import LibreFillError, TemplateFiller
from app.libre.pdf import PDFGenerator
from app.libre.tables import TablesReaderError, TablesReaderService

__all__ = [
    "ContractorReportFiller",
    "LibreFillError",
    "PDFGenerator",
    "TablesReaderError",
    "TablesReaderService",
    "TemplateFiller",
]
