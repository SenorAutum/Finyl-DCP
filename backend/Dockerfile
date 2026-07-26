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

# Uploaded client documents live here (mount a volume in production)
ENV STORAGE_DIR=/app/storage
RUN mkdir -p /app/storage

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
