# Container for internal deployment. No PaaS config is provided, by design (CLAUDE.md).
#
# Build from the repository root:
#   docker build -f app/Dockerfile -t naceesa:0.1 app
#
# Run (the codebooks and the .env are mounted, never baked in - they are bank-internal):
#   docker run --rm -p 8000:8000 \
#     -v /path/to/codebooks:/app/data/codebooks:ro \
#     -v /path/to/app.env:/app/.env:ro \
#     -v naceesa-cache:/app/data/cache \
#     naceesa:0.1
#
# The cache volume matters: without it the answer cache and the usage ledger are lost on
# every restart, so repeated issuers are paid for again and the daily budget cannot be
# enforced (it fails closed, refusing to spend - see core/classify/budget.py).

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml README.md ./
COPY core ./core
COPY api ./api
COPY ui ./ui
COPY config ./config
RUN pip install --no-cache-dir .

# Writable state: the answer cache and the usage ledger.
RUN mkdir -p /app/data/cache /app/data/codebooks \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000

# The startup check refuses to serve an inconsistent codebook, so an unhealthy container
# fails fast rather than emitting wrong CTS IDs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
