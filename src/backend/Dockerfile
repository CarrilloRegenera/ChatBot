FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm

ARG EMBEDDING_MODEL=intfloat/multilingual-e5-small
ARG RERANK_MODEL=${EMBEDDING_MODEL}
ARG RERANK_MODEL_REVISION=
ARG EMBEDDING_FP16=false
ARG EMBEDDING_QUERY_PREFIX=query:
ARG EMBEDDING_PASSAGE_PREFIX=passage:
ARG RAG_INDEX_VERSION=5
ARG HF_TOKEN=
ARG HUGGINGFACE_HUB_TOKEN=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/models/sentence-transformers \
    HF_TOKEN=${HF_TOKEN} \
    HUGGINGFACE_HUB_TOKEN=${HUGGINGFACE_HUB_TOKEN} \
    EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    EMBEDDING_FP16=${EMBEDDING_FP16} \
    EMBEDDING_QUERY_PREFIX=${EMBEDDING_QUERY_PREFIX} \
    EMBEDDING_PASSAGE_PREFIX=${EMBEDDING_PASSAGE_PREFIX} \
    RERANK_MODEL=${RERANK_MODEL} \
    RERANK_MODEL_REVISION=${RERANK_MODEL_REVISION} \
    RAG_INDEX_VERSION=${RAG_INDEX_VERSION}

WORKDIR /app

# Dependencias del sistema + ODBC Driver para SQL Server.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    unixodbc \
    unixodbc-dev \
    ca-certificates \
    build-essential \
    && curl -fsSL -o /tmp/packages-microsoft-prod.deb https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm -f /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --upgrade pip \
    && pip install --extra-index-url https://download.pytorch.org/whl/cpu "torch==2.4.1+cpu" \
    && pip install -r requirements.txt
RUN python -c "import os; from sentence_transformers import SentenceTransformer; model=os.environ['RERANK_MODEL']; revision=os.getenv('RERANK_MODEL_REVISION') or None; kwargs={'revision': revision} if revision else {}; SentenceTransformer(model, **kwargs)"

COPY backend/ .
COPY frontend/ /frontend/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
