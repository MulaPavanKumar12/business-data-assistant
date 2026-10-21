"""Result-set comparison for execution accuracy."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from decimal import Decimal
from itertools import combinations
from typing import Any


def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float | Decimal):
        return round(float(v), 2)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()[:10]
    if isinstance(v, str):
        try:  # ISO timestamps returned as strings by the executor
            return (
                dt.datetime.fromisoformat(v).date().isoformat() if len(v) >= 10 and v[4] == "-" and v[7] == "-" else v
            )
        except ValueError:
            return v
    return str(v)


def _row_key(row: list[Any]) -> tuple:
    # Column order is ignored: sort the normalised values of each row.
    return tuple(sorted((_norm(v) for v in row), key=lambda x: (str(type(x)), str(x))))


def results_match(gold: list[list[Any]], pred: list[list[Any]], order_matters: bool = False) -> bool:
    """True if ``pred`` contains the same answer as ``gold``.

    * row order ignored unless ``order_matters``
    * column order and names ignored
    * extra columns in ``pred`` are tolerated (e.g. an added id column)
    """
    if len(gold) != len(pred):
        return False
    if not gold:
        return True
    n_gold, n_pred = len(gold[0]), len(pred[0]) if pred else 0
    if n_pred < n_gold:
        return False
    for cols in combinations(range(n_pred), n_gold):
        projected = [[r[i] for i in cols] for r in pred]
        if order_matters:
            if [_row_key(r) for r in gold] == [_row_key(r) for r in projected]:
                return True
        elif Counter(_row_key(r) for r in gold) == Counter(_row_key(r) for r in projected):
            return True
        if n_pred - n_gold > 3:  # bound the search for very wide results
            break
    return False
