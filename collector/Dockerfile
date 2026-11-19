# Evidence collector: an isolated Playwright browser that captures what a
# flagged address shows. Build context is the REPO ROOT (compose sets this),
# because the collector shares the network guard with the rest of the project.
#
# Runs on demand only (docker compose run --rm collector ...); it is not a
# long-running service. It writes artifacts to its own /evidence volume and
# has no database access - ingestion happens from the trusted app side.
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

# The base image (built FROM ubuntu:jammy) ships Ubuntu's Python 3.10, the
# playwright pip package built for this exact release, and its browsers.
# Pin the package explicitly anyway so the match is checked at build time;
# pytest (needs Python >=3.10) runs the boundary tests inside this image.
RUN pip install --no-cache-dir playwright==1.49.1 pytest==9.2.0

COPY worker/netguard.py ./
COPY collector/capture.py ./

CMD ["python", "capture.py", "--help"]
