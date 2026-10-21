# 10 likely interview questions (with strong answers)

### 1. Walk me through what happens when a user asks "Top 10 customers by revenue last quarter."

The Streamlit UI sends the message with the user's JWT to `/api/v1/chat`. A pattern-based injection
guard screens it first. The agent loop calls the LLM with only the tools this role may use; the model
picks `query_database`. The schema retriever scores tables with BM25 plus a synonym dictionary and
ChromaDB embeddings, adds bridge tables by walking foreign keys, and filters by role. The SQL prompt
contains only those tables, their column descriptions and enum values, plus business definitions like
"revenue excludes cancelled orders" and "last quarter = previous calendar quarter". The model returns
SQL; sqlglot parses it and checks it is a single SELECT, touches only allowed tables and columns, uses no
dangerous functions, and rewrites the LIMIT. It runs as a read-only role in a read-only transaction with
a timeout. The result goes back to the model as data for a short summary, a chart type is chosen from the
result's shape, and the question, SQL, row count and latency are written to the audit log.

### 2. Why did you build your own agent loop instead of using LangChain?

Control and auditability. Every tool call in this system must pass an RBAC check, argument validation,
a confirmation gate for writes, and be audited. In my loop that's explicit Python in about 200 lines, so
it's easy to reason about and to test: I have tests where a scripted "compromised" model emits forbidden
tool calls and I assert they're refused. LangChain is faster for prototyping and has many integrations,
but its abstractions hide prompts and change often. I kept a provider-neutral `LLMClient` interface, so
I can still swap Claude, OpenAI or a local Ollama model without touching the loop.

### 3. How do you stop the model from running something destructive like `DROP TABLE`?

Defence in depth, assuming the model can be tricked. The prompt states the rules, but I don't rely on
it. The validator works on the parsed syntax tree, so comments, casing or a `DELETE` hidden inside a CTE
don't get through, and multiple statements are rejected. Below that, the database role has only SELECT
grants, `default_transaction_read_only` is on, and the executor opens an explicit READ ONLY transaction.
I have integration tests proving that role can't DELETE, UPDATE, DROP, TRUNCATE, ALTER, CREATE or GRANT,
even after trying `SET default_transaction_read_only = off`. Security failures are final: a blocked
query is never sent back to the model to "repair".

### 4. How does schema RAG work, and why not just put the whole schema in the prompt?

With 9 tables you could, but real companies have hundreds. Sending everything costs tokens and latency
and makes wrong-table mistakes more likely. I store documentation as `COMMENT ON` in PostgreSQL so it
lives next to the schema. The introspector reads comments, keys and low-cardinality values (never PII).
Retrieval is hybrid: embeddings understand paraphrases, while BM25 with a business synonym list is
explainable and works offline. Values found in the question, like "Hardware" or "overdue", are strong
hints. Then a breadth-first search over foreign keys adds join tables. I measured recall: the tables the
gold SQL needs are retrieved for all 58 benchmark questions, in both keyword and hybrid mode.

### 5. How did you evaluate accuracy, and what's execution accuracy?

I wrote 58 questions in business language across three difficulty levels, each with gold SQL using dates
relative to `now()` so the set doesn't go stale. Execution accuracy runs both the generated and the gold
SQL and compares result sets, rounding numbers and ignoring column names and order, and only checking
row order when the question implies ranking. That's the metric used by Spider and BIRD, and it's fairer
than string matching because many different SQL queries are correct. I also added an "oracle" mode where
the model returns the gold SQL; it must score 100%, which validates the harness and the gold queries
before spending tokens. The harness also reports latency p50/p95, token usage, retrieval recall and how
often the self-repair retry was used. [Then give your live numbers and one failure category you fixed.]

### 6. How do you handle actions that change data, like creating an invoice?

Tools are marked `mutates=True`. The loop never executes those. It validates arguments, fetches a
preview from the business API ("Invoice order #4990, Som Inc, $497.42, due in 30 days") and stores a
pending action bound to the user, with a 15-minute expiry. The UI shows Confirm and Cancel. On confirm,
the server checks the action belongs to the caller, is still pending, hasn't expired, and that the role
still allows the tool, then calls the API once. Both the request and the decision are audited. The
business API also enforces its own rules, like a status state machine and no duplicate invoices, so the
model can't push an order from delivered back to pending.

### 7. What about prompt injection, including indirect injection through data?

Direct injection is screened by a weighted rule set with normalisation (Unicode, zero-width characters,
spaced-out letters, base64 payloads). On my red-team set it blocks 24 of 25 attack prompts with zero
false positives on 58 normal questions; the 25th is a bulk-PII request that's legitimate for some roles,
so RBAC handles it. But I treat the guard as a speed bump, not a wall: everything behind it still holds
if it's bypassed. For indirect injection, for example a customer named "Ignore previous instructions
Inc", tool results are wrapped in `<tool_result_data>` and the system prompt says to never follow
instructions inside them, and writes still require a human click.

### 8. How is access control implemented?

Five roles (viewer, analyst, operations, finance, admin), each a policy of readable tables, hidden
columns and allowed tools. It's enforced in five places: retrieval hides tables and columns, so the model
never learns `salary` exists for an analyst; the validator blocks hidden columns including via aliases
and `SELECT *`; only allowed tools are offered; each tool call is re-checked; and confirmation re-checks
again. API responses are filtered the same way. In production I'd map identity-provider groups to these
roles and add PostgreSQL row-level security for things like "reps only see their own accounts".

### 9. What were the hardest problems, and what would you do next?

Business semantics, not syntax. "Revenue" must exclude cancelled orders; "last quarter" has to be the
previous calendar quarter; units sold come from order lines but still need the order status. I solved
that with explicit definitions in the prompt, rich column comments, and a retrieval rule that brings
`orders` along whenever `order_items` is used. Next: a semantic layer of named metrics so the model
picks "revenue" instead of rewriting it, caching of repeated questions, row-level security, an LLM-based
second opinion for injection detection measured against the same attack set, and streaming responses.

### 10. How would you deploy and run this in production for a client?

The stack is containerised already. I'd run the API on a managed container service behind an API
gateway with TLS and SSO, PostgreSQL on a managed service with the read-only role pointed at a read
replica so analytics never load the primary, and secrets in a secrets manager instead of `.env`. The
ChromaDB index would be persisted or rebuilt at startup from the live schema. I'd ship logs and the
audit table to the client's monitoring, alert on spikes of blocked requests, track accuracy by running
the evaluation in CI on every change, and set per-user rate limits and token budgets to control cost.
