"""PDF generation via headless LibreOffice (``soffice``).

Replaces the legacy Windows-only COM backend. Works identically on host
and container. Requires ``soffice`` on PATH (or ``LIBREOFFICE_BIN`` set).

Usage:
    generator = PDFGenerator(config)
    pdf_path = generator.convert_to_pdf("path/to/file.ods")
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.models.config import AppConfig
from app.utils.exceptions import PDFError
from app.utils.logger import get_logger

logger = get_logger(__name__)

WIN_DEFAULT = r"C:\Program Files\LibreOffice\program\soffice.exe"


def find_soffice() -> str:
    """Locate the soffice binary or raise PDFError."""
    env = os.environ.get("LIBREOFFICE_BIN")
    if env and Path(env).exists():
        return env
    found = shutil.which("soffice")
    if found:
        return found
    if Path(WIN_DEFAULT).exists():
        return WIN_DEFAULT
    raise PDFError(
        "LibreOffice (soffice) not found. Install LibreOffice Still and "
        "ensure soffice is on PATH, or set LIBREOFFICE_BIN."
    )


class PDFGenerator:
    """Generates PDF files from .ods documents via headless soffice."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def convert_to_pdf(
        self,
        source_path: str,
        output_path: Optional[str] = None,
    ) -> str:
        """Convert a document to PDF.

        Args:
            source_path: Path to the source .ods file.
            output_path: Optional output PDF path (default: pdf folder,
                same stem). Different stems are handled via rename.

        Returns:
            Path to the generated PDF file.
        """
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(
                f"Source file not found for PDF conversion: {source_path}"
            )
        dest = Path(output_path) if output_path else (
            self._config.pdf_folder_path / f"{src.stem}.pdf"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)

        soffice = find_soffice()
        profile = Path(tempfile.mkdtemp(prefix="atlas-lo-")).as_posix()
        try:
            logger.info("Converting to PDF: %s -> %s", src, dest)
            proc = subprocess.run(
                [
                    soffice, "--headless",
                    f"-env:UserInstallation=file:///{profile}",
                    "--convert-to", "pdf",
                    "--outdir", str(dest.parent),
                    str(src.resolve()),
                ],
                capture_output=True, text=True, timeout=180,
            )
            generated = dest.parent / f"{src.stem}.pdf"
            if proc.returncode != 0 or not generated.exists():
                raise PDFError(
                    f"soffice conversion failed (rc={proc.returncode}): "
                    f"{(proc.stderr or proc.stdout)[-500:]}"
                )
            if generated.resolve() != dest.resolve():
                generated.replace(dest)
            logger.info("PDF generated: %s", dest)
            return str(dest.resolve())
        except PDFError:
            raise
        except FileNotFoundError:
            raise
        except Exception as e:
            raise PDFError(
                f"Failed to convert to PDF: {e}", original_exception=e
            ) from e
        finally:
            shutil.rmtree(profile, ignore_errors=True)
