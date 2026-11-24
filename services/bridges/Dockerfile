# Build from runtime/build (the parent of this directory).
# Initial shared runtime; Alpine binaries and native WeChat require separate
# compatibility work before this image replaces the existing services.
FROM golang:1.25-bookworm AS go-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libsqlite3-dev libolm-dev ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src/email
COPY email/source/ .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=1 go build -trimpath -o /out/matrimail ./cmd/matrimail
WORKDIR /src/tiktok
COPY tiktok/source/ .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=1 go build -trimpath -o /out/matrix-tiktok ./cmd/matrix-tiktok

FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libolm3 libsqlite3-0 ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.56.0 aiohttp==3.13.2 Pillow==11.3.0 psycopg2-binary==2.9.10
COPY --from=go-build /out/ /usr/local/bin/
COPY threads/*.py threads/*.js /opt/bridges/threads/
COPY social/*.py social/*.js /opt/bridges/social/
COPY shopping/*.py shopping/*.js /opt/bridges/shopping/
COPY imo/*.py imo/*.js /opt/bridges/imo/
COPY wechat/*.py /opt/bridges/wechat/
COPY bridges/manager.py /opt/bridges/manager.py
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/bridges
USER 10001:10001
ENTRYPOINT ["python", "/opt/bridges/manager.py"]
