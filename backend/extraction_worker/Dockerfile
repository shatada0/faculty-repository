# локально — project-base, в CI — образ из registry
ARG BASE_IMAGE=project-base
FROM ${BASE_IMAGE}
# OCR сканов: Tesseract (Apache 2.0) с русской и английской моделями
USER root
RUN apt-get update     && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng     && rm -rf /var/lib/apt/lists/*
USER app
COPY shared shared
COPY extraction_worker extraction_worker
CMD ["uvicorn", "extraction_worker.src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--loop", "uvloop", "--http", "httptools"]
