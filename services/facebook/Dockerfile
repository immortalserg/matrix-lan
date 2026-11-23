FROM golang:1-alpine3.24 AS build
RUN apk add --no-cache git ca-certificates build-base olm-dev
WORKDIR /build
ENV GOMAXPROCS=2 GOMEMLIMIT=1536MiB
COPY source/ .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build go build -tags goolm -p 2 -ldflags "-X main.Commit=d1c0f61206494e2709099e8b737a6ceb2ff6a083" -o /mautrix-meta ./cmd/mautrix-meta
FROM dock.mau.dev/mautrix/meta:v26.11@sha256:b8928aaec0ac37ddaf5e04cded685c2503dafc3bf084737147a4d9ad342c299b
COPY --from=build /mautrix-meta /usr/bin/mautrix-meta
RUN /usr/bin/mautrix-meta --version
