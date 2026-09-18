"""Shared construction-management design system (code-owned layout).

Owns presentation near the ``app.libre.ots`` DOM seam because
``scripts/restyle_templates.py`` is style-only by contract (no widths,
row counts, page setup). Business logic stays in fillers; this module
only paints + paginates. ``a_*`` naming reuses the restyle convention.
"""

from __future__ import annotations

import re

from odf.style import (
    Footer,
    MasterPage,
    PageLayout,
    PageLayoutProperties,
    ParagraphProperties,
    Style,
    TableCellProperties,
    TableColumnProperties,
    TableRowProperties,
    TextProperties,
)
from odf.table import TableColumn, TableRow
from odf.text import P, PageNumber

from app.libre import ots

FONTS = "Arial, Liberation Sans, DejaVu Sans, sans-serif"
# Design tokens: professional construction-management, restrained.
HEADER_BG = "#1F4E79"
HEADER_FG = "#FFFFFF"
BAND = "#D9E2F3"
GRID = "#B0B0B0"
GRID_SPEC = "0.5pt solid %s" % GRID
TITLE_SIZE = "16pt"
LABEL_SIZE = "10pt"
BODY_SIZE = "10.5pt"
FOOT_SIZE = "8.5pt"
FOOT_GRAY = "#595959"
# ponytail: fixed widths tuned to A4-landscape printable width (~27.7cm).
DAILY_WIDTHS = ("1.2cm", "5.5cm", "3.5cm", "3.5cm", "2.5cm",
                "2.5cm", "2.5cm", "6.0cm")
CONTRACTOR_WIDTHS = ("1.0cm", "3.0cm", "2.5cm", "3.0cm", "8.0cm",
                     "4.2cm", "3.0cm", "3.0cm")
NUMERIC_FIELDS = ("workers", "craftsmen", "helpers")
ADVANCE_WIDTHS = ("4.2cm", "5.0cm", "4.2cm", "4.3cm")


class Styles:
    """a_* cell/text styles for one document (idempotent via clean)."""

    def __init__(self, doc):
        self.doc = doc
        self.hdr = self.cell("hdr", bg=HEADER_BG, border=GRID_SPEC)
        self.hdr_t = self.text("hdrt", color=HEADER_FG, bold=True,
                               size=BODY_SIZE, align="center")
        self.band = self.cell("band", bg=BAND, border=GRID_SPEC)
        self.grid = self.cell("grid", border=GRID_SPEC)
        self.grid_c = self.text("gridc", align="center", size=BODY_SIZE)
        self.grid_l = self.text("gridl", align="start", size=BODY_SIZE)
        self.title = self.cell("title", border=None)
        self.title_t = self.text("titlet", bold=True, size=TITLE_SIZE,
                                 align="center")
        self.meta_l = self.cell("metal", border=None)
        self.meta_lt = self.text("metalt", bold=True, size=LABEL_SIZE,
                                 align="start")
        self.meta_v = self.text("metav", size=BODY_SIZE, align="start")
        self.total = self.cell("total", bg=BAND, border=GRID_SPEC)
        self.total_t = self.text("totalt", bold=True, size=BODY_SIZE,
                                 align="center")
        self.foot = self.cell("foot", border=None)
        self.foot_t = self.text("foott", color=FOOT_GRAY, size=FOOT_SIZE,
                                align="center")
        self.head_row = self.rowstyle("ahead", minheight="0.9cm")

    def cell(self, name, *, bg=None, border=None, valign=None,
               borderbottom=None, bordertop=None):
        st = Style(name="a_" + name, family="table-cell")
        props = TableCellProperties()
        if bg:
            props.setAttribute("backgroundcolor", bg)
        if border:
            props.setAttribute("border", border)
        if borderbottom:
            props.setAttribute("borderbottom", borderbottom)
        if bordertop:
            props.setAttribute("bordertop", bordertop)
        if valign:
            props.setAttribute("verticalalign", valign)
        props.setAttribute("padding", "1.2mm")
        st.addElement(props)
        self.doc.styles.addElement(st)
        return "a_" + name

    def text(self, name, *, color=None, bold=False, size=None, align=None,
               writingmode=None):
        st = Style(name="a_" + name, family="text")
        tp = TextProperties(fontfamily=FONTS)
        if color:
            tp.setAttribute("color", color)
        if bold:
            tp.setAttribute("fontweight", "bold")
        if size:
            tp.setAttribute("fontsize", size)
        st.addElement(tp)
        if align or writingmode:
            pp = ParagraphProperties()
            if align:
                pp.setAttribute("textalign", align)
            if writingmode:
                pp.setAttribute("writingmode", writingmode)
            st.addElement(pp)
        self.doc.styles.addElement(st)
        return "a_" + name

    def rowstyle(self, name, *, minheight=None):
        st = Style(name=name, family="table-row")
        if minheight:
            st.addElement(TableRowProperties(minrowheight=minheight))
        self.doc.automaticstyles.addElement(st)
        return name

    def clean(self):
        for holder in (self.doc.styles, self.doc.automaticstyles):
            for s in list(holder.getElementsByType(Style)):
                if str(s.getAttribute("name") or "").startswith("a_"):
                    holder.removeChild(s)


