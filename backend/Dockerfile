# Python 3.11 to MATCH the local dev/test venv (backend/.venv). Keeping the
# runtime interpreter identical to the one the tests run on avoids the whole
# class of "passes locally, breaks in Docker" version skew — e.g. Python 3.10's
# asyncio.TimeoutError being a distinct class from the builtin TimeoutError
# (merged only in 3.11+).
#
# Microsoft's Playwright images only ship 3.10 (jammy) or 3.12 (noble), never
# 3.11, so we base on the official python:3.11 image and install just Chromium
# — the only browser the scraper launches (pw.chromium.launch).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl backs the docker-compose healthcheck (GET /health); slim has no curl.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Install ONLY Chromium plus its OS shared-library dependencies. The browser
# build is pinned by the playwright==1.47.0 package installed above, so it stays
# in lockstep with the client just like the old MS base image did. `--with-deps`
# runs apt-get for the browser's native libs; clean the lists afterwards.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
