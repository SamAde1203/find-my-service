FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install . && useradd --create-home --uid 10001 app
USER app

ENV FMS_CORPUS_PATH=/app/data/corpus.json
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready')" || exit 1

CMD ["uvicorn", "find_my_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
