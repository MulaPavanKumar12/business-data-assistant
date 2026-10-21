# Resume bullets

Replace the bracketed values with the numbers from your live evaluation report
(`backend/eval/reports/<timestamp>_<model>.md`). Everything else is already measured.

**Business Data Assistant: natural-language interface to business data** (Python, FastAPI, PostgreSQL, Claude, ChromaDB, sqlglot, Streamlit, Docker)

1. Built an LLM-powered Text-to-SQL assistant that answers plain-English business questions with validated PostgreSQL, tables and auto-generated charts, reaching **[X]% execution accuracy** on a custom 58-question benchmark (18 easy / 20 medium / 20 hard) at **[p50] s median latency**.
2. Designed hybrid schema RAG (ChromaDB embeddings + BM25 with business synonyms + foreign-key join-path expansion) that retrieves the correct tables for **100% of benchmark questions** while sending the model only the relevant slice of the schema.
3. Implemented a custom function-calling agent over internal REST APIs (customer lookup, order status updates, invoice creation) with **human-in-the-loop confirmation for every data-changing action**, enforced in code with per-user, single-use, expiring pending actions.
4. Engineered 7 layers of defence (JWT auth, role-based access to tables/columns/tools across 5 roles, prompt-injection detection, sqlglot AST validation, read-only database role with timeouts, audit log), blocking **25/25 malicious SQL statements and 24/25 injection prompts with 0 false positives** on 58 legitimate questions.
5. Shipped as a one-command Docker Compose stack with **232 automated tests** (unit, integration, end-to-end, red-team) and a GitHub Actions pipeline that lints, tests and runs the evaluation harness on every push.

**Shorter variant (3 bullets):**

- Built a Text-to-SQL and API agent assistant (FastAPI, PostgreSQL, Claude function calling) with [X]% execution accuracy on a 58-question benchmark and 100% schema-retrieval recall via hybrid RAG.
- Secured it with 7 guardrail layers (RBAC, sqlglot validation, read-only DB role, injection detection, audit, human confirmation for writes); blocked 49/50 red-team attacks with 0 false positives.
- Delivered a Dockerized stack with a Streamlit chat UI, 232 tests and CI that runs the evaluation on every push.
