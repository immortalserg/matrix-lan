# Build from runtime/build (the parent of this directory).
# Initial shared runtime; Alpine binaries and native WeChat require separate
# compatibility work before this image replaces the existing services.
FROM dock.mau.dev/mautrix/signal:latest AS signal
FROM dock.mau.dev/mautrix/whatsapp:v26.10 AS whatsapp
FROM dock.mau.dev/mautrix/telegram:v26.10 AS telegram
FROM dock.mau.dev/mautrix/meta:ig-v26.10 AS instagram
FROM dock.mau.dev/mautrix/twitter@sha256:0276d454df0bed3713610a8c6eef1470245f531dd2aee35ad6084dd0332621b7 AS twitter
FROM dock.mau.dev/mautrix/linkedin@sha256:c6d885f92aed640fa95cf573d383e91c6eecab3ce63183bb246efd27852bdca6 AS linkedin
FROM golang:1-alpine3.24 AS facebook
RUN apk add --no-cache git ca-certificates build-base olm-dev
WORKDIR /build
COPY facebook/source/ .
RUN go build -tags goolm -p 2 -o /mautrix-meta ./cmd/mautrix-meta
FROM golang:1.25-bookworm AS go-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libsqlite3-dev libolm-dev ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src/email
COPY email/source/ .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=1 go build -trimpath -o /out/matrimail ./cmd/matrimail
WORKDIR /src/tiktok
COPY tiktok/source/ .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=1 go build -trimpath -o /out/matrix-tiktok ./cmd/matrix-tiktok

FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libolm3 libsqlite3-0 ca-certificates tzdata binutils && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.56.0 aiohttp==3.13.2 Pillow==11.3.0 psycopg2-binary==2.9.10 PyYAML==6.0.2
COPY --from=go-build /out/ /usr/local/bin/
# Keep Alpine executables with their original library trees. Do not overwrite
# Ubuntu system libraries; the launcher must select the matching ELF loader.
COPY --from=signal / /opt/bridge-roots/signal/
COPY --from=whatsapp / /opt/bridge-roots/whatsapp/
COPY --from=telegram / /opt/bridge-roots/telegram/
COPY --from=instagram / /opt/bridge-roots/instagram/
COPY --from=twitter / /opt/bridge-roots/twitter/
COPY --from=linkedin / /opt/bridge-roots/linkedin/
COPY --from=facebook /mautrix-meta /opt/bridge-roots/instagram/usr/bin/mautrix-facebook
COPY threads/*.py threads/*.js /opt/bridges/threads/
COPY social/*.py social/*.js /opt/bridges/social/
COPY shopping/*.py shopping/*.js /opt/bridges/shopping/
COPY imo/*.py imo/*.js /opt/bridges/imo/
COPY wechat/*.py /opt/bridges/wechat/
COPY bridges/manager.py bridges/storage.py bridges/sql_storage.py /opt/bridges/
ENV PYTHONPATH=/opt/bridges
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/bridges
RUN install -d -m 0700 -o 10001 -g 10001 /var/log/matrix-bridges
USER 10001:10001
ENTRYPOINT ["python", "/opt/bridges/manager.py"]
