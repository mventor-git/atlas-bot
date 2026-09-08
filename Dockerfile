# Contractor-Bot (V1: bot runs anywhere, PDF-via-Excel needs Windows host)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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
