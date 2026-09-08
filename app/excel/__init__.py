"""Excel manipulation package for Labor-Report.

Handles reading/writing Excel files, template filling,
and dynamic table row insertion.
"""

from app.excel.template_filler import TemplateFiller, ExcelFillError

__all__ = [
    "TemplateFiller",
    "ExcelFillError",
]
