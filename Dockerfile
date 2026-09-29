# One image, no external services. The database is a SQLite file inside the
# container (or on a mounted volume) and the fixtures are copied in, so the
# portal comes up with the network off.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The database lives in /app/data and is created on first boot.
RUN useradd --create-home --uid 10001 dogfood \
    && mkdir -p /app/data \
    && chown -R dogfood:dogfood /app/data
USER dogfood

EXPOSE 8080

# Standard library only, like the acceptance checker.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"

# main.py imports fixtures.json into the empty database and prints the
# acceptance headers before it starts listening.
CMD ["python", "backend/main.py"]
