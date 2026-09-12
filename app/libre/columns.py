"""Template column contract (030): the SINGLE interpretation of daily
report table headers shared by the filler and the validator.

Fields resolve by header text so owners may insert columns anywhere.
First header containing any alias wins; EN + common AR word stems.

Contracted daily layout (024):
    #/م | Contractor/المقاول | Type/البند | Zone/مكان العمل |
    Workers/عدد العمال | Craftsmen/الحرفيين | Helpers/المساعدين |
    Details/التفصيلي
"""

from __future__ import annotations

from app.utils.exceptions import LaborReportError


class TemplateColumnError(LaborReportError):
    """A template header/column violates the contract (validator + filler)."""

    def __init__(self, field: str = "", reason: str = "",
                 matches: list | None = None, original_exception=None):
        self.field = field
        self.reason = reason
        self.matches = matches or []
        text = f"{field}: {reason}" if field or reason else "template error"
        super().__init__(text, original_exception=original_exception)


class LibreFillError(TemplateColumnError):
    """Fill-time column error (kept name for compatibility)."""

    @property
    def user_message(self) -> str:
        return "An error occurred while generating the report file. Please try again."


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "contractor": ("contractor", "المقاول"),
    "type": ("type", "البند"),
    "zone": ("zone", "مكان"),
    "workers": ("worker", "العمال"),
    "craftsmen": ("craftsm", "حرفي"),  # stem: matches singular + plural
    "helpers": ("helper", "مساعد"),
    "details": ("detail", "التفصيلي"),
}
# ponytail: if an owner label matches nothing, they rename the header or we
# add an alias here (upgrade: config table.header_aliases if that recurs).
REQUIRED = ("contractor", "workers")


def locate_columns(header_texts: list[str]) -> dict[str, int | None]:
    """Resolve fields to logical column indexes by header text.

    Missing required labels or a header matching two fields raises
    TemplateColumnError carrying field + reason (+ matches when known).
    Optional labels (type, zone, split columns, details) simply miss.
    """
    fold = [t.casefold() for t in header_texts]
    cols: dict[str, int | None] = {}
    hits_by_field: dict[str, list[int]] = {}
    for field, aliases in FIELD_ALIASES.items():
        hits = [i for i, t in enumerate(fold)
                if any(a in t for a in aliases)]
        hits_by_field[field] = hits
        cols[field] = hits[0] if hits else None
    missing = [f for f in REQUIRED if cols.get(f) is None]
    if missing:
        raise LibreFillError(
            ",".join(missing), "missing required header", [])
    taken = [i for i in cols.values() if i is not None]
    if len(taken) != len(set(taken)):
        dupes = sorted({i for i in taken if taken.count(i) > 1})
        fields = sorted(f for f, h in hits_by_field.items()
                        if h and h[0] in dupes)
        raise LibreFillError(
            ",".join(fields) or "headers",
            "one label matches two fields",
            [f"col{i}" for i in dupes])
    return cols