def _paint(cell, cstyle, tstyle=None):
    cell.setAttribute("stylename", cstyle)
    if tstyle:
        for p in cell.getElementsByType(P):
            p.setAttribute("stylename", tstyle)


def ensure_page(doc, table, nrows: int, widths=DAILY_WIDTHS,
                orient="landscape", margintop="1cm",
                footer_style=None) -> None:
    """A4 page + margins + fixed widths + print-range + footer."""
    for mp in list(doc.masterstyles.getElementsByType(MasterPage)):
        doc.masterstyles.removeChild(mp)
    for pl in list(doc.automaticstyles.getElementsByType(PageLayout)):
        if str(pl.getAttribute("name") or "").startswith("atlas_"):
            doc.automaticstyles.removeChild(pl)
    portrait = orient == "portrait"
    layout = PageLayout(name="atlas_a4_port" if portrait else "atlas_a4_land")
    layout.addElement(PageLayoutProperties(
        pagewidth="21cm" if portrait else "29.7cm",
        pageheight="29.7cm" if portrait else "21cm",
        printorientation="portrait" if portrait else "landscape",
        margintop=margintop, marginbottom="1cm",
        marginleft="1cm", marginright="1cm"))
    doc.automaticstyles.addElement(layout)
    master = MasterPage(name="Default", pagelayoutname="atlas_a4_port"
                        if portrait else "atlas_a4_land")
    footer = Footer()
    fp = P()
    if footer_style:
        fp.setAttribute("stylename", footer_style)
    fp.addText("Generated by Atlas-Bot | ")
    fp.addElement(PageNumber())
    footer.addElement(fp)
    master.addElement(footer)
    doc.masterstyles.addElement(master)
    tabstyle = Style(name="tab_daily", family="table",
                     masterpagename="Default")
    doc.automaticstyles.addElement(tabstyle)
    table.setAttribute("stylename", "tab_daily")
    # Fixed column widths (drop + rebuild column defs).
    for col in list(table.getElementsByType(TableColumn)):
        table.removeChild(col)
    for i, w in enumerate(widths):
        cs = Style(name="a_col%d" % i, family="table-column")
        cs.addElement(TableColumnProperties(columnwidth=w))
        doc.automaticstyles.addElement(cs)
        col = TableColumn()
        col.setAttribute("stylename", "a_col%d" % i)
        table.insertBefore(col, table.firstChild)
    # Print-range locked to content kills the blank 2nd page.
    lastcol = chr(ord("A") + len(widths) - 1)  # ponytail: <=8 cols, no AA logic
    table.setAttribute("printranges", "A1:%s%d" % (lastcol, max(nrows, 1)))


