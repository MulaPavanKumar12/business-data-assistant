"""Choose a chart automatically from the *shape* of a query result.

The backend returns a small, library-agnostic spec; the Streamlit UI renders
it with Plotly. Keeping the decision server-side makes it unit-testable and
lets any frontend reuse it.

Heuristics (first match wins):

* 1 row x 1 numeric column             -> ``metric`` (a big number)
* a date column + numeric column(s)     -> ``line`` (trend over time)
* 1 category + 1 numeric, <= 8 rows, share-like question -> ``pie``
* 1 category + numeric column(s), <= 30 rows -> ``bar`` (horizontal if labels are long)
* anything else                         -> ``table`` only
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

SHARE_WORDS = re.compile(r"\b(share|split|breakdown|distribution|proportion|percentage|mix)\b", re.I)
ID_COLUMN = re.compile(r"(^id$|_id$)")


@dataclass
class ChartSpec:
    type: str  # metric | line | bar | pie | table
    x: str | None = None
    y: list[str] = field(default_factory=list)
    title: str = ""
    orientation: str = "v"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def choose_chart(columns: list[str], column_types: list[str], rows: list[list[Any]], question: str = "") -> ChartSpec:
    """Return the most useful chart for this result."""
    title = question.strip().rstrip("?")[:90]
    if not rows or not columns:
        return ChartSpec("table", title=title)
    numeric = [c for c, t in zip(columns, column_types, strict=True) if t == "number" and not ID_COLUMN.search(c)]
    dates = [c for c, t in zip(columns, column_types, strict=True) if t == "date"]
    text = [c for c, t in zip(columns, column_types, strict=True) if t == "text"]

    if len(rows) == 1 and len(columns) == 1 and numeric:
        return ChartSpec("metric", y=numeric, title=title)
    if dates and numeric and len(rows) >= 2:
        return ChartSpec("line", x=dates[0], y=numeric[:3], title=title)
    if not numeric or len(rows) < 2:
        return ChartSpec("table", title=title)
    category = text[0] if text else next((c for c in columns if c not in numeric), None)
    if category is None:
        return ChartSpec("table", title=title)
    if len(rows) <= 8 and len(numeric) == 1 and SHARE_WORDS.search(question):
        return ChartSpec("pie", x=category, y=numeric, title=title)
    if len(rows) <= 30:
        idx = columns.index(category)
        long_labels = max(len(str(r[idx])) for r in rows) > 14
        return ChartSpec("bar", x=category, y=numeric[:2], title=title, orientation="h" if long_labels else "v")
    return ChartSpec("table", title=title)
