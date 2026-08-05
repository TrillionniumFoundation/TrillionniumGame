# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
ARG SOURCE_DATE_EPOCH=0
FROM heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c AS builder

ARG SOURCE_DATE_EPOCH
WORKDIR /backend
COPY go.mod go.sum ./
RUN go mod download
COPY . ./
RUN case "$SOURCE_DATE_EPOCH" in \
      ''|*[!0-9]*) exit 64 ;; \
    esac \
    && go build --trimpath -mod=readonly -buildmode=plugin -o backend.so . \
    && install -d -m 0755 \
      /release/nakama/data/modules \
      /release/nakama/share/doc/trnm-paper-raid \
    && install -m 0644 -o 65532 -g 65532 backend.so \
      /release/nakama/data/modules/backend.so \
    && install -m 0644 -o 65532 -g 65532 sbom.cdx.json \
      /release/nakama/share/doc/trnm-paper-raid/sbom.cdx.json \
    && find /release -type d -exec touch --date="@$SOURCE_DATE_EPOCH" {} + \
    && find /release -type f -exec touch --date="@$SOURCE_DATE_EPOCH" {} +

FROM heroiclabs/nakama:3.40.0@sha256:92fb184e3271be12fd4d239766afb285322a50aaf769a59433445d59624c78cd

ARG PAPER_RAID_SOURCE_REVISION
ARG PAPER_RAID_SOURCE_TREE
ARG PAPER_RAID_SBOM_SHA256
RUN case "$PAPER_RAID_SOURCE_REVISION" in \
      ''|*[!0-9a-f]*) exit 64 ;; \
    esac \
    && case "$PAPER_RAID_SOURCE_TREE" in \
      ''|*[!0-9a-f]*) exit 64 ;; \
    esac \
    && case "$PAPER_RAID_SBOM_SHA256" in \
      ''|*[!0-9a-f]*) exit 64 ;; \
    esac \
    && test "${#PAPER_RAID_SOURCE_REVISION}" -eq 40 \
    && test "${#PAPER_RAID_SOURCE_TREE}" -eq 40 \
    && test "${#PAPER_RAID_SBOM_SHA256}" -eq 64
LABEL org.opencontainers.image.source="https://github.com/TrillionniumFoundation/Trillionnium-Nakama" \
      org.opencontainers.image.revision="$PAPER_RAID_SOURCE_REVISION" \
      org.opencontainers.image.title="Trillionnium Nakama Paper Raid Runtime" \
      org.opencontainers.image.description="Nakama authoritative runtime for signed Paper Raid research sessions" \
      org.opencontainers.image.sbom="/nakama/share/doc/trnm-paper-raid/sbom.cdx.json" \
      org.trillionnium.source.tree="$PAPER_RAID_SOURCE_TREE" \
      org.trillionnium.sbom.sha256="$PAPER_RAID_SBOM_SHA256"

# Copy a fully normalized directory tree. Copying a lone file makes BuildKit
# stamp its parent directory with wall-clock time, which changes the layer
# digest even when the plugin bytes themselves are reproducible.
COPY --from=builder /release/ /
USER 65532:65532
