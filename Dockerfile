FROM python:3.11-slim@sha256:cdbd05fb6f457ca275ff51ce00d93d865ca0b6a25f5ffb08262d94f6835771e5

ARG SOPS_VERSION=3.11.0
ARG TARGETARCH

# Install git, sops, age, and docker CLI
RUN apt-get update && \
    apt-get install -y git curl age ca-certificates && \
    arch="${TARGETARCH:-amd64}" && \
    case "$arch" in \
      amd64) sops_arch=amd64 ;; \
      arm64) sops_arch=arm64 ;; \
      *) echo "Unsupported architecture for sops: $arch" >&2; exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${sops_arch}" -o /usr/local/bin/sops && \
    chmod 0755 /usr/local/bin/sops && \
    curl -fsSL https://get.docker.com -o get-docker.sh && \
    sh get-docker.sh && \
    rm get-docker.sh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ /app/src/

# Create config directory
RUN mkdir -p /app/config

EXPOSE 8080

# Run HiveMind
ENTRYPOINT ["python3", "-m", "src.main"]
CMD ["/app/config/hivemind-config.yml"]
