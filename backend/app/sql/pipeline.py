"""Text-to-SQL pipeline: question -> retrieve schema -> LLM -> validate -> execute -> chart.

Every step is timed and the outcome is recorded, so the same object powers the
API, the agent tool and the evaluation harness.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from app.llm.base import LLMClient, LLMError, Message
from app.schema_rag.retriever import SchemaRetriever
from app.security.rbac import RolePolicy
from app.sql.executor import QueryExecutionError, QueryResult, execute_readonly
from app.sql.prompts import REPAIR_TEMPLATE, SQL_SYSTEM_PROMPT, SQL_USER_TEMPLATE
from app.sql.validator import SQLValidator
from app.viz.chart_spec import choose_chart

logger = logging.getLogger(__name__)

SQL_BLOCK = re.compile(r"```(?:sql|postgresql)?\s*(.+?)```", re.S | re.I)
CANNOT_ANSWER = re.compile(r"CANNOT_ANSWER:?\s*(.*)", re.I | re.S)

Executor = Callable[[str], QueryResult]


@dataclass
class AskResult:
    """Everything the UI, the audit log and the evaluator need about one question."""

    question: str
    status: str  # success | blocked | cannot_answer | error
    sql: str | None = None
    explanation: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    chart: dict[str, Any] | None = None
    tables_used: list[str] = field(default_factory=list)
    attempts: int = 0
    error: str | None = None
    latency_ms: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_sql(text: str) -> str | None:
    """Pull the SQL out of a model reply (fenced block preferred, bare SELECT accepted)."""
    match = SQL_BLOCK.search(text or "")
    if match:
        return match.group(1).strip()
    bare = re.search(r"\b(with|select)\b.+", text or "", re.I | re.S)
    return bare.group(0).strip() if bare else None


def _explanation(text: str) -> str:
    rest = SQL_BLOCK.sub("", text or "").strip()
    return rest.splitlines()[0][:300] if rest else ""


class TextToSQLPipeline:
    """Orchestrates retrieval, generation, validation, execution and repair."""

    def __init__(
        self,
        llm: LLMClient,
        retriever: SchemaRetriever,
        max_rows: int = 200,
        repair_attempts: int = 1,
        top_k: int = 4,
        executor: Executor | None = None,
    ) -> None:
        self.llm = llm
        self.retriever = retriever
        self.max_rows = max_rows
        self.repair_attempts = repair_attempts
        self.top_k = top_k
        self.executor: Executor = executor or (lambda q: execute_readonly(q, max_rows=max_rows))

    def ask(self, question: str, policy: RolePolicy) -> AskResult:
        start = time.perf_counter()
        result = AskResult(question=question, status="error")
        timings: dict[str, float] = {}

        t0 = time.perf_counter()
        retrieval = self.retriever.retrieve(question, k=self.top_k, allowed_tables=set(policy.tables))
        hidden = {t: set(c) for t, c in policy.hidden_columns.items()}
        schema_text = self.retriever.render_schema(retrieval.tables, hidden)
        timings["retrieval"] = (time.perf_counter() - t0) * 1000
        result.tables_used = retrieval.tables

        validator = SQLValidator(policy, max_rows=self.max_rows)
        messages: list[Message] = [
            {"role": "user", "content": SQL_USER_TEMPLATE.format(schema=schema_text, question=question)}
        ]
        system = SQL_SYSTEM_PROMPT.format(max_rows=self.max_rows)
        timings["llm"] = timings["validation"] = timings["execution"] = 0.0

        for attempt in range(1, self.repair_attempts + 2):
            result.attempts = attempt
            t0 = time.perf_counter()
            try:
                reply = self.llm.complete(system, messages, max_tokens=4000)
            except LLMError as exc:
                result.status, result.error = "error", str(exc)
                break
            timings["llm"] += (time.perf_counter() - t0) * 1000
            result.input_tokens += reply.input_tokens
            result.output_tokens += reply.output_tokens

            refusal = CANNOT_ANSWER.search(reply.text or "")
            sql = extract_sql(reply.text)
            if refusal and not sql:
                result.status, result.error = "cannot_answer", refusal.group(1).strip()[:300]
                break
            if not sql:
                result.status, result.error = "error", "The model did not return SQL"
                messages += [
                    reply.as_assistant_message(),
                    {"role": "user", "content": REPAIR_TEMPLATE.format(error=result.error)},
                ]
                continue

            t0 = time.perf_counter()
            check = validator.validate(sql)
            timings["validation"] += (time.perf_counter() - t0) * 1000
            result.sql = check.sql or sql
            result.explanation = _explanation(reply.text)
            if not check.ok:
                # Security violations are final: never "repair" a blocked query into a working one.
                result.status, result.error = "blocked", "; ".join(check.errors)
                break

            t0 = time.perf_counter()
            try:
                data = self.executor(check.sql)
            except QueryExecutionError as exc:
                timings["execution"] += (time.perf_counter() - t0) * 1000
                result.status, result.error = "error", str(exc)
                messages += [
                    reply.as_assistant_message(),
                    {"role": "user", "content": REPAIR_TEMPLATE.format(error=str(exc))},
                ]
                continue
            timings["execution"] += (time.perf_counter() - t0) * 1000
            result.status, result.error = "success", None
            result.columns, result.rows = data.columns, data.rows
            result.row_count, result.truncated = data.row_count, data.truncated
            result.chart = choose_chart(data.columns, data.column_types, data.rows, question).to_dict()
            break

        result.timings_ms = {k: round(v, 1) for k, v in timings.items()}
        result.latency_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info("ask status=%s attempts=%d latency_ms=%.0f", result.status, result.attempts, result.latency_ms)
        return result
