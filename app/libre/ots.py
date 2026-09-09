"""Low-level .ots/.ods DOM helpers (odfpy).

Row element index == logical column only after :func:`expand_repeats`,
because LibreOffice stores runs of empty cells as a single element with
``numbercolumnsrepeated``.
"""

from __future__ import annotations

from pathlib import Path

from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.text import P


def load_doc(path: str | Path):
    """Load an .ots/.ods document."""
    return load(str(path))


def first_table(doc) -> Table:
    """Return the first spreadsheet table in the document."""
    tables = doc.getElementsByType(Table)
    if not tables:
        raise ValueError("No tables found in document")
    return tables[0]


def cell_text(cell: TableCell) -> str:
    """Concatenated text of a cell."""
    return "/".join(
        "".join(str(n) for n in p.childNodes if n.nodeType == 3)
        for p in cell.getElementsByType(P)
    )


def set_cell_text(cell: TableCell, text: str) -> None:
    """Replace a cell's content with a single paragraph of text."""
    for p in list(cell.getElementsByType(P)):
        cell.removeChild(p)
    p = P()
    p.addText(str(text))
    cell.addElement(p)


def row_cells(row: TableRow) -> list:
    """Cell elements of a row (snapshot list)."""
    return row.getElementsByType(TableCell)


def _fresh_cell_like(cell: TableCell) -> TableCell:
    """Empty cell carrying the source cell's style (for expanded slots)."""
    new = TableCell()
    style = cell.getAttribute("stylename")
    if style:
        new.setAttribute("stylename", style)
    return new


def expand_repeats(row: TableRow) -> None:
    """Expand repeated empty cells so element index == logical column.

    Only expands cells without text content; fresh cells inherit the
    source cell's style, so formatting is preserved.
    """
    for cell in row_cells(row):
        reps = int(cell.getAttribute("numbercolumnsrepeated") or 1)
        if reps <= 1:
            continue
        if cell_text(cell):
            continue  # never split cells holding content
        cell.removeAttribute("numbercolumnsrepeated")
        parent = cell.parentNode
        nxt = cell.nextSibling
        for _ in range(reps - 1):
            parent.insertBefore(_fresh_cell_like(cell), nxt)


def row_text(row: TableRow) -> str:
    """Joined text of a row (for marker search)."""
    return " ".join(cell_text(c) for c in row_cells(row))


def find_row(table: Table, marker: str, start: int = 0) -> int:
    """Index of first row at/after ``start`` containing ``marker``."""
    rows = table.getElementsByType(TableRow)
    for i in range(start, len(rows)):
        if marker in row_text(rows[i]):
            return i
    raise ValueError(f"Marker not found in template: {marker!r}")


def find_first(table: Table, markers, start: int = 0) -> int:
    """First row matching any marker (tried in order)."""
    last_error: ValueError | None = None
    for marker in markers:
        try:
            return find_row(table, marker, start)
        except ValueError as e:
            last_error = e
    raise ValueError(f"No markers found in template: {markers!r}") from last_error


def _row_signature(row: TableRow) -> tuple:
    """Style signature of a row: (row style, [cell styles])."""
    cells: list[str | None] = []
    for cell in row.getElementsByType(TableCell):
        reps = int(cell.getAttribute("numbercolumnsrepeated") or 1)
        cells.extend([cell.getAttribute("stylename")] * reps)
    return (row.getAttribute("stylename"), cells)


def _build_blank_row(sig: tuple) -> TableRow:
    """Fresh empty row from a style signature."""
    row_style, cell_styles = sig
    new_row = TableRow()
    if row_style:
        new_row.setAttribute("stylename", row_style)
    for style in cell_styles:
        cell = TableCell()
        if style:
            cell.setAttribute("stylename", style)
        new_row.addElement(cell)
    return new_row


def insert_blank_row(table: Table, idx: int, like: TableRow | None = None) -> int:
    """Insert an empty style-matching row after ``idx``.

    Args:
        table: The spreadsheet table.
        idx: Insert after this row index.
        like: Style source row (default: the row at ``idx``).

    Returns:
        Index of the new row.
    """
    rows = table.getElementsByType(TableRow)
    sig = _row_signature(like if like is not None else rows[idx])
    new_row = _build_blank_row(sig)
    ref = rows[idx + 1] if idx + 1 < len(rows) else None
    if ref is None:
        table.appendChild(new_row)
        return len(rows)
    table.insertBefore(new_row, ref)
    return idx + 1


def clone_row_after(table: Table, idx: int) -> int:
    """Insert an empty style-matching row after ``idx`` (for overflow items).

    Source data rows are empty at clone time, so a fresh row carrying the
    same row/cell styles renders identically.

    Returns:
        Index of the new row.
    """
    return insert_blank_row(table, idx)


def logical_cells(row: TableRow) -> list:
    """Cells expanded so ``cells[i]`` is logical column ``i``."""
    expand_repeats(row)
    return row_cells(row)


def save(doc, path: str | Path) -> str:
    """Save document; return resolved path string."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return str(out.resolve())
