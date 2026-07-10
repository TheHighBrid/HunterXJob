FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/data/storage_states /app/data/rendered

ENV DATABASE_PATH=/app/data/hunterxjob.db \
    PLAYWRIGHT_STORAGE_STATE_DIR=/app/data/storage_states \
    RENDERED_DOCS_DIR=/app/data/rendered

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