def apply_daily(doc, table, header_idx: int, totals_idx: int,
                cols: dict) -> None:
    """Paint Daily sheet: title/meta/header/banding/total/footer."""
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    rows = table.getElementsByType(TableRow)
    nrows = len(rows)
    # Title row (Daily Labor ...), metadata rows (Day:/Date:).
    for i, r in enumerate(rows):
        flat = ots.row_text(r)
        if "Daily Labor" in flat or "بيان العمالة" in flat:
            for c in ots.logical_cells(r):
                if c is not None:
                    _paint(c, s.title, s.title_t)
        if i < header_idx and (":" in flat or "：" in flat):
            cells = ots.logical_cells(r)
            for n, c in enumerate(cells):
                if c is None:
                    continue
                t = ots.cell_text(c)
                _paint(c, s.meta_l, s.meta_lt if (":" in t) else s.meta_v)
    # Header row: locked height + zebra header (repeat = first content
    # row under print-range; multi-page repeat stays template-side).
    header_row = rows[header_idx]
    header_row.setAttribute("stylename", s.head_row)
    for c in ots.logical_cells(header_row):
        if c is not None:
            _paint(c, s.hdr, s.hdr_t)
    # Data banding (fixes clone duplication: repaint post-fill) + alignment.
    num = {cols.get(f) for f in NUMERIC_FIELDS if cols.get(f) is not None}
    contractor = cols.get("contractor")
    for n, i in enumerate(range(header_idx + 1, totals_idx)):
        r = table.getElementsByType(TableRow)[i]
        cells = ots.logical_cells(r)
        for j, c in enumerate(cells):
            if c is None:
                continue
            tstyle = (s.grid_c if j in num
                      else s.grid_l if j == contractor else s.grid_c)
            _paint(c, s.band if n % 2 else s.grid, tstyle)
    # Summary + footer provenance.
    for c in ots.logical_cells(rows[totals_idx]):
        if c is not None:
            _paint(c, s.total, s.total_t)
    for i in range(totals_idx + 1, len(rows)):
        flat = ots.row_text(rows[i])
        if flat.strip() and "Atlas-Bot" in flat or "Automated Report" in flat:
            for c in ots.logical_cells(rows[i]):
                if c is not None:
                    _paint(c, s.foot, s.foot_t)
    # Collapse trailing blank rows after last content (no removeChild:
    # odfpy cache breaks on row delete after cell expansion) -> no 2nd page.
    rows = table.getElementsByType(TableRow)
    last = totals_idx + 1
    for i in range(totals_idx + 1, len(rows)):
        if ots.row_text(rows[i]).strip():
            last = i
    for i in range(last + 1, len(rows)):
        if not ots.row_text(rows[i]).strip():
            rows[i].setAttribute("visibility", "collapse")
    ensure_page(doc, table, last + 1)


# --- METADATA ENGINE (v3): doc-type -> ordered cover rows. ---
# ponytail: plain dict, other doc-types stubbed until built.
COVER_META: dict[str, list[str]] = {
    "daily": ["company", "site", "date", "automated-by"],
    "contractor": ["company", "contractor", "period", "automated-by"],
    "advance": ["company", "requester", "date", "automated-by"],
}

COVER_LABELS: dict[str, str] = {
    "company": "Company", "site": "Site", "date": "Date",
    "automated-by": "Automated By", "contractor": "Contractor",
    "period": "Period", "requester": "Requester",
}


def cover_rows(doc_type: str, ctx: dict) -> list[tuple[str, str]]:
    """Ordered (label, value) cover rows. Unknown doc-types return []."""
    keys = COVER_META.get(doc_type, [])
    out = []
    for k in keys:
        if k == "automated-by":
            out.append((COVER_LABELS[k], str(ctx.get(k, "Atlas-bot"))))
        elif k in ctx:
            out.append((COVER_LABELS.get(k, k), str(ctx[k])))
    return out


# --- Daily v2: user overrides (EN-only, all centered, all white,
# top-anchored portrait A4). v1 apply_daily stays intact above. ---
V2_GRID = "#D0D0D0"
V2_HAIRLINE = "0.5pt solid %s" % V2_GRID
V2_TITLE_SIZE = "17pt"
V2_HDR_RULE = "1pt solid #000000"
# ponytail: portrait printable is 19cm (21 - 2x1cm margins); scaled v1 mix.
DAILY_V2_WIDTHS = ("0.9cm", "3.8cm", "2.4cm", "2.4cm", "1.8cm",
                   "1.8cm", "1.8cm", "4.1cm")
_AR = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+")


def _en_only(text: str) -> str:
    """English half of a 'EN / AR' chrome label (data cells never pass)."""
    t = text.split(" / ")[0] if " / " in text else text
    return " ".join(_AR.sub("", t).split()).rstrip("/").strip()


