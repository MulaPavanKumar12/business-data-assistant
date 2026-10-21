"""Run the evaluation: Text-to-SQL execution accuracy, latency, retrieval recall and security.

Usage (from the backend/ folder, database running)::

    python -m eval.run_eval                 # real LLM from .env (LLM_PROVIDER / API key)
    python -m eval.run_eval --limit 10      # quick smoke run
    python -m eval.run_eval --oracle        # harness self-check: the "LLM" returns the gold SQL

``--oracle`` exercises every component except the language model (retrieval,
validation, execution, comparison) and must score 100%. It proves the harness
and the gold SQL are correct before you spend tokens on a real run.

Outputs ``eval/reports/<timestamp>_<model>.md`` and ``.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from app.config import get_settings
from app.db import readonly_connection
from app.llm.base import LLMClient, LLMResponse
from app.llm.factory import get_llm_client
from app.llm.scripted import ScriptedLLM
from app.logging_config import configure_logging
from app.schema_rag.introspect import introspect_schema
from app.schema_rag.retriever import SchemaRetriever, build_retriever
from app.security.injection import detect_injection
from app.security.rbac import get_policy
from app.sql.executor import QueryExecutionError, execute_readonly
from app.sql.pipeline import TextToSQLPipeline
from app.sql.validator import SQLValidator
from eval.compare import results_match

logger = logging.getLogger("eval")
EVAL_DIR = Path(__file__).parent


@dataclass
class QuestionResult:
    id: str
    difficulty: str
    question: str
    correct: bool
    status: str
    latency_ms: float
    retrieval_recall: float
    attempts: int
    input_tokens: int
    output_tokens: int
    generated_sql: str | None
    error: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)


def tables_in(sql: str) -> set[str]:
    tree = sqlglot.parse_one(sql, read="postgres")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    return {t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes}


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def oracle_llm(questions: list[dict[str, Any]]) -> ScriptedLLM:
    gold = {q["question"]: q["sql"] for q in questions}

    def respond(_system: str, messages: list[dict[str, Any]], _tools: Any) -> LLMResponse:
        content = messages[0]["content"]
        question = content.split("<question>")[1].split("</question>")[0].strip()
        return LLMResponse(text=f"```sql\n{gold[question]}\n```\nGold query.")

    return ScriptedLLM(responder=respond)


def run_accuracy(
    questions: list[dict[str, Any]], llm: LLMClient, retriever: SchemaRetriever, role: str, max_rows: int
) -> list[QuestionResult]:
    policy = get_policy(role)
    pipeline = TextToSQLPipeline(llm, retriever, max_rows=max_rows, repair_attempts=1)
    gold_validator = SQLValidator(policy, max_rows=max_rows)
    results: list[QuestionResult] = []
    for i, q in enumerate(questions, 1):
        gold_sql = gold_validator.validate_or_raise(q["sql"]).sql
        gold = execute_readonly(gold_sql, max_rows=max_rows)
        pred = pipeline.ask(q["question"], policy)
        correct = pred.status == "success" and results_match(gold.rows, pred.rows, q.get("order_matters", False))
        needed = tables_in(q["sql"])
        recall = len(needed & set(pred.tables_used)) / len(needed) if needed else 1.0
        results.append(
            QuestionResult(
                q["id"],
                q["difficulty"],
                q["question"],
                correct,
                pred.status,
                pred.latency_ms,
                round(recall, 3),
                pred.attempts,
                pred.input_tokens,
                pred.output_tokens,
                pred.sql,
                pred.error,
                pred.timings_ms,
            )
        )
        logger.info(
            "[%d/%d] %s %s %.0fms %s",
            i,
            len(questions),
            q["id"],
            "PASS" if correct else "FAIL",
            pred.latency_ms,
            pred.error or "",
        )
    return results


def run_security(attacks: dict[str, list[str]], benign: list[str], max_rows: int) -> dict[str, Any]:
    prompt_rows = [{"prompt": p, **detect_injection(p).__dict__} for p in attacks["prompts"]]
    validator = SQLValidator(get_policy("analyst"), max_rows=max_rows)
    sql_rows = [
        {"sql": s, "blocked": not (r := validator.validate(s)).ok, "errors": r.errors} for s in attacks["malicious_sql"]
    ]
    false_positives = [b for b in benign if detect_injection(b).blocked]
    return {
        "prompt_attacks": len(prompt_rows),
        "prompt_attacks_blocked": sum(r["blocked"] for r in prompt_rows),
        "sql_attacks": len(sql_rows),
        "sql_attacks_blocked": sum(r["blocked"] for r in sql_rows),
        "benign_questions": len(benign),
        "benign_false_positives": len(false_positives),
        "false_positive_examples": false_positives,
        "missed_prompts": [r["prompt"] for r in prompt_rows if not r["blocked"]],
        "missed_sql": [r["sql"] for r in sql_rows if not r["blocked"]],
    }


def summarize(results: list[QuestionResult]) -> dict[str, Any]:
    def acc(rs: list[QuestionResult]) -> float:
        return round(100 * sum(r.correct for r in rs) / len(rs), 1) if rs else 0.0

    lat = [r.latency_ms for r in results]
    by_diff = {
        d: {"n": len(rs), "accuracy": acc(rs)}
        for d in ("easy", "medium", "hard")
        if (rs := [r for r in results if r.difficulty == d])
    }
    return {
        "questions": len(results),
        "execution_accuracy": acc(results),
        "by_difficulty": by_diff,
        "valid_sql_rate": round(100 * sum(r.status == "success" for r in results) / len(results), 1),
        "retrieval_recall": round(100 * statistics.mean(r.retrieval_recall for r in results), 1),
        "repair_used": sum(r.attempts > 1 for r in results),
        "latency_ms": {
            "mean": round(statistics.mean(lat), 1),
            "p50": round(percentile(lat, 50), 1),
            "p95": round(percentile(lat, 95), 1),
            "max": round(max(lat), 1),
        },
        "tokens": {
            "input_total": sum(r.input_tokens for r in results),
            "output_total": sum(r.output_tokens for r in results),
        },
    }


def write_report(
    summary: dict[str, Any],
    security: dict[str, Any],
    results: list[QuestionResult],
    model: str,
    mode: str,
    out_dir: Path,
) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"{stamp}_{model.replace('/', '-')}"
    base.with_suffix(".json").write_text(
        json.dumps(
            {
                "model": model,
                "mode": mode,
                "summary": summary,
                "security": security,
                "results": [r.__dict__ for r in results],
            },
            indent=2,
            default=str,
        )
    )
    s, sec = summary, security
    lines = [
        f"# Evaluation report: {model}",
        "",
        f"Run: {dt.datetime.now():%Y-%m-%d %H:%M} · mode: **{mode}** · questions: {s['questions']}",
        "",
        "## Text-to-SQL",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Execution accuracy | **{s['execution_accuracy']}%** |",
        *[f"| Accuracy ({d}, n={v['n']}) | {v['accuracy']}% |" for d, v in s["by_difficulty"].items()],
        f"| Valid, executable SQL | {s['valid_sql_rate']}% |",
        f"| Schema retrieval recall | {s['retrieval_recall']}% |",
        f"| Questions that used the self-repair retry | {s['repair_used']} |",
        f"| Latency mean / p50 / p95 | {s['latency_ms']['mean']} / {s['latency_ms']['p50']} / {s['latency_ms']['p95']} ms |",
        f"| Tokens in / out (total) | {s['tokens']['input_total']:,} / {s['tokens']['output_total']:,} |",
        "",
        "## Security",
        "",
        "| Test | Result |",
        "|---|---|",
        f"| Prompt-injection attacks blocked | {sec['prompt_attacks_blocked']}/{sec['prompt_attacks']} |",
        f"| Malicious SQL blocked by validator | {sec['sql_attacks_blocked']}/{sec['sql_attacks']} |",
        f"| Benign questions wrongly blocked | {sec['benign_false_positives']}/{sec['benign_questions']} |",
        "",
        "## Failures",
        "",
    ]
    failures = [r for r in results if not r.correct]
    if not failures:
        lines.append("None.")
    for r in failures:
        lines += [
            f"### {r.id} ({r.difficulty}): {r.question}",
            f"Status: `{r.status}` {r.error or ''}",
            "",
            "```sql",
            r.generated_sql or "-- no SQL",
            "```",
            "",
        ]
    path = base.with_suffix(".md")
    path.write_text("\n".join(lines))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--oracle", action="store_true", help="use gold SQL as the model output (harness check)")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions")
    parser.add_argument("--ids", default="", help="comma-separated question ids")
    parser.add_argument("--out", default=str(EVAL_DIR / "reports"))
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    spec = yaml.safe_load((EVAL_DIR / "questions.yaml").read_text())
    attacks = yaml.safe_load((EVAL_DIR / "attacks.yaml").read_text())
    questions = spec["questions"]
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if args.limit:
        questions = questions[: args.limit]

    with readonly_connection(settings) as conn:
        tables = introspect_schema(conn)
    retriever = build_retriever(tables, settings.rag_backend, settings.chroma_path or None)
    llm: LLMClient = oracle_llm(spec["questions"]) if args.oracle else get_llm_client(settings)
    mode = "oracle (gold SQL as model output: harness self-check, NOT model accuracy)" if args.oracle else "live LLM"

    start = time.perf_counter()
    try:
        results = run_accuracy(questions, llm, retriever, spec.get("role", "admin"), settings.max_result_rows)
    except QueryExecutionError as exc:
        logger.error("gold SQL failed to execute: %s", exc)
        return 1
    summary = summarize(results)
    security = run_security(attacks, [q["question"] for q in spec["questions"]], settings.max_result_rows)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    label = "oracle-self-check" if args.oracle else llm.model
    report = write_report(summary, security, results, label, mode, out_dir)

    print(f"\n=== Evaluation ({mode}) in {time.perf_counter() - start:.1f}s ===")
    print(f"Execution accuracy: {summary['execution_accuracy']}%  {summary['by_difficulty']}")
    print(f"Retrieval recall:   {summary['retrieval_recall']}%")
    print(f"Latency p50/p95:    {summary['latency_ms']['p50']} / {summary['latency_ms']['p95']} ms")
    print(
        f"Injection blocked:  {security['prompt_attacks_blocked']}/{security['prompt_attacks']}   "
        f"Malicious SQL blocked: {security['sql_attacks_blocked']}/{security['sql_attacks']}   "
        f"False positives: {security['benign_false_positives']}/{security['benign_questions']}"
    )
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
