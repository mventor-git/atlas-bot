"""Insert dedicated Craftsmen/Helpers columns before Details (mventor-ticket-024).

Applies Option B (owner-approved) to the three daily templates:

  B..F unchanged | G Craftsmen/الحرفيين | H Helpers/المساعدين | I was G Details

Rules honored: LibreOffice column-insert semantics - cell/column repeat runs
split at the insertion point, horizontal spans covering it grow by two,
everything else (styles, formulas, merges left of the point, page layout,
print behavior which none of these files defines) is untouched. Idempotent:
a template that already resolves split headers is skipped.

Usage:
    python scripts/add_split_columns_024.py            # dry-run report
    python scripts/add_split_columns_024.py --apply    # edit + verify
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from odf.opendocument import load                    # noqa: E402
from odf.table import CoveredTableCell              # noqa: E402
from odf.table import TableCell, TableColumn        # noqa: E402
from odf.table import TableRow, Table              # noqa: E402
from odf.text import P                              # noqa: E402

TARGETS = (
    "templates/contractor-daily-labor-template.ods",
    "templates/medium_template.ots",
    "templates/large_template.ots",
)
HEADERS = ("Craftsmen / \u0627\u0644\u062d\u0631\u0641\u064a\u064a\u0646",
           "Helpers / \u0627\u0644\u0645\u0633\u0627\u0639\u062f\u064a\u0646")
DETAIL_HINT = ("details", "\u0627\u0644\u062a\u0641\u0635\u064a\u0644\u064a")
SPLIT_HINT = ("craftsm", "\u062d\u0631\u0641\u064a")
INSERT_AT = 6  # physical/logical column of Details (G) in shipped layout


def _attr(el, name: str) -> int:
    return int(el.getAttribute(name) or 1)


def _text(cell) -> str:
    return "".join(
        "".join(str(n) for n in p.childNodes if n.nodeType == 3)
        for p in cell.getElementsByType(P))


def header_row(table):
    for row in table.getElementsByType(TableRow):
        texts = [(_text(c).casefold() if c is not None else "")
                 for c in _logical_cells(row)]
        if any("contractor" in t or "\u0645\u0642\u0627\u0648\u0644" in t
               for t in texts):
            return texts
    raise SystemExit("no header row found")


def _logical_cells(row):
    """Cells expanded so list index == logical column (empty runs only)."""
    out = []
    for cell in row.getElementsByType(TableCell):
        reps = _attr(cell, "numbercolumnsrepeated")
        span = _attr(cell, "numbercolumnsspanned")
        if reps > 1 and _text(cell):
            raise SystemExit("unexpected: repeated run with content")
        if span > 1:
            out.append(cell)          # span anchor stands alone (caller tracks)
            out.extend([None] * (span - 1))
        else:
            out.extend([cell] * reps)
    return out


def _physical_map(row):
    """[(element, start_logical, width)] covering walk, spans included."""
    out = []
    pos = 0
    for cell in row.getElementsByType(TableCell):
        reps = _attr(cell, "numbercolumnsrepeated")
        span = _attr(cell, "numbercolumnsspanned")
        if span > 1:
            out.append((cell, pos, span))
            pos += span
        else:
            out.append((cell, pos, reps))
            pos += reps
    return out, pos


def _blank_clone(cell: TableCell) -> TableCell:
    """Fresh empty cell keeping only the source style."""
    return _clone_style(cell)


def _clone_def(column: TableColumn) -> TableColumn:
    return copy.deepcopy(column)


def _split_columns(table) -> None:
    """Insert 2 narrow column defs before INSERT_AT (serial-width style).

    The inserted numeric columns clone the SERIAL column (1st narrow
    bordered column) so page width does not spill; existing columns keep
    their exact defs.
    """
    pos = 0
    serial_def = None
    for col in list(table.getElementsByType(TableColumn)):
        reps = _attr(col, "numbercolumnsrepeated")
        if pos <= 1 < pos + reps:
            serial_def = col
        if pos <= INSERT_AT < pos + reps:
            if INSERT_AT - pos == 0 and reps == 1 and serial_def is not None:
                table.insertBefore(_clone_def(serial_def), col)
                table.insertBefore(_clone_def(serial_def), col)
                return
            raise SystemExit("insertion not at a single column boundary")
        pos += reps
    raise SystemExit("column insertion point beyond table columns")


def _split_cells(row) -> None:
    """Insert 2 cells at INSERT_AT, styled like the previous (Workers) cell."""
    mapping, total = _physical_map(row)
    if total <= INSERT_AT:
        raise SystemExit("row does not cover insertion column")
    prev_cell = None
    for cell, start, width in mapping:
        if start <= 1 < start + width:      # serial column: narrow bordered
            prev_cell = cell
            break
        if start <= INSERT_AT - 1 < start + width and prev_cell is None:
            prev_cell = cell
    if prev_cell is None:
        raise SystemExit("no left neighbor cell to clone style")
    for cell, start, width in mapping:
        end = start + width
        if start < INSERT_AT < end:
            # horizontal span crossing the point: grows by two (LO semantics)
            span = _attr(cell, "numbercolumnsspanned")
            if span > 1 and start < INSERT_AT <= start + span - 1:
                cell.setAttribute("numbercolumnsspanned", str(span + 2))
                return
            inner = INSERT_AT - start
            reps = _attr(cell, "numbercolumnsrepeated")
            if reps > 1 and 0 < inner < reps:
                cell.setAttribute("numbercolumnsrepeated", str(inner))
                moved = copy.deepcopy(cell)
                moved.setAttribute("numbercolumnsrepeated", str(reps - inner))
                # run content belongs to its FIRST column only; tail empty
                for p in list(moved.getElementsByType(P)):
                    moved.removeChild(p)
                n1, n2 = _blank_clone(prev_cell), _blank_clone(prev_cell)
                ref = cell.nextSibling
                parent = cell.parentNode
                parent.insertBefore(n1, ref)
                parent.insertBefore(n2, ref)
                parent.insertBefore(moved, ref)
                return
            raise SystemExit("insertion inside unhandled cell structure")
        if start == INSERT_AT:
            ref = cell
            n1, n2 = _blank_clone(prev_cell), _blank_clone(prev_cell)
            row.insertBefore(n1, ref)
            row.insertBefore(n2, ref)
            return
    raise SystemExit("no boundary found at insertion column")


def _clone_style(cell: TableCell) -> TableCell:
    new = TableCell()
    if cell.getAttribute("stylename"):
        new.setAttribute("stylename", cell.getAttribute("stylename"))
    return new


def _label_headers(table) -> None:
    for row in table.getElementsByType(TableRow):
        cells = _physical_map(row)[0]
        texts = "".join(_text(c) for c, _, _ in cells).casefold()
        if not any(h in texts for h in DETAIL_HINT):
            continue
        if any(h in texts for h in SPLIT_HINT):
            return  # already labeled (idempotent)
        expanded = _logical_cells(row)
        for i, hdr in enumerate(HEADERS):
            cell = expanded[INSERT_AT + i]
            if cell is None:
                raise SystemExit("new header lands inside a merged span")
            for p in list(cell.getElementsByType(P)):
                cell.removeChild(p)
            p = P()
            p.addText(hdr)
            cell.addElement(p)
        return
    raise SystemExit("details header not found to label next to")


def process(path: Path, apply: bool) -> str:
    doc = load(str(path))
    table = doc.getElementsByType(Table)[0]
    texts = header_row(table)
    fold = [t.casefold() for t in texts]
    if any("craftsm" in t or "\u062d\u0631\u0641\u064a" in t for t in fold):
        return "skip (already split)"
    if INSERT_AT >= len(texts) or not any(
            h in " ".join(fold) for h in DETAIL_HINT):
        return "FAIL (layout differs from shipped expectation)"
    if texts[INSERT_AT] and not any(h in texts[INSERT_AT].casefold()
                                    for h in DETAIL_HINT):
        return "FAIL (insertion point not on Details label)"
    if apply:
        _split_columns(table)
        for row in table.getElementsByType(TableRow):
            _split_cells(row)
        _label_headers(table)
        doc.save(str(path))
        return "split added"
    return "would split"


def verify(path: Path) -> None:
    """Post-apply structural proof via the shipped filler's own lookup."""
    from app.libre.filler import TemplateFiller
    from app.libre import ots

    doc = ots.load_doc(str(path))
    table = ots.first_table(doc)
    hidx = ots.find_first(table, ("Contractor", "\u0627\u0644\u0645\u0642\u0627\u0648\u0644"))
    hdr = [ots.cell_text(c) for c in
           ots.logical_cells(table.getElementsByType(ots.TableRow)[hidx])]
    cols = TemplateFiller._locate_columns(hdr)
    assert cols["craftsmen"] == 6 and cols["helpers"] == 7, (hidx, cols)
    assert cols["details"] == 8 and cols["workers"] == 5, cols
    assert cols["contractor"] == 2 and cols["type"] == 3 and cols["zone"] == 4
    drow = ots.logical_cells(
        table.getElementsByType(ots.TableRow)[hidx + 1])
    assert len(drow) > INSERT_AT + 2
    print("verified", path.name, "->", cols)


def main() -> None:
    apply = "--apply" in sys.argv
    root = Path(__file__).resolve().parents[1]
    for rel in TARGETS:
        path = root / rel
        print(rel, "->", process(path, apply))
    if apply:
        for rel in TARGETS:
            verify(root / rel)


if __name__ == "__main__":
    main()
