# Evaluation report: oracle-self-check

Run: 2026-10-21 18:01 · mode: **oracle (gold SQL as model output: harness self-check, NOT model accuracy)** · questions: 58

## Text-to-SQL

| Metric | Value |
|---|---|
| Execution accuracy | **100.0%** |
| Accuracy (easy, n=18) | 100.0% |
| Accuracy (medium, n=20) | 100.0% |
| Accuracy (hard, n=20) | 100.0% |
| Valid, executable SQL | 100.0% |
| Schema retrieval recall | 100.0% |
| Questions that used the self-repair retry | 0 |
| Latency mean / p50 / p95 | 25.6 / 25.8 / 34.7 ms |
| Tokens in / out (total) | 0 / 0 |

## Security

| Test | Result |
|---|---|
| Prompt-injection attacks blocked | 24/25 |
| Malicious SQL blocked by validator | 25/25 |
| Benign questions wrongly blocked | 0/58 |

## Failures

None.