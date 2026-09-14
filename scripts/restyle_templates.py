"""Restyle Atlas document templates (owner request; ticket-035).

Style-only, content-neutral edits: NEVER touches cell text or markers,
row/column counts, widths, or font sizes on the paginated daily
templates (small/medium/large and contractor period keep the 1/2/3 page
baseline asserted by tests - only color/border/alignment are applied,
which cannot affect layout). Physical cells only (repeat runs carry one
style). Idempotent: own styles are prefixed a_* and dropped before
applying.

Palette (shared with scripts/build_hr_templates.py):
  navy #1b3a5f bands/titles   gold #c5a04a accent rules
  light #eaf0f7 fills         gray #6b7280 hints
  line #9aa5b1 grid borders

Usage:
    python scripts/restyle_templates.py            # all templates
    python scripts/restyle_templates.py <file>...  # selected
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from odf.opendocument import load                       # noqa: E402
from odf.style import (ParagraphProperties, Style,
                       TableCellProperties, TextProperties)  # noqa: E402
from odf.table import TableCell, TableRow                   # noqa: E402
from odf.text import P                                  # noqa: E402

from app.libre import ots                               # noqa: E402

NAVY = "#1b3a5f"
GOLD = "#c5a04a"
LIGHT = "#eaf0f7"
GRAY = "#6b7280"
LINE = "#9aa5b1"
WHITE = "#ffffff"
TINT = "#f5f8fc"

TARGETS = (
    "templates/contractor-daily-labor-template.ods",
    "templates/medium_template.ots",
    "templates/large_template.ots",
    "templates/empty-day.ots",
    "templates/contractor_report_template.ots",
    "templates/hr-advance-template.ots",
    "templates/hr-advance-template_ar.ots",
    "templates/acc-transport-template.ots",
    "templates/acc-transport-template_ar.ots",
)

HEADER_TOKENS = ("contractor", "workers", "zone", "details", "type",
                 "craftsmen", "helpers", "date", "added", "role",
                 "مقاول", "العمال", "مكان", "تفصيلي", "بند", "حرفي",
                 "مساعد", "بواسطة", "الدور", "التاريخ")
DASH_RUN = re.compile(r"^[─\s]+$")


def cells(row):
    return row.getElementsByType(TableCell)


def texts(row):
    return [ots.cell_text(c) for c in cells(row)]


def joined(row):
    return " ".join(texts(row)).strip()


class Styles:
    """Named a_* styles for one document (cell bg/border + text color/align)."""

    def __init__(self, doc):
        self.doc = doc
        self.band = self.style("band", bg=NAVY, border="0.5pt solid " + NAVY)
        self.band_t = self.text("bandt", color=WHITE, bold=True,
                                align="center")
        self.grid = self.style("grid", border="0.5pt solid " + LINE)
        self.total = self.style("total", bg=LIGHT,
                                border="0.5pt solid " + NAVY)
        self.total_t = self.text("totalt", color=NAVY, bold=True)
        self.label = self.style("label", bg=TINT)
        self.label_t = self.text("labelt", color=NAVY, bold=True)
        self.value_t = self.text("valuet")
        self.hint = self.text("hint", color=GRAY, italic=True, size="9pt",
                              align="center")
        self.hero = self.style("hero", bg=NAVY, border="0.5pt solid " + NAVY)
        self.hero_t = self.text("herot", color=WHITE, bold=True,
                                align="center", size="22pt")
        self.tint = self.style("tint", bg=TINT)
        self.center = self.text("center", align="center")
        self.right = self.text("right", color=NAVY, align="end")

    def style(self, name, *, bg=None, border=None):
        st = Style(name="a_" + name, family="table-cell")
        props = TableCellProperties()
        if bg:
            props.setAttribute("backgroundcolor", bg)
        if border:
            props.setAttribute("border", border)
        props.setAttribute("padding", "1mm")
        st.addElement(props)
        self.doc.styles.addElement(st)
        return "a_" + name

    def text(self, name, *, color=None, bold=False, italic=False, size=None,
             align=None):
        st = Style(name="a_" + name, family="text")
        tp = TextProperties()
        if color:
            tp.setAttribute("color", color)
        if bold:
            tp.setAttribute("fontweight", "bold")
        if italic:
            tp.setAttribute("fontstyle", "italic")
        if size:
            tp.setAttribute("fontsize", size)
        st.addElement(tp)
        if align:
            st.addElement(ParagraphProperties(textalign=align))
        self.doc.styles.addElement(st)
        return "a_" + name

    def clean(self):
        for holder in (self.doc.styles, self.doc.automaticstyles):
            for s in list(holder.getElementsByType(Style)):
                if str(s.attributes.get("style:name", "")).startswith("a_"):
                    holder.removeChild(s)


def paint(cell, cstyle, tstyle=None):
    cell.setAttribute("stylename", cstyle)
    if tstyle:
        for p in cell.getElementsByType(P):
            p.setAttribute("stylename", tstyle)


def paint_row(row, s, cstyle, tstyle=None):
    for c in cells(row):
        paint(c, cstyle, tstyle)


def classify(rows):
    header = total = None
    labels, hints = [], []
    for i, r in enumerate(rows):
        low = joined(r).casefold()
        flat = " ".join(texts(r))
        if header is None and sum(1 for t in HEADER_TOKENS if t in low) >= 3:
            header = i
        elif total is None and ("total" in low or "الإجمالي" in flat):
            total = i
        if re.search(r"^(day|date|اليوم|التاريخ)[:：]", flat.strip()):
            labels.append(i)
        if "automated report" in low or "تم الإنشاء بواسطة" in flat:
            hints.append(i)
    return header, total, set(labels), set(hints)


def restyle_daily(path: Path):
    doc = load(str(path))
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    tab = ots.first_table(doc)
    rows = tab.getElementsByType(TableRow)
    header, total, labels, hints = classify(rows)
    for i, r in enumerate(rows):
        if not joined(r) and not (header is not None and total is not None
                                  and header < i < total):
            continue
        flat = " ".join(texts(r))
        if flat.startswith("//"):
            continue                       # logo anchor row: untouched
        if i == header:
            paint_row(r, s, s.band, s.band_t)
        elif "Daily Labor" in flat or "بيان العمالة" in flat:
            paint_row(r, s, s.hero, s.hero_t)
        elif i == total:
            paint_row(r, s, s.total, s.total_t)
        elif i in labels:
            for c in cells(r):
                t = ots.cell_text(c)
                if ":" in t or ":" in t:
                    paint(c, s.label, s.label_t)
                else:
                    paint(c, s.grid, s.value_t)
        elif header is not None and total is not None and header < i < total:
            paint_row(r, s, s.grid)
        elif i in hints:
            paint_row(r, s, s.tint, s.hint)
    ots.save(doc, str(path))
    print("restyled(daily):", path.name)


def restyle_period(path: Path):
    doc = load(str(path))
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    tab = ots.first_table(doc)
    rows = tab.getElementsByType(TableRow)
    header, total, labels, hints = classify(rows)
    for i, r in enumerate(rows):
        flat = " ".join(texts(r)).strip()
        if not flat:
            continue
        low = flat.casefold()
        if i == 0:
            paint_row(r, s, s.hero, s.hero_t)
        elif "contractor:" in low or "[name]" in low:
            paint_row(r, s, s.label, s.label_t)
        elif i == header:
            paint_row(r, s, s.band, s.band_t)
        elif low.startswith("summary"):
            paint_row(r, s, s.total, s.total_t)
        elif header is not None and i > header:
            for c in cells(r):
                if ots.cell_text(c):
                    paint(c, s.grid, s.center)
    ots.save(doc, str(path))
    print("restyled(period):", path.name)


def restyle_empty(path: Path):
    doc = load(str(path))
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    tab = ots.first_table(doc)
    for i, r in enumerate(tab.getElementsByType(TableRow)):
        flat = " ".join(texts(r)).strip()
        if not flat:
            continue
        if "Atlas-Bot" in flat:
            paint_row(r, s, s.hero, s.hero_t)
        elif "No Labor" in flat or "لا عمالة" in flat:
            paint_row(r, s, s.band, s.band_t)
        else:
            paint_row(r, s, s.tint, s.hint)
    ots.save(doc, str(path))
    print("restyled(empty):", path.name)


def restyle_hr(path: Path):
    doc = load(str(path))
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    rule = s.style("rule", border="0.05cm solid " + GOLD)
    tab = ots.first_table(doc)
    replaced = 0
    for r in tab.getElementsByType(TableRow):
        for c in cells(r):
            t = ots.cell_text(c)
            if DASH_RUN.match(t or ""):
                for p in list(c.getElementsByType(P)):
                    c.removeChild(p)
                c.addElement(P())
                c.setAttribute("stylename", rule)
                replaced += 1
                continue
            if "[" in t:
                paint(c, s.label, s.value_t)     # [MARKER] value: tint
            elif t.endswith(":") or t.endswith(":"):
                for p in c.getElementsByType(P):
                    p.setAttribute("stylename", s.label_t)
    ots.save(doc, str(path))
    print("restyled(hr): %s (rules fixed: %d)" % (path.name, replaced))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    files = [Path(a) for a in argv] or [Path(t) for t in TARGETS]
    for f in files:
        name = f.name
        if name.startswith(("hr-advance", "acc-transport")):
            restyle_hr(f)
        elif name == "contractor_report_template.ots":
            restyle_period(f)
        elif name == "empty-day.ots":
            restyle_empty(f)
        elif name in ("contractor-daily-labor-template.ods",
                      "medium_template.ots", "large_template.ots"):
            restyle_daily(f)
        else:
            print("skip", name)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
