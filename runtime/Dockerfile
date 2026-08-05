# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
FROM heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c AS builder

WORKDIR /backend
COPY go.mod go.sum ./
RUN go mod download
COPY . ./
RUN go build --trimpath -mod=readonly -buildmode=plugin -o backend.so .

FROM heroiclabs/nakama:3.40.0@sha256:92fb184e3271be12fd4d239766afb285322a50aaf769a59433445d59624c78cd

ARG PAPER_RAID_SOURCE_REVISION
RUN case "$PAPER_RAID_SOURCE_REVISION" in \
      ''|*[!0-9a-f]*) exit 64 ;; \
    esac \
    && test "${#PAPER_RAID_SOURCE_REVISION}" -eq 40
LABEL org.opencontainers.image.source="https://github.com/TrillionniumFoundation/Trillionnium-Nakama" \
      org.opencontainers.image.revision="$PAPER_RAID_SOURCE_REVISION" \
      org.opencontainers.image.title="Trillionnium Nakama Paper Raid Runtime" \
      org.opencontainers.image.description="Nakama authoritative runtime for signed Paper Raid research sessions"

COPY --from=builder --chown=65532:65532 /backend/backend.so /nakama/data/modules/backend.so
USER 65532:65532
