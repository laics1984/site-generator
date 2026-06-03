# Playwright Python image ships Chromium + Firefox + WebKit + all OS libs preinstalled.
# Pinned to match `playwright==1.47.0` in requirements.txt.
# Image is ~1.5 GB but skips all the "install browser deps" pain on first run.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is already in the playwright image; only install if missing on slim variants.
RUN command -v curl >/dev/null 2>&1 || ( \
        apt-get update && apt-get install -y --no-install-recommends curl && \
        rm -rf /var/lib/apt/lists/* )

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
