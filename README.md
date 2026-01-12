# HiveMind

**GitOps for Docker Swarm** - A Flux-inspired continuous deployment system for managing Docker Swarm stacks declaratively from Git.

## Overview

HiveMind brings GitOps principles to Docker Swarm, allowing you to manage your entire swarm infrastructure through Git. Similar to Flux for Kubernetes, HiveMind continuously monitors a Git repository and automatically deploys stack changes to your swarm cluster.

## Features

- **GitOps Workflow**: Declare your infrastructure in Git, HiveMind handles deployment
- **Automatic Reconciliation**: Polls Git repository and syncs changes automatically
- **Stack Management**: Deploy, update, and remove Docker stacks based on `stacks.yml`
- **Authentication Support**: HTTP(S) basic auth and SSH key support
- **Bootstrap Process**: Simple setup similar to `flux bootstrap`
- **No WebUI Required**: Lightweight, runs as a Docker service on manager nodes
- **Declarative Configuration**: All configuration in YAML files

## Quick Start

### Prerequisites

- Docker Swarm initialized (`docker swarm init`)
- Git repository with your stack configurations

### Bootstrap

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

The bootstrap script will:
1. Prompt for Git repository details
2. Create `hivemind-config.yml`
3. Build the Docker image
4. Provide deployment options

### Deploy with Docker

**Option 1: Docker Compose (standalone)**
```bash
docker-compose up -d
```

**Option 2: Docker Swarm (production)**
```bash
docker swarm init  # if not already initialized
docker stack deploy -c hivemind-stack.yml hivemind
```

**Using Makefile:**
```bash
make build           # Build image
make deploy-compose  # Deploy with compose
make deploy-swarm    # Deploy to swarm
make logs            # View logs
```

## Configuration

### HiveMind Configuration (`hivemind-config.yml`)

```yaml
git:
  url: "https://github.com/yourusername/your-infra-repo.git"
  branch: "main"
  path: "."  # Path within repo where stacks.yml is located
  username: "your-username"  # Optional for private repos
  password: "your-token"     # Optional for private repos
  poll_interval: 60          # Seconds between Git polls
```

### Stacks Configuration (`stacks.yml` in your repo)

```yaml
stacks:
  - name: traefik
    compose_file: stacks/traefik/docker-compose.yml
    enabled: true
    env_file: stacks/traefik/.env  # Optional
  
  - name: monitoring
    compose_file: stacks/monitoring/docker-compose.yml
    enabled: true
  
  - name: app
    compose_file: stacks/app/docker-compose.yml
    enabled: false  # Disabled stacks won't be deployed
```

## Project Structure

**HiveMind Repository:**
```
HiveMind/
├── src/                          # Python source code
│   ├── __init__.py
│   ├── main.py                   # Entry point
│   ├── controller.py             # Main controller
│   ├── git_manager.py            # Git operations
│   └── stack_manager.py          # Stack management
├── examples/                     # Example configurations
├── Dockerfile                    # Container image
├── docker-compose.yml            # Compose deployment
├── hivemind-stack.yml            # Swarm deployment
├── bootstrap.sh                  # Bootstrap script
└── hivemind-config.yml           # Your config (gitignored)
```
## How It Works

1. **Initialization**: HiveMind clones your Git repository
2. **Polling**: Every `poll_interval` seconds, it checks for new commits
3. **Reconciliation**: On changes, it reads `stacks.yml` and compares with deployed stacks
4. **Deployment**: Deploys new/updated stacks, removes disabled stacks
5. **Repeat**: Continues monitoring for changes

## Architecture

```
┌─────────────────┐
│   Git Repo      │
│  (stacks.yml)   │
└────────┬────────┘
         │
         │ Poll & Pull
         ▼
┌─────────────────┐
│   HiveMind      │
│   Controller    │
└────────┬────────┘
         │
         │ Deploy/Update
         ▼
┌─────────────────┐
│  Docker Swarm   │
│    Stacks       │
└─────────────────┘
```

## Examples

See the `examples/` directory for:
- Sample `stacks.yml` configuration
- Traefik reverse proxy stack
- Monitoring stack (Prometheus + Grafana)


## Roadmap

Notificatons on rollout/failures
Webui to view rollout/faliures like weave

## License

MIT

## Contributing

Contributions welcome! Please open an issue or PR.