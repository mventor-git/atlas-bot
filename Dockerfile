# Atlas-Bot (PDF via headless soffice, works on host and container)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# PDF engine: headless LibreOffice Writer (slim, no recommends).
# NOTE: unvalidated (no Docker daemon at build time) - verify on first build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/ ./config/
COPY templates/ ./templates/
COPY scripts/ ./scripts/
COPY main.py ./

# exports/ logs/ database/ are mounted volumes (see compose), never baked in
CMD ["python", "main.py"]
