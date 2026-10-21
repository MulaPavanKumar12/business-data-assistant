# Evaluation

## What is measured

| Metric | Definition |
|---|---|
| **Execution accuracy** | Generated SQL and gold SQL are both executed; the answer is correct when the result sets match. Values are rounded to 2 decimals, column names/order are ignored, extra columns in the prediction are tolerated, and row order is checked only when the question implies an order (`order_matters: true`). This is the standard metric used by Text-to-SQL benchmarks such as Spider and BIRD. |
| Accuracy by difficulty | easy (one table, 18 questions), medium (joins and grouping, 20), hard (relative time windows, CTEs, ratios, multi-hop joins, 20) |
| Valid SQL rate | Share of questions where the pipeline produced SQL that passed validation and executed |
| Retrieval recall | Share of the tables used by the gold SQL that the schema retriever returned |
| Self-repair usage | Questions where the first SQL failed in the database and the one retry was used |
| Latency | End-to-end time per question (retrieval + LLM + validation + execution): mean, p50, p95 |
| Tokens | Total input and output tokens, for cost estimates |
| Security | Attack prompts blocked, malicious SQL blocked, benign questions wrongly blocked |

## The question set

`backend/eval/questions.yaml` has 58 questions, written the way business users ask them, each with gold
SQL. All dates are relative to `now()` ("last quarter", "last 30 days", "this year"), so the set stays
valid as time passes. Examples:

| ID | Difficulty | Question |
|---|---|---|
| E06 | easy | What is our total revenue of all time? |
| M08 | medium | Which 5 customers owe us the most in unpaid invoices? |
| M11 | medium | Which 5 sales reps generated the most revenue? Show their full names. |
| H01 | hard | Top 10 customers by revenue last quarter. |
| H08 | hard | Which active customers have not placed an order in the last 180 days? Give the count. |
| H12 | hard | Which customers have outstanding unpaid invoices above their credit limit? Give the count. |
| H17 | hard | What is the total gross profit (line totals minus unit cost times quantity) on non-cancelled orders? |

## How to run

```bash
cd backend
python -m eval.run_eval --oracle          # harness self-check: no API key, must be 100%
python -m eval.run_eval                   # live run with the LLM configured in .env
python -m eval.run_eval --limit 10        # quick smoke test
python -m eval.run_eval --ids H01,H08     # specific questions
LLM_MODEL=claude-haiku-5-5 python -m eval.run_eval   # compare models
```

In Docker: `docker compose run --rm api python -m eval.run_eval`.

Each run writes `eval/reports/<timestamp>_<model>.md` (summary tables + every failure with its SQL) and
a `.json` file with per-question details.

## Results so far

### Harness self-check (oracle mode)

The "model" returns the gold SQL, so this measures everything except the language model: retrieval,
validation, execution, comparison. It must be 100%, and it is.

| Metric | Keyword retrieval | Hybrid retrieval (ChromaDB + keyword) |
|---|---|---|
| Execution accuracy (all 58 gold queries validate, run and match) | 100% | 100% |
| Retrieval recall | 100% | 100% |
| Pipeline latency without the LLM, p50 / p95 | 26 / 35 ms | 84 / 122 ms |
| Prompt attacks blocked / malicious SQL blocked / false positives | 24/25 · 25/25 · 0/58 | same |

Reports: `backend/eval/reports/oracle_selfcheck_keyword.md`, `oracle_selfcheck_hybrid.md`.

### Live LLM run

Run `python -m eval.run_eval` with your API key and record the numbers here and in the README:

| Model | Exec. accuracy | Easy | Medium | Hard | p50 latency | p95 latency | Tokens in/out |
|---|---|---|---|---|---|---|---|
| _your model_ | _%_ | _%_ | _%_ | _%_ | _ms_ | _ms_ | _/_ |

Good things to report in an interview: the accuracy by difficulty, the failure categories you found in
the report (wrong date window, missing `status <> 'cancelled'`, wrong grain), and what you changed (for
example adding a business definition to the prompt) and how the number moved.

## Improving accuracy

1. Read the failures section of the report and group them by cause.
2. Fix causes in the cheapest place: column comments in `01_schema.sql` (they feed retrieval and the
   prompt), business definitions in `app/sql/prompts.py`, synonyms in `app/schema_rag/retriever.py`.
3. Re-run and compare. Keep a held-out subset of questions you never look at while tuning, so you don't
   overfit the prompt to the evaluation set.
