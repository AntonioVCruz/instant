FROM golang:1.24.8-bookworm@sha256:4ed690d6649d63c312b99a6120025ec79ce3b542968a37da53d6236c7c61a848 AS builder

ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=4
WORKDIR /src
# Official release commits. Build from source because the old image and binary
# distribution endpoints are no longer consistently available.
RUN git init minio && cd minio \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth=1 origin 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a \
    && git checkout --detach FETCH_HEAD
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    cd minio && go build -p 4 -trimpath -ldflags='-s -w' -o /out/minio .
RUN git init mc && cd mc \
    && git remote add origin https://github.com/minio/mc.git \
    && git fetch --depth=1 origin 7394ce0dd2a80935aded936b09fa12cbb3cb8096 \
    && git checkout --detach FETCH_HEAD
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    cd mc && go build -p 4 -trimpath -ldflags='-s -w' -o /out/mc .

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /out/ /usr/local/bin/
COPY --from=builder /src/minio/LICENSE /usr/share/licenses/minio/LICENSE
COPY --from=builder /src/mc/LICENSE /usr/share/licenses/mc/LICENSE
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
    org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
ENTRYPOINT ["minio"]