def apply_daily_v2(doc, table, header_idx: int, totals_idx: int,
                    cols: dict) -> None:
    """Paint Daily v2: EN-only chrome, centered, white, portrait A4."""
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    hair = s.cell("v2hair", border=V2_HAIRLINE, valign="middle")
    hair_t = s.text("v2hairt", size=BODY_SIZE, align="center")
    plain = s.cell("v2plain", border=None, valign="middle")
    hdr = s.cell("v2hdr", border=V2_HAIRLINE, borderbottom=V2_HDR_RULE,
                 valign="middle")
    hdr_t = s.text("v2hdrt", bold=True, size=BODY_SIZE, align="center")
    title_t = s.text("v2titlet", bold=True, size=V2_TITLE_SIZE,
                     align="center")
    meta_t = s.text("v2metat", bold=True, size=LABEL_SIZE, align="center")
    meta_v = s.text("v2metav", size=BODY_SIZE, align="center")
    total = s.cell("v2total", border=V2_HAIRLINE, bordertop=V2_HDR_RULE,
                   valign="middle")
    total_t = s.text("v2totalt", bold=True, size=BODY_SIZE, align="center")
    # Footer paragraph style: 8.5pt grey, centered, pinned via master page.
    fsp = Style(name="a_v2footp", family="paragraph")
    fsp.addElement(TextProperties(fontfamily=FONTS, color=FOOT_GRAY,
                                  fontsize=FOOT_SIZE))
    fsp.addElement(ParagraphProperties(textalign="center"))
    doc.styles.addElement(fsp)
    rows = table.getElementsByType(TableRow)
    for i, r in enumerate(rows):
        flat = ots.row_text(r)
        if "Daily Labor" in flat or "بيان العمالة" in flat:
            for c in ots.logical_cells(r):
                if c is None:
                    continue
                if "Daily Labor" in ots.cell_text(c):
                    ots.set_cell_text(c, "Daily Labor Report")
                _paint(c, plain, title_t)
        if i < header_idx and (":" in flat or "：" in flat):
            cells = ots.logical_cells(r)
            for c in cells:
                if c is None:
                    continue
                t = ots.cell_text(c)
                if not t.strip():
                    continue
                if " / " in t or _AR.search(t):
                    ots.set_cell_text(c, _en_only(t))
                    t = ots.cell_text(c)
                _paint(c, plain, meta_t if (":" in t) else meta_v)
    header_row = rows[header_idx]
    header_row.setAttribute("stylename", s.head_row)
    for c in ots.logical_cells(header_row):
        if c is None:
            continue
        t = ots.cell_text(c)
        if t.strip() and (" / " in t or _AR.search(t)):
            ots.set_cell_text(c, _en_only(t))
        _paint(c, hdr, hdr_t)
    for i in range(header_idx + 1, totals_idx):
        r = table.getElementsByType(TableRow)[i]
        for c in ots.logical_cells(r):
            if c is not None:
                _paint(c, hair, hair_t)
    for c in ots.logical_cells(rows[totals_idx]):
        if c is None:
            continue
        t = ots.cell_text(c)
        if t.strip() and (" / " in t or _AR.search(t)):
            ots.set_cell_text(c, _en_only(t))
        _paint(c, total, total_t)
    rows = table.getElementsByType(TableRow)
    last = totals_idx
    for i in range(totals_idx + 1, len(rows)):
        if ots.row_text(rows[i]).strip():
            last = i
    for i in range(last + 1, len(rows)):
        if not ots.row_text(rows[i]).strip():
            rows[i].setAttribute("visibility", "collapse")
    ensure_page(doc, table, last + 1, widths=DAILY_V2_WIDTHS,
                orient="portrait", margintop="0.7cm",
                footer_style="a_v2footp")


