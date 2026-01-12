#!/bin/bash
# HiveMind Bootstrap Script
# Usage: ./bootstrap.sh

set -e

echo "HiveMind Bootstrap"
echo "=================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed"
    exit 1
fi

# Prompt for configuration
echo "Enter your Git repository configuration:"
read -p "Repository URL: " REPO_URL
read -p "Branch (default: main): " REPO_BRANCH
REPO_BRANCH=${REPO_BRANCH:-main}
read -p "Path in repo (default: .): " REPO_PATH
REPO_PATH=${REPO_PATH:-"."}

echo ""
echo "Authentication (leave blank for public repos or SSH):"
read -p "Username (optional): " GIT_USERNAME
if [ ! -z "$GIT_USERNAME" ]; then
    read -sp "Password/Token: " GIT_PASSWORD
    echo ""
fi

read -p "Poll interval in seconds (default: 60): " POLL_INTERVAL
POLL_INTERVAL=${POLL_INTERVAL:-60}

# Create configuration file
cat > hivemind-config.yml <<EOF
git:
  url: "${REPO_URL}"
  branch: "${REPO_BRANCH}"
  path: "${REPO_PATH}"
EOF

if [ ! -z "$GIT_USERNAME" ]; then
cat >> hivemind-config.yml <<EOF
  username: "${GIT_USERNAME}"
  password: "${GIT_PASSWORD}"
EOF
fi

cat >> hivemind-config.yml <<EOF
  poll_interval: ${POLL_INTERVAL}
EOF

echo ""
echo "Configuration saved to hivemind-config.yml"
echo ""

# Build Docker image
echo "Building HiveMind Docker image..."
docker build -t hivemind:latest .

echo ""
echo "Bootstrap complete!"
echo ""
echo "Choose deployment method:"
echo ""
echo "1. Docker Compose (standalone):"
echo "   docker-compose up -d"
echo ""
echo "2. Docker Swarm (recommended for production):"
echo "   docker swarm init  # if not already initialized"
echo "   docker stack deploy -c hivemind-stack.yml hivemind"
echo ""
