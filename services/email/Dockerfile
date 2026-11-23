FROM golang:1.25-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends libolm-dev ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY source/ .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=1 go build -trimpath -o /matrimail ./cmd/matrimail

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libolm3 ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
COPY --from=build /matrimail /usr/local/bin/matrimail
WORKDIR /data
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/matrimail"]
