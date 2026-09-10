"""HR document rendering: fill .ots templates, embed receipts, PDF, print queue.

Templates carry [MARKERS] replaced verbatim, so EN and AR variants fill
with identical code. Language comes from config (default en).
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Optional

from odf.draw import Frame, Image
from odf.opendocument import load
from odf.text import P

from app.libre import ots
from app.libre.filler import LibreFillError
from app.models.config import AppConfig
from app.models.hr import HRRequest, HRRequestType
from app.utils.logger import get_logger

logger = get_logger(__name__)

PRINT_QUEUE = Path("exports/print_queue")


def template_for(request_type: str, config: AppConfig, lang: str | None = None) -> Path:
    """Resolve the HR template path for type + language."""
    language = (lang or getattr(config, "language", "en") or "en").lower()
    suffix = "_ar" if language.startswith("ar") else ""
    if request_type == HRRequestType.TRANSPORT:
        base = Path(config.template.hr_transport_template)
    else:
        base = Path(config.template.hr_advance_template)
    if suffix:
        candidate = base.parent / (base.stem + "_ar" + base.suffix)
        if candidate.exists():
            return candidate
    return base


def _sig_value(req: HRRequest, role: str, field: str, default: str = "") -> str:
    for sig in req.signatures or []:
        if sig.get("role") == role:
            value = sig.get(field, "") or ""
            if field == "at" and value:
                return str(value)[:10]
            return str(value)
    return default


def _sig_line(req: HRRequest, role: str) -> str:
    """Compact signature line: name + date (decision goes in its own cell)."""
    for sig in req.signatures or []:
        if sig.get("role") == role:
            name = sig.get("name", "")
            at = str(sig.get("at", ""))[:10]
            return ("%s, %s" % (name, at)).strip(", ")
    return ""


def fill_hr(req: HRRequest, config: AppConfig, output_path: str | Path | None = None,
            lang: str | None = None) -> str:
    """Fill the HR template for a request. Returns the .ods path."""
    if req.id is None:
        raise LibreFillError("HR request needs an ID before rendering.")
    template = template_for(req.request_type, config, lang)
    if not template.exists():
        raise FileNotFoundError(f"HR template not found: {template}")
    if output_path is None:
        output_path = config.docs_folder_path / f"HR-{req.id:04d}.ods"

    values = {
        "[REF]": f"HR-{req.id:04d}",
        "[DATE]": date.today().isoformat(),
        "[SITE]": req.site_id or "",
        "[REQUESTER]": req.requester_name or "",
        "[CHAT_ID]": req.requester_chat_id or "",
        "[AMOUNT]": ("%g" % req.amount),
        "[REASON]": req.reason or "",
        "[TRIP_DATE]": req.trip_date or "",
        "[REPORT_REF]": req.report_ref or "",
        "[DED_MONTH]": req.deduction_month or "",
        "[PM_DECISION]": _sig_value(req, "project_manager", "decision"),
        "[PM_SIG]": _sig_line(req, "project_manager"),
        "[HR_DECISION]": _sig_value(req, "hr", "decision"),
        "[HR_SIG]": _sig_line(req, "hr"),
        "[NOTE]": req.note or "",
    }
    try:
        doc = ots.load_doc(template)
        table = ots.first_table(doc)
        for row in table.getElementsByType(ots.TableRow):
            for cell in row.getElementsByType(ots.TableCell):
                text = ots.cell_text(cell)
                if "[" not in text or "]" not in text:
                    continue
                for marker, value in values.items():
                    if marker in text:
                        text = text.replace(marker, value)
                if "[RECEIPT]" not in text:
                    ots.set_cell_text(cell, text)
        out = ots.save(doc, output_path)
        if req.request_type == HRRequestType.TRANSPORT and req.receipt_path:
            _embed_receipt(out, req.receipt_path)
        logger.info("HR document filled: %s", out)
        return out
    except (FileNotFoundError, LibreFillError):
        raise
    except Exception as e:
        raise LibreFillError(
            f"Failed to fill HR template: {e}", original_exception=e) from e


def _embed_receipt(doc_path: str, receipt_path: str) -> None:
    """Embed a receipt photo into the [RECEIPT] cell (zip surgery)."""
    src = Path(receipt_path)
    if not src.exists():
        logger.warning("Receipt missing, skipped: %s", receipt_path)
        return
    doc = load(str(doc_path))
    table = ots.first_table(doc)
    target = None
    for row in table.getElementsByType(ots.TableRow):
        for cell in row.getElementsByType(ots.TableCell):
            if "[RECEIPT]" in ots.cell_text(cell):
                target = cell
                break
        if target is not None:
            break
    if target is None:
        logger.warning("No [RECEIPT] cell in %s", doc_path)
        return
    for p in list(target.getElementsByType(P)):
        target.removeChild(p)
    paragraph = P()
    frame = Frame(name="Receipt", width="8cm", height="6cm", x="0cm", y="0cm")
    frame.addElement(Image(href="Pictures/receipt.jpg"))
    paragraph.addElement(frame)
    target.addElement(paragraph)
    doc.save(str(doc_path))

    import zipfile

    with zipfile.ZipFile(doc_path, "r") as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}
    with open(src, "rb") as f:
        blobs["Pictures/receipt.jpg"] = f.read()
    manifest = blobs["META-INF/manifest.xml"].decode("utf-8")
    entry = ('<manifest:file-entry manifest:full-path="Pictures/receipt.jpg" '
             'manifest:media-type="image/jpeg"/>')
    if "Pictures/receipt.jpg" not in manifest:
        manifest = manifest.replace("</manifest:manifest>",
                                    " " + entry + "</manifest:manifest>")
    blobs["META-INF/manifest.xml"] = manifest.encode("utf-8")
    with zipfile.ZipFile(doc_path, "w") as z:
        for n in names:
            comp = zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED
            z.writestr(n, blobs[n], compress_type=comp)
        if "Pictures/receipt.jpg" not in names:
            z.writestr("Pictures/receipt.jpg", blobs["Pictures/receipt.jpg"],
                       compress_type=zipfile.ZIP_DEFLATED)


def render_pdf(req: HRRequest, config: AppConfig,
               lang: str | None = None) -> str:
    """Fill + convert an approved request to PDF. Returns the PDF path."""
    from app.libre.pdf import PDFGenerator

    if req.status != "approved":
        raise LibreFillError("PDF renders only for approved requests.")
    filled = fill_hr(req, config, lang=lang)
    pdf_path = str(Path(filled).with_suffix(".pdf"))
    pdf_path = Path(config.pdf_folder_path) / Path(pdf_path).name
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    return PDFGenerator(config).convert_to_pdf(filled, str(pdf_path))


def queue_for_print(pdf_path: str | Path) -> str:
    """Drop an approved PDF into the print queue (host watcher prints)."""
    PRINT_QUEUE.mkdir(parents=True, exist_ok=True)
    dest = PRINT_QUEUE / Path(pdf_path).name
    shutil.copy2(str(pdf_path), str(dest))
    logger.info("Queued for print: %s", dest)
    return str(dest)
