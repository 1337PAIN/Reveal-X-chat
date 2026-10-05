# Reveal-X container image.
#
# The CMD here is kept identical to the Procfile's web command, and
# tests/test_deployment.py fails if they drift apart -- two copies of a
# deployment command is how a platform quietly runs something nobody tested.

FROM python:3.12-slim

# opencv-python-headless skips the GUI stack but still links libglib, and
# libgomp is needed by scikit-learn's OpenMP threading. Without these the
# import fails at *runtime*, not at build time, so the image looks fine and the
# first request 500s.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first: this layer only rebuilds when the pins change, not on
# every source edit. requirements-dev is deliberately absent -- production must
# stand up without the test and ONNX-export toolchain.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Run as a non-root user. If the app is ever made to write somewhere it should
# not, this is the difference between a bad day and a root-owned container.
RUN useradd --create-home --shell /usr/sbin/nologin revealx \
    && mkdir -p /app/app/data /app/app/shares \
    && chown -R revealx:revealx /app
USER revealx

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-5000}/api/auth/config" || exit 1

# Identical to the Procfile. See the note at the top.
CMD ["sh", "-c", "gunicorn --worker-class gthread --workers 1 --threads 100 --timeout 120 --bind 0.0.0.0:${PORT:-5000} app:app"]
