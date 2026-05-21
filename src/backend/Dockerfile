FROM python:3.11-bookworm

ARG RERANK_MODEL=paraphrase-multilingual-MiniLM-L12-v2
ARG RERANK_MODEL_REVISION=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/models/sentence-transformers \
    RERANK_MODEL=${RERANK_MODEL} \
    RERANK_MODEL_REVISION=${RERANK_MODEL_REVISION}

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
RUN pip install --upgrade pip && pip install -r requirements.txt
RUN python -c "import os; from sentence_transformers import SentenceTransformer; model=os.environ['RERANK_MODEL']; revision=os.getenv('RERANK_MODEL_REVISION') or None; kwargs={'revision': revision} if revision else {}; SentenceTransformer(model, **kwargs)"

COPY backend/ .
COPY frontend/ /frontend/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
