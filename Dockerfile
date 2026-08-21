# Multi-arch base so this builds natively on x86-64 and on the Jetson's arm64.
# Every dependency ships aarch64 wheels, so no compiler is needed.
FROM python:3.11-slim

# tzdata lets TIMEZONE resolve IANA zones inside the container; without it
# every stage_changed_at lands on UTC wall-clock time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PORT=3100

RUN mkdir -p /app/data

EXPOSE 3100

# app.py runs migrations on import, so a deploy migrates itself.
CMD ["python3", "app.py"]
