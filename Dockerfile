# Multi-arch base so this builds natively on x86-64 and on the Jetson's arm64.
# Every dependency ships aarch64 wheels, so no compiler is needed.
FROM python:3.11-slim

# tzdata lets TIMEZONE resolve IANA zones inside the container; without it
# every stage_changed_at lands on UTC wall-clock time.
#
# git is here for scripts/fetch_bsdata.py, which is run inside this container
# (by deploy.sh on a fresh box, and by hand when the pin is bumped). It fetches
# one pinned SHA at --depth 1 and verifies what it got, which is the whole
# point of pinning — a tarball download would drop that check and pull 65 MB of
# whatever HEAD happens to be. Leaving git out builds a perfectly healthy image
# that cannot obtain the rules data it needs to be useful.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata git \
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
