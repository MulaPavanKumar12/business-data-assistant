"""Evaluation harness: result comparison rules and an oracle run against the real DB."""

from __future__ import annotations

import pytest

from eval.compare import results_match


def test_row_and_column_order_ignored_unless_required() -> None:
    gold = [["SMB", 10.0], ["Enterprise", 20.0]]
    assert results_match(gold, [[20.0, "Enterprise"], [10.0, "SMB"]])
    assert not results_match(gold, [["Enterprise", 20.0], ["SMB", 10.0]], order_matters=True)


def test_numeric_rounding_and_extra_columns() -> None:
    assert results_match([["Acme", 10.004]], [[7, "Acme", 10.0]])
    assert not results_match([["Acme", 10.0]], [["Acme", 11.0]])
    assert not results_match([["Acme", 10.0]], [])


def test_timestamps_compare_by_date() -> None:
    assert results_match([["2026-01-01T00:00:00+00:00", 5]], [["2026-01-01", 5]])


@pytest.mark.integration
def test_oracle_run_scores_100_percent(readonly_conn, tmp_path) -> None:  # noqa: ANN001
    """Gold SQL must execute and the harness must recognise it as correct."""
    from eval.run_eval import main

    assert main(["--oracle", "--limit", "12", "--out", str(tmp_path)]) == 0
    report = next(tmp_path.glob("*.md")).read_text()
    assert "Execution accuracy | **100.0%**" in report
