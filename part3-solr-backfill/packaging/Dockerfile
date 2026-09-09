# syntax=docker/dockerfile:1
# Linux amd64 standalone binary (no Python on the target server).
FROM python:3.12-slim-bookworm AS build
ARG ONEFILE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY requirements.txt requirements-build.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-build.txt
COPY *.py ./
RUN if [ "$ONEFILE" = "1" ]; then \
      MODE=--onefile; \
    else \
      MODE=--onedir; \
    fi \
    && python -m PyInstaller --noconfirm --clean "$MODE" \
         --name solr-doc-meta-backfill \
         --collect-all oracledb \
         --collect-all openpyxl \
         --hidden-import dotenv \
         solr_doc_meta_backfill.py

FROM scratch AS export
COPY --from=build /src/dist/ /
