# Optional: deploy with Docker so scanned documents and photos can be read (OCR).
# On Render: New > Web Service > "Docker" runtime (or switch the existing service's runtime to Docker).
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD gunicorn --chdir backend app:app --bind 0.0.0.0:${PORT:-10000} --timeout 120
