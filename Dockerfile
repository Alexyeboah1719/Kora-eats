# KORA EATS: one image serves the API and the website (the frontend is plain files, no build step).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ /srv/backend/
COPY frontend/ /srv/frontend/
COPY deploy/start.sh /srv/start.sh

# run as a normal user, with a folder for uploaded photos (mount a persistent volume here in production)
RUN useradd --create-home --uid 10001 kora && mkdir -p /data/uploads && chown -R kora /data /srv && chmod +x /srv/start.sh
USER kora
ENV UPLOAD_DIR=/data/uploads PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')" || exit 1
CMD ["/srv/start.sh"]
