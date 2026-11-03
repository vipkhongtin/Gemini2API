FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV GEMINI2API_HOST=0.0.0.0 \
    GEMINI2API_PORT=18610 \
    GEMINI2API_CHROMIUM_PATH=/usr/bin/chromium

EXPOSE 18610
VOLUME ["/app/data"]
CMD ["python", "-m", "gemini2api"]
