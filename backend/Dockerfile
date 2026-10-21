# backend/Dockerfile - image for the API, the seeder, the evaluator and the test runner.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so Docker caches this layer between code edits.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY . .

# Never run as root inside the container.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

# Pre-download the small embedding model used by ChromaDB so the first question is fast
# and the container works without internet at runtime. Non-fatal: keyword retrieval is the fallback.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warm up'])" \
    || echo "embedding model not downloaded; hybrid retrieval will fall back to keyword search"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
