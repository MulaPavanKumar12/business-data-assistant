"""Text-to-SQL pipeline with a scripted LLM and a fake executor (no DB, no API key)."""

from __future__ import annotations

import pytest

from app.llm.base import LLMError, LLMResponse
from app.llm.scripted import ScriptedLLM
from app.schema_rag.retriever import SchemaRetriever
from app.security.rbac import get_policy
from app.sql.executor import QueryExecutionError, QueryResult
from app.sql.pipeline import TextToSQLPipeline, extract_sql
from tests.test_retriever_and_charts import TOY


class FakeExecutor:
    def __init__(self, fail_first: bool = False) -> None:
        self.queries: list[str] = []
        self.fail_first = fail_first

    def __call__(self, sql: str) -> QueryResult:
        self.queries.append(sql)
        if self.fail_first and len(self.queries) == 1:
            raise QueryExecutionError('column "revenue" does not exist')
        return QueryResult(
            ["segment", "revenue"], [["SMB", 10.0], ["Enterprise", 90.0]], 2, False, 1.0, ["text", "number"]
        )


def make(llm: ScriptedLLM, executor: FakeExecutor | None = None) -> tuple[TextToSQLPipeline, FakeExecutor]:
    ex = executor or FakeExecutor()
    return TextToSQLPipeline(llm, SchemaRetriever(TOY), max_rows=50, executor=ex), ex


def test_extract_sql_variants() -> None:
    assert extract_sql("```sql\nSELECT 1\n```\nexplains") == "SELECT 1"
    assert extract_sql("Here: SELECT 2") == "SELECT 2"
    assert extract_sql("no query here") is None


def test_success_returns_rows_chart_and_limited_sql() -> None:
    llm = ScriptedLLM(
        [
            "```sql\nSELECT segment, SUM(total_amount) AS revenue FROM orders o JOIN customers c "
            "USING (customer_id) GROUP BY segment\n```\nRevenue by segment."
        ]
    )
    pipe, ex = make(llm)
    r = pipe.ask("Revenue by segment", get_policy("analyst"))
    assert r.status == "success" and r.row_count == 2
    assert r.chart and r.chart["type"] == "bar"
    assert "LIMIT 50" in ex.queries[0]
    assert r.explanation == "Revenue by segment."
    assert "<schema>" in llm.calls[0]["messages"][0]["content"]


def test_execution_error_triggers_one_repair() -> None:
    llm = ScriptedLLM(["```sql\nSELECT revenue FROM orders\n```", "```sql\nSELECT total_amount FROM orders\n```"])
    pipe, ex = make(llm, FakeExecutor(fail_first=True))
    r = pipe.ask("revenue", get_policy("analyst"))
    assert r.status == "success" and r.attempts == 2
    assert 'column "revenue" does not exist' in llm.calls[1]["messages"][-1]["content"]


def test_blocked_sql_is_never_executed_or_repaired() -> None:
    llm = ScriptedLLM(["```sql\nDELETE FROM orders\n```", "```sql\nSELECT 1\n```"])
    pipe, ex = make(llm)
    r = pipe.ask("delete everything", get_policy("analyst"))
    assert r.status == "blocked" and ex.queries == [] and len(llm.calls) == 1


def test_restricted_column_is_blocked_for_role() -> None:
    pipe, ex = make(ScriptedLLM(["```sql\nSELECT AVG(salary) FROM employees\n```"]))
    assert pipe.ask("average salary", get_policy("analyst")).status == "blocked"
    assert ex.queries == []


def test_cannot_answer_is_reported() -> None:
    pipe, _ = make(ScriptedLLM(["CANNOT_ANSWER: there is no weather data"]))
    r = pipe.ask("What's the weather?", get_policy("analyst"))
    assert r.status == "cannot_answer" and "weather" in (r.error or "")


def test_llm_failure_is_an_error_not_a_crash() -> None:
    class Down:
        model = "down"

        def complete(self, *a: object, **k: object) -> LLMResponse:
            raise LLMError("Could not reach the LLM API")

    pipe = TextToSQLPipeline(Down(), SchemaRetriever(TOY), executor=FakeExecutor())
    r = pipe.ask("revenue", get_policy("analyst"))
    assert r.status == "error" and "reach" in (r.error or "")


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_prompt_never_contains_hidden_columns(role: str) -> None:
    llm = ScriptedLLM(["CANNOT_ANSWER: n/a"])
    pipe, _ = make(llm)
    pipe.ask("employee salary and staff list", get_policy(role))
    assert "salary" not in llm.calls[0]["messages"][0]["content"].split("<question>")[0]
