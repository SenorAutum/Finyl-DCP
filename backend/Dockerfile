# Finyl-DCP backend — FastAPI + Uvicorn
FROM python:3.12-slim

WORKDIR /app

# tesseract-ocr + poppler-utils power the "Process ID" OCR endpoint
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Migration runner + numbered migrations (so the image can bootstrap/upgrade a DB)
COPY scripts ./scripts
COPY migrations ./migrations

# Uploaded client documents live here (mount a volume in production)
ENV STORAGE_DIR=/app/storage
RUN mkdir -p /app/storage

# Listen on $PORT when the platform sets one (e.g. AWS App Runner), else 8000.
# Abacus / docker-compose / local set no PORT, so behaviour is unchanged there.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
