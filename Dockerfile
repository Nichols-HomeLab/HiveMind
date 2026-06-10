FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0

# Install git and docker CLI
RUN apt-get update && \
    apt-get install -y git curl && \
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

# Run HiveMind
ENTRYPOINT ["python3", "-m", "src.main"]
CMD ["/app/config/hivemind-config.yml"]
