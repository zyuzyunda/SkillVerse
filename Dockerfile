FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY src ./src
COPY scripts ./scripts
RUN chmod +x /app/scripts/docker_entrypoint.sh

# Данные seed монтируются volume'ом /data
CMD ["python", "-m", "src.db.init_db"]