def apply_contractor(doc, table, info_idx: int, header_idx: int,
                     summary_idx: int) -> None:
    """Paint Contractor Period sheet: header/context/table/summary/footer.

    Period-summary + table composition (not a Daily copy): title row 0,
    context row, 6-col data banding (workers centered, details left),
    anchored summary directly after data + generated provenance row.
    Slice to 8 cols: template pads to 16k logical cols via repeats.
    Repeating header skipped (known odfpy reparent limit, template-side).
    """
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    rows = table.getElementsByType(TableRow)
    NCOLS = len(CONTRACTOR_WIDTHS)
    for c in ots.logical_cells(rows[0])[:NCOLS]:
        if c is not None:
            _paint(c, s.title, s.title_t)
    for c in ots.logical_cells(rows[info_idx])[:NCOLS]:
        if c is None:
            continue
        t = ots.cell_text(c)
        _paint(c, s.meta_l, s.meta_lt if (":" in t or "|" in t) else s.meta_v)
    header_row = rows[header_idx]
    header_row.setAttribute("stylename", s.head_row)
    for c in ots.logical_cells(header_row)[:NCOLS]:
        if c is not None:
            _paint(c, s.hdr, s.hdr_t)
    htexts = [ots.cell_text(c).casefold()
              for c in ots.logical_cells(header_row)[:NCOLS]]
    num = {i for i, t in enumerate(htexts) if "worker" in t}
    left = {i for i, t in enumerate(htexts) if "detail" in t}
    for n, i in enumerate(range(header_idx + 1, summary_idx)):
        for j, c in enumerate(ots.logical_cells(rows[i])[:NCOLS]):
            if c is None:
                continue
            tstyle = (s.grid_c if j in num
                      else s.grid_l if j in left else s.grid_c)
            _paint(c, s.band if n % 2 else s.grid, tstyle)
    for c in ots.logical_cells(rows[summary_idx])[:NCOLS]:
        if c is not None:
            _paint(c, s.total, s.total_t)
    # Footer provenance anchored right after summary (not floating).
    foot_idx = summary_idx + 1
    if foot_idx >= len(rows):
        foot_idx = ots.insert_blank_row(table, summary_idx)
        rows = table.getElementsByType(TableRow)
    foot = ots.logical_cells(rows[foot_idx])
    if not ots.row_text(rows[foot_idx]).strip():
        ots.set_cell_text(foot[0], "Generated by Atlas-Bot | Automated Report")
    for c in foot[:NCOLS]:
        if c is not None:
            _paint(c, s.foot, s.foot_t)
    rows = table.getElementsByType(TableRow)
    last = foot_idx
    for i in range(foot_idx + 1, len(rows)):
        if ots.row_text(rows[i]).strip():
            last = i
    for i in range(last + 1, len(rows)):
        if not ots.row_text(rows[i]).strip():
            rows[i].setAttribute("visibility", "collapse")
    ensure_page(doc, table, last + 1, widths=CONTRACTOR_WIDTHS)


# --- salary-advance form + workflow primitives (EN LTR / AR RTL) ---
def form_pair(cells, s: Styles, label_t: str, value_t: str,
              value_cell: str) -> None:
    """Paired label/value cells: even cols label, odd cols value (order kept)."""
    for j, c in enumerate(cells):
        if c is None:
            continue
        if not ots.cell_text(c).strip():
            continue
        if j % 2 == 0:
            _paint(c, s.meta_l, label_t)
        else:
            _paint(c, value_cell, value_t)


def workflow_step(cells, s: Styles, label_t: str, value_t: str,
                  band: bool) -> None:
    """One aligned approval step row (decision + approver, same columns)."""
    form_pair(cells, s, label_t, value_t, s.band if band else s.grid)


