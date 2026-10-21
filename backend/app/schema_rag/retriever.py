"""Schema RAG: pick the few tables a question needs, then add the tables that join them.

Retrieval is *hybrid*:

1. **Semantic search** over table and column descriptions in ChromaDB
   (sentence embeddings), which understands "clients" ~ "customers".
2. **Keyword search** with a small business-synonym dictionary. It needs no
   model download, makes results explainable, and is the fallback if
   embeddings are unavailable.

Scores are normalised and blended. Then a breadth-first search over the
foreign-key graph adds any *bridge* tables needed to join the chosen ones
(e.g. ``customers`` + ``products`` also needs ``orders`` and ``order_items``).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.schema_rag.introspect import TableDoc

logger = logging.getLogger(__name__)

# Business vocabulary -> words that appear in our schema docs.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "revenue": ("orders", "total_amount", "sales"),
    "sales": ("orders", "order_items", "revenue"),
    "sold": ("order_items", "quantity", "products"),
    "units": ("order_items", "quantity"),
    "bestselling": ("order_items", "products", "quantity"),
    "client": ("customers",),
    "clients": ("customers",),
    "account": ("customers",),
    "accounts": ("customers",),
    "company": ("customers",),
    "companies": ("customers",),
    "buyer": ("customers",),
    "buyers": ("customers",),
    "staff": ("employees",),
    "employee": ("employees",),
    "people": ("employees",),
    "rep": ("employees", "sales_rep_id", "representatives"),
    "reps": ("employees", "sales_rep_id"),
    "representative": ("employees",),
    "representatives": ("employees",),
    "seller": ("employees",),
    "salesperson": ("employees", "sales_rep_id"),
    "headcount": ("employees",),
    "salary": ("employees", "salary"),
    "salaries": ("employees", "salary"),
    "payroll": ("employees", "salary"),
    "manager": ("employees", "manager_id"),
    "hired": ("employees", "hire_date"),
    "team": ("departments",),
    "teams": ("departments",),
    "department": ("departments",),
    "bill": ("invoices",),
    "bills": ("invoices",),
    "billed": ("invoices",),
    "receivable": ("invoices", "unpaid"),
    "receivables": ("invoices", "unpaid"),
    "overdue": ("invoices", "due_date", "status"),
    "unpaid": ("invoices", "status"),
    "outstanding": ("invoices", "unpaid"),
    "due": ("invoices", "due_date"),
    "collected": ("payments", "amount"),
    "cash": ("payments",),
    "paid": ("payments", "invoices"),
    "method": ("payments", "method"),
    "card": ("payments", "method"),
    "item": ("products", "order_items"),
    "items": ("products", "order_items"),
    "sku": ("products",),
    "inventory": ("products", "stock_quantity"),
    "stock": ("products", "stock_quantity"),
    "margin": ("products", "unit_cost", "unit_price"),
    "profit": ("products", "unit_cost", "order_items"),
    "category": ("product_categories",),
    "categories": ("product_categories",),
    "basket": ("order_items", "orders"),
    "aov": ("orders", "total_amount"),
    "shipped": ("orders", "status"),
    "cancelled": ("orders", "status"),
    "delivered": ("orders", "status"),
    "churned": ("customers", "status"),
    "segment": ("customers", "segment"),
    "industry": ("customers", "industry"),
    "region": ("customers", "region"),
    "country": ("customers", "country"),
    "city": ("customers", "city"),
    "regions": ("customers", "region"),
    "countries": ("customers", "country"),
    "cities": ("customers", "city"),
    "segments": ("customers", "segment"),
    "industries": ("customers", "industry"),
}

# Tables whose correct use depends on another table's business rule
# (units sold must exclude cancelled orders, so order_items needs orders).
COMPANION_TABLES: dict[str, tuple[str, ...]] = {"order_items": ("orders",)}

STOPWORDS = frozenset(
    "a an the of in on for to by and or with what which who how many much is are was were show list give me "
    "top last this per each all our we my from at as be do does did their there".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, splitting snake_case and dropping stopwords."""
    words = re.findall(r"[a-z0-9]+", text.lower().replace("_", " "))
    return [w for w in words if w not in STOPWORDS]


