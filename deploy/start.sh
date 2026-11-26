#!/bin/sh
# Apply database migrations, then start the web server. Safe to run on every start: migrations only apply what is missing.
set -e
cd /srv/backend
alembic upgrade head
exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w "${WEB_CONCURRENCY:-2}" -b "0.0.0.0:${PORT:-8000}" \
  --timeout 60 --graceful-timeout 30 --access-logfile - --forwarded-allow-ips "*"
