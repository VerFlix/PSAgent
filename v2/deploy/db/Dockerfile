# syntax=docker/dockerfile:1
# PostgreSQL mit WAL-G für die Sicherung nach Unraid (#23). Baut die CI wie das App-Image und legt
# es als ghcr.io/verflix/psagent-db ab (amd64, arm64).
#
# WAL-G kommt als offizieller Build (https://github.com/wal-g/wal-g/releases) mit fester Version
# und Prüfsumme. Für ein Update: Version und beide Prüfsummen aus den *.sha256-Dateien des
# Releases eintragen.

FROM --platform=$BUILDPLATFORM debian:trixie-slim AS walg
ARG TARGETARCH
ADD --checksum=sha256:444ce216230c254edef803e098a59a1746806da3e0d55671d7b0f0082db14ef4 \
    https://github.com/wal-g/wal-g/releases/download/v3.0.9/wal-g-pg-24.04-amd64.tar.gz \
    /tmp/wal-g-amd64.tar.gz
ADD --checksum=sha256:cb37c5f8391bd8ef2efceeb9c6995059c0abf2fa74b9b717b42a16a7c93cfde1 \
    https://github.com/wal-g/wal-g/releases/download/v3.0.9/wal-g-pg-24.04-aarch64.tar.gz \
    /tmp/wal-g-arm64.tar.gz
RUN mkdir /tmp/wal-g \
    && tar -xzf "/tmp/wal-g-${TARGETARCH}.tar.gz" -C /tmp/wal-g \
    && install -m 0755 /tmp/wal-g/wal-g-pg-* /usr/local/bin/wal-g

FROM postgres:17
COPY --from=walg /usr/local/bin/wal-g /usr/local/bin/wal-g
