FROM python:3.11-slim@sha256:af796fc0cf5d7f68e33d30ff5ab9eccd5c3468e9f919a9c1f5b9fea1d24699ab

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
