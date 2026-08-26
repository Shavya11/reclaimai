# ReclaimAI — one container, one port.
#
# No Node in this image. `ui/out` is the Next.js static export and it is
# committed to the repo, so the dashboard ships as files FastAPI serves rather
# than as a second build stage that can fail on a host with a different Node
# version than the one it was developed against.

FROM python:3.11-slim

# PYTHONUNBUFFERED so logs appear in the host's log viewer as they happen rather
# than when a buffer fills. PYTHONIOENCODING because the rupee sign is in almost
# every line this program prints and a container without it dies formatting money.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first: this layer is cached until the dependency list changes, so
# an application edit rebuilds in seconds instead of reinstalling the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY reclaim/ ./reclaim/
COPY ui/out/ ./ui/out/
COPY fixtures/ ./fixtures/

# SQLite needs somewhere to live. On a host with no attached disk this is
# ephemeral and the batch regenerates on boot; mount a volume here to keep it.
RUN mkdir -p /app/data

EXPOSE 8000

# Render, Railway and Fly all inject $PORT. Defaulting to 8000 keeps
# `docker run -p 8000:8000` working locally without setting anything.
CMD ["sh", "-c", "uvicorn reclaim.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
