# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt requirements-admin.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-admin.txt

COPY app.py wsgi.py ./
COPY app ./app
COPY templates ./templates
COPY static ./static
# Seed only; Cloud uses USERS_CONFIG_GCS_URI as source of truth
COPY config ./config

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Admin provision (sheet + Exclusive GCS seed + IAP) can exceed 2–5 min.
# Keep in sync with scripts/deploy-admin.ps1 --timeout.
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 900 wsgi:app
