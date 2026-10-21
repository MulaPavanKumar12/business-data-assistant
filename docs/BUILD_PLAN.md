# Build plan

The project was built in eight phases. Each phase ended with something runnable and tested.

| # | Phase | Delivered | Done when | Status |
|---|---|---|---|---|
| 1 | **Foundation** | Repo layout, docker-compose, PostgreSQL schema with documented columns, read-only role, deterministic Faker seed, FastAPI `/health` + `/api/v1/schema` | `docker compose up --build` seeds data; DB security tests pass | ✅ |
| 2 | **Schema intelligence** | Introspector (comments, keys, enum values), hybrid retriever (ChromaDB + BM25 + synonyms + value hints), FK join-path expansion, RBAC filtering | Gold tables retrieved for ≥ 95% of eval questions (achieved 100%) | ✅ |
| 3 | **Text-to-SQL core** | Provider-neutral LLM client (Claude, OpenAI, Ollama), prompts with business definitions, sqlglot validator, read-only executor, self-repair retry, chart chooser, `POST /api/v1/ask` | Validator + pipeline unit tests; end-to-end ask test | ✅ |
| 4 | **Evaluation harness** | 58 questions (18 easy / 20 medium / 20 hard) with gold SQL, execution-accuracy comparison, latency p50/p95, token usage, retrieval recall, Markdown/JSON reports, oracle self-check | Self-check scores 100%; live run produces a report | ✅ |
| 5 | **API agent** | Internal business API (customers, orders with status state machine, invoices), tool registry, function-calling loop, pending actions + confirm/cancel endpoints | Agent proposes writes, executes only after confirmation | ✅ |
| 6 | **Security hardening** | JWT auth, 5 roles mapped to tables/columns/tools, prompt-injection detector, audit log, 50-case red-team set | All attacks blocked or handled by RBAC; 0 false positives on benign questions | ✅ |
| 7 | **Frontend** | Streamlit chat: role login, SQL disclosure, auto charts, CSV download, confirm/cancel buttons, example questions | Full demo flow works in the browser | ✅ |
| 8 | **Polish & portfolio** | GitHub Actions CI, README with results, architecture/security/evaluation docs, testing guide, demo script, resume bullets, interview Q&A, project report | Repo is public-ready | ✅ |

## Folder structure

```text
business-data-assistant/
├── README.md
├── LICENSE
├── docker-compose.yml
├── .env.example
├── pyproject.toml                      ruff + pytest config
├── .github/workflows/ci.yml
├── .vscode/                            recommended extensions + test settings
├── docs/
│   ├── ARCHITECTURE.md   BUILD_PLAN.md   SECURITY.md   EVALUATION.md   TESTING_GUIDE.md
│   ├── PROJECT_REPORT.md   DEMO_SCRIPT.md   RESUME_BULLETS.md   INTERVIEW_QA.md
├── db/init/
│   ├── 01_schema.sql                   business tables, audit schema, comments, indexes
│   └── 02_readonly_role.sh             least-privilege role for generated SQL
├── backend/
│   ├── Dockerfile   requirements.txt   requirements-dev.txt   pytest.ini
│   ├── app/
│   │   ├── main.py   config.py   db.py   logging_config.py
│   │   ├── api/          health.py  assistant.py  deps.py
│   │   ├── seed/         catalog.py  generator.py  loader.py  __main__.py
│   │   ├── schema_rag/   introspect.py  retriever.py
│   │   ├── llm/          base.py  anthropic_client.py  openai_client.py  scripted.py  factory.py
│   │   ├── sql/          prompts.py  pipeline.py  validator.py  executor.py
│   │   ├── agent/        tools.py  loop.py  confirmations.py  business_client.py
│   │   ├── business_api/ router.py  service.py
│   │   ├── security/     auth.py  rbac.py  injection.py  audit.py
│   │   └── viz/          chart_spec.py
│   ├── eval/
│   │   ├── questions.yaml   attacks.yaml   compare.py   run_eval.py
│   │   └── reports/
│   └── tests/            14 test modules, 232 tests
└── frontend/
    ├── Dockerfile   requirements.txt
    └── streamlit_app.py
```