def expand_query(tokens: Iterable[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        out.append(t)
        for syn in SYNONYMS.get(t, ()):
            out.extend(tokenize(syn) + [syn])
        if t.endswith("s") and len(t) > 3:  # crude singular form
            out.append(t[:-1])
    return out


class SemanticIndex(Protocol):
    """Anything that can score tables for a question (ChromaDB in production)."""

    def table_scores(self, question: str, n_results: int = 20) -> dict[str, float]: ...


class KeywordIndex:
    """BM25 over per-table documents (table + column descriptions + names)."""

    def __init__(self, tables: list[TableDoc], k1: float = 1.4, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs: dict[str, Counter[str]] = {}
        for t in tables:
            text = " ".join(f"{t.name} {t.name} {t.name} {txt}" for _, txt in t.to_documents())
            self.docs[t.name] = Counter(tokenize(text))
        self.avg_len = sum(sum(c.values()) for c in self.docs.values()) / max(len(self.docs), 1)
        df: Counter[str] = Counter()
        for counts in self.docs.values():
            df.update(counts.keys())
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def table_scores(self, question: str, n_results: int = 20) -> dict[str, float]:
        terms = expand_query(tokenize(question))
        scores: dict[str, float] = {}
        for table, counts in self.docs.items():
            length = sum(counts.values())
            s = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if tf:
                    s += (
                        self.idf[term]
                        * tf
                        * (self.k1 + 1)
                        / (tf + self.k1 * (1 - self.b + self.b * length / self.avg_len))
                    )
            if s > 0:
                scores[table] = s
        return scores


class ChromaSemanticIndex:
    """ChromaDB collection holding one embedding per table and per column description."""

    COLLECTION = "schema_docs"

    def __init__(self, tables: list[TableDoc], persist_path: str | None = None) -> None:
        import chromadb  # imported lazily: optional dependency at import time
        from chromadb.config import Settings as ChromaSettings

        chroma_settings = ChromaSettings(anonymized_telemetry=False)
        client = (
            chromadb.PersistentClient(path=persist_path, settings=chroma_settings)
            if persist_path
            else chromadb.EphemeralClient(settings=chroma_settings)
        )
        self.collection = client.get_or_create_collection(self.COLLECTION, metadata={"hnsw:space": "cosine"})
        ids, texts, metas = [], [], []
        for t in tables:
            for doc_id, text in t.to_documents():
                ids.append(doc_id)
                texts.append(text)
                metas.append({"table": t.name})
        self.collection.upsert(ids=ids, documents=texts, metadatas=metas)
        logger.info("chroma index ready with %d schema documents", len(ids))

    def table_scores(self, question: str, n_results: int = 20) -> dict[str, float]:
        res = self.collection.query(query_texts=[question], n_results=n_results)
        scores: dict[str, float] = defaultdict(float)
        for meta, dist in zip(res["metadatas"][0], res["distances"][0], strict=True):
            # Sum of similarities: a table with several matching columns ranks higher.
            scores[meta["table"]] += max(0.0, 1.0 - float(dist))
        return dict(scores)


@dataclass
class RetrievalResult:
    """Tables selected for a question, with the reasoning kept for debugging/audit."""

    tables: list[str]
    matched: list[str]
    bridges: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


class SchemaRetriever:
    """Hybrid retriever + foreign-key join expansion."""

    def __init__(
        self, tables: list[TableDoc], semantic: SemanticIndex | None = None, semantic_weight: float = 0.5
    ) -> None:
        self.tables = {t.name: t for t in tables}
        self.keyword = KeywordIndex(tables)
        self.semantic = semantic
        self.semantic_weight = semantic_weight if semantic else 0.0
        # Known enum-like values ('Hardware', 'Enterprise', 'overdue') point at their table.
        self.value_index: dict[str, set[str]] = defaultdict(set)
        for t in tables:
            for c in t.columns:
                for v in c.sample_values:
                    if len(v) >= 4:
                        self.value_index[v.lower()].add(t.name)
        self.graph: dict[str, set[str]] = defaultdict(set)
        for t in tables:
            for _col, ref in t.foreign_keys:
                ref_table = ref.split(".")[0]
                if ref_table != t.name:
                    self.graph[t.name].add(ref_table)
                    self.graph[ref_table].add(t.name)

    @staticmethod
    def _normalise(scores: dict[str, float]) -> dict[str, float]:
        top = max(scores.values(), default=0.0)
        return {k: v / top for k, v in scores.items()} if top > 0 else {}

    def table_hints(self, question: str) -> set[str]:
        """Tables named directly or through a business synonym ("clients" -> customers)."""
        hints: set[str] = set()
        lowered = f" {question.lower()} "
        for value, owners in self.value_index.items():
            if re.search(rf"\b{re.escape(value)}\b", lowered):
                hints |= owners
        for token in tokenize(question):
            for word in (token, token[:-1] if token.endswith("s") else token):
                if word in self.tables or f"{word}s" in self.tables:
                    hints.add(word if word in self.tables else f"{word}s")
                for syn in SYNONYMS.get(word, ()):
                    if syn in self.tables:
                        hints.add(syn)
        return hints

    def score(self, question: str, hint_bonus: float = 0.4) -> dict[str, float]:
        kw = self._normalise(self.keyword.table_scores(question))
        combined = {t: (1 - self.semantic_weight) * s for t, s in kw.items()}
        if self.semantic is not None:
            try:
                sem = self._normalise(self.semantic.table_scores(question))
            except Exception as exc:  # noqa: BLE001 - retrieval must degrade, not fail
                logger.warning("semantic retrieval failed, keyword only: %s", exc)
                sem = {}
                combined = kw
            for t, s in sem.items():
                combined[t] = combined.get(t, 0.0) + self.semantic_weight * s
        for t in self.table_hints(question):
            combined[t] = combined.get(t, 0.0) + hint_bonus
        return dict(sorted(combined.items(), key=lambda kv: -kv[1]))

    def retrieve(
        self, question: str, k: int = 4, min_relative_score: float = 0.35, allowed_tables: set[str] | None = None
    ) -> RetrievalResult:
        """Return up to ``k`` relevant tables plus bridge tables needed for joins.

        Args:
            question: the user's natural-language question.
            k: max tables taken from the ranking (bridges are added on top).
            min_relative_score: drop tables scoring below this fraction of the best one.
            allowed_tables: RBAC filter; tables outside it are never returned.
        """
        scores = self.score(question)
        if allowed_tables is not None:
            scores = {t: s for t, s in scores.items() if t in allowed_tables}
        best = next(iter(scores.values()), 0.0)
        matched = [t for t, s in scores.items() if s >= best * min_relative_score][:k]
        if not matched:  # nothing matched: fall back to the core sales tables
            matched = [t for t in ("orders", "customers") if allowed_tables is None or t in allowed_tables]
        bridges = self._bridge_tables(matched, allowed_tables)
        for t in list(matched):
            for companion in COMPANION_TABLES.get(t, ()):
                if (
                    companion not in matched
                    and companion not in bridges
                    and (allowed_tables is None or companion in allowed_tables)
                ):
                    bridges.append(companion)
        return RetrievalResult(tables=matched + bridges, matched=matched, bridges=bridges, scores=scores)

    def _shortest_path(self, start: str, goal: str, allowed: set[str] | None) -> list[str]:
        queue: deque[list[str]] = deque([[start]])
        seen = {start}
        while queue:
            path = queue.popleft()
            if path[-1] == goal:
                return path
            for nxt in sorted(self.graph[path[-1]]):
                if nxt not in seen and (allowed is None or nxt in allowed):
                    seen.add(nxt)
                    queue.append(path + [nxt])
        return []

    def _bridge_tables(self, tables: list[str], allowed: set[str] | None) -> list[str]:
        bridges: list[str] = []
        anchor = tables[0] if tables else None
        for other in tables[1:]:
            for t in self._shortest_path(anchor, other, allowed)[1:-1]:
                if t not in tables and t not in bridges:
                    bridges.append(t)
        return bridges

    def render_schema(self, table_names: Iterable[str], hidden_columns: dict[str, set[str]] | None = None) -> str:
        """Prompt text for the chosen tables only."""
        hidden_columns = hidden_columns or {}
        return "\n\n".join(self.tables[t].to_prompt(hidden_columns.get(t)) for t in table_names if t in self.tables)


def build_retriever(
    tables: list[TableDoc], backend: str = "hybrid", persist_path: str | None = None
) -> SchemaRetriever:
    """Create the retriever. ``backend`` is ``"hybrid"`` (Chroma + keyword) or ``"keyword"``."""
    semantic: SemanticIndex | None = None
    if backend == "hybrid":
        try:
            semantic = ChromaSemanticIndex(tables, persist_path)
        except Exception as exc:  # noqa: BLE001 - missing model/network must not break the app
            logger.warning("ChromaDB unavailable (%s); using keyword retrieval only", exc)
    return SchemaRetriever(tables, semantic)
