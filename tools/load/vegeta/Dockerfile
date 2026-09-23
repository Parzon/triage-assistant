# vegeta built from the official release binary, checksum-verified - no
# official image exists, and a random community image is someone else's
# supply chain. TARGETARCH (set by buildx) picks amd64 or arm64 (Apple
# Silicon); the checksum line for that asset is looked up in the release's
# checksums file.
FROM alpine:3.24
ARG VEGETA_VERSION=12.13.0
ARG TARGETARCH=amd64
RUN apk add --no-cache curl \
 && cd /tmp \
 && curl -fsSLO "https://github.com/tsenart/vegeta/releases/download/v${VEGETA_VERSION}/vegeta_${VEGETA_VERSION}_linux_${TARGETARCH}.tar.gz" \
 && curl -fsSLO "https://github.com/tsenart/vegeta/releases/download/v${VEGETA_VERSION}/vegeta_${VEGETA_VERSION}_checksums.txt" \
 && grep " vegeta_${VEGETA_VERSION}_linux_${TARGETARCH}.tar.gz$" "vegeta_${VEGETA_VERSION}_checksums.txt" | sha256sum -c - \
 && tar -xzf "vegeta_${VEGETA_VERSION}_linux_${TARGETARCH}.tar.gz" -C /usr/local/bin vegeta \
 && rm -f /tmp/vegeta_* \
 && apk del curl
ENTRYPOINT ["vegeta"]
