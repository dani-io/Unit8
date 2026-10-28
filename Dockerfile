# Unit8 — trading worker image.
# Runs main.py as a long-lived process (no HTTP port). On Linux it trades
# through the MT5 bridge (broker type "bridge"); credentials come from env.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC \
    UNIT8_CONFIG=config/checklist_bridge.json

WORKDIR /app

# Dependencies first so code changes don't bust the pip layer
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as an unprivileged user
RUN useradd --create-home --uid 1000 unit8 && chown -R unit8:unit8 /app
USER unit8

CMD ["python", "main.py"]