def apply_advance(doc, table, rtl: bool = False) -> None:
    """Paint Salary Advance: 5-section admin form (title/identity/requester/
    financial/workflow/archive+footer). Post-fill only; text untouched.
    RTL: writingmode=rl-tb paragraph styles, column order kept, fixed
    widths so Arabic wraps inside value cells."""
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    if rtl:
        label_t = s.text("advlrtl", bold=True, size=LABEL_SIZE,
                         align="start", writingmode="rl-tb")
        value_t = s.text("advvrtl", size=BODY_SIZE, align="start",
                         writingmode="rl-tb")
        title_t = s.text("advtrtl", color=HEADER_FG, bold=True,
                         size=TITLE_SIZE, align="center",
                         writingmode="rl-tb")
        sec_t = s.text("advsrtl", color=HEADER_FG, bold=True,
                       size=BODY_SIZE, align="center",
                       writingmode="rl-tb")
        foot_t = s.text("advfrtl", color=FOOT_GRAY, size=FOOT_SIZE,
                        align="center", writingmode="rl-tb")
    else:
        label_t, value_t, title_t, sec_t, foot_t = (
            s.meta_lt, s.meta_v, s.title_t, s.hdr_t, s.foot_t)
    rows = table.getElementsByType(TableRow)
    for c in ots.logical_cells(rows[0]):  # 1. title block
        if c is not None:
            _paint(c, s.hdr, title_t)
    for i in (1, 2, 3, 4, 5):  # 2-4. identity/requester/financial pairs
        if i < len(rows):
            form_pair(ots.logical_cells(rows[i]), s, label_t, value_t,
                      s.grid)
    if len(rows) > 6:  # 5. workflow header + aligned steps
        for c in ots.logical_cells(rows[6]):
            if c is not None and ots.cell_text(c).strip():
                _paint(c, s.hdr, sec_t)
    if len(rows) > 7:
        workflow_step(ots.logical_cells(rows[7]), s, label_t, value_t,
                      False)  # Step 1 PM
    if len(rows) > 8:
        workflow_step(ots.logical_cells(rows[8]), s, label_t, value_t,
                      True)  # Step 2 HR (BAND zebra, aligned)
    if len(rows) > 9:  # archive note
        note = ots.logical_cells(rows[9])
        for j, c in enumerate(note):
            if c is not None and ots.cell_text(c).strip():
                _paint(c, s.total if j == 1 else s.meta_l,
                       value_t if j == 1 else label_t)
    if len(rows) > 11:  # record/archive footer provenance
        for c in ots.logical_cells(rows[11]):
            if c is not None:
                _paint(c, s.foot, foot_t)
    rows = table.getElementsByType(TableRow)
    last = min(11, len(rows) - 1)
    for i in range(last + 1, len(rows)):
        if not ots.row_text(rows[i]).strip():
            rows[i].setAttribute("visibility", "collapse")
    ensure_page(doc, table, last + 1, widths=ADVANCE_WIDTHS,
                orient="portrait")


# --- primitives for later reports (approval workflow + empty state) ---
def approval_block(table, idx: int, s: Styles, steps=("PM", "HR")) -> None:
    """Two-line sign/date block; other reports call this after their table."""
    rows = table.getElementsByType(TableRow)
    for k, step in enumerate(steps):
        cells = ots.logical_cells(rows[idx + k])
        if cells[0] is not None:
            ots.set_cell_text(cells[0], "%s sign/date:" % step)
            _paint(cells[0], s.meta_l, s.meta_lt)


def empty_state_row(table, idx: int, s: Styles,
                    text="No records for this period.") -> None:
    """Single centered placeholder row for empty reports."""
    cells = ots.logical_cells(table.getElementsByType(TableRow)[idx])
    if cells[0] is not None:
        ots.set_cell_text(cells[0], text)
        _paint(cells[0], s.grid, s.grid_c)


def apply_no_labor(doc, table, date_str: str, site: str = "") -> None:
    """Intentional empty-state document (no table, no grid).

    Brand -> bilingual title block (HEADER_BG) -> Date/Site ->
    recorded message -> provenance footer. Borderless cells only;
    portrait A4, print-range locked to content (1 page).
    """
    s = Styles(doc)
    s.clean()
    s = Styles(doc)
    lines = ["Atlas-Bot | Construction Project",
             "No Labor Today / \u0644\u0627 \u0639\u0645\u0627\u0644\u0629 \u0627\u0644\u064a\u0648\u0645",
             "", "Date: %s" % (date_str or "-"),
             "Site: %s" % (site or "-"), "",
             "No labor entries were recorded for this reporting period.",
             "", "Generated by Atlas-Bot | Automated Report"]
    rows = table.getElementsByType(TableRow)
    while len(rows) < len(lines):
        ots.insert_blank_row(table, len(rows) - 1)
        rows = table.getElementsByType(TableRow)
    for i, text in enumerate(lines):
        cells = ots.logical_cells(rows[i])
        if cells[0] is None:
            continue
        ots.set_cell_text(cells[0], text)
        for extra in cells[1:]:  # drop stale template text outside col A
            if extra is not None and ots.cell_text(extra).strip():
                ots.set_cell_text(extra, "")
        if i == 1:
            _paint(cells[0], s.hdr, s.hdr_t)
        elif i in (0, len(lines) - 1):
            _paint(cells[0], s.foot, s.foot_t)
        else:
            _paint(cells[0], s.meta_l, s.grid_c)
    for i in range(len(lines), len(rows)):
        if not ots.row_text(rows[i]).strip():
            rows[i].setAttribute("visibility", "collapse")
    ensure_page(doc, table, len(lines), widths=("17.7cm",), orient="portrait")
