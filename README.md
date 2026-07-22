# HiveMind **Assisted by claude and codex**

**GitOps for Docker Swarm** - A Flux-inspired continuous deployment system for managing Docker Swarm stacks declaratively from Git. 

## Overview

HiveMind brings GitOps principles to Docker Swarm, allowing you to manage your entire swarm infrastructure through Git. Similar to Flux for Kubernetes, HiveMind continuously monitors a Git repository and automatically deploys stack changes to your swarm cluster.

## Quick Start

### Prerequisites

- Docker Swarm initialized (`docker swarm init`)
- Git repository with your stack configurations

### Deploy with Docker

**Option 1: Using Pre-built Image from GitHub Container Registry**
```bash
# Pull the latest image
docker pull ghcr.io/Nichols-HomeLab/HiveMind:latest

# Run with environment variables
docker run -d \
  --name hivemind-controller \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e HIVEMIND_GIT_URL="https://github.com/yourusername/your-infra-repo.git" \
  -e HIVEMIND_GIT_BRANCH="main" \
  -e HIVEMIND_GIT_PATH="." \
  -e HIVEMIND_GIT_POLL_INTERVAL="60" \
  -e HIVEMIND_GIT_USERNAME="your-username" \
  -e HIVEMIND_GIT_PASSWORD="your-token" \
  ghcr.io/Nichols-HomeLab/HiveMind:latest
```

**Option 2: Docker Compose (standalone)**

1. Create a `.env` file or set environment variables:
```bash
HIVEMIND_GIT_URL=https://github.com/yourusername/your-infra-repo.git
HIVEMIND_GIT_BRANCH=main
HIVEMIND_GIT_PATH=.
HIVEMIND_GIT_POLL_INTERVAL=60
HIVEMIND_GIT_USERNAME=your-username
HIVEMIND_GIT_PASSWORD=your-token
```

2. Deploy:
```bash
docker-compose up -d
```

**Option 3: Docker Swarm (production)**
```bash
docker swarm init  # if not already initialized
docker stack deploy -c hivemind-stack.yml hivemind
```

## Configuration

HiveMind can be configured using either environment variables or a YAML configuration file.

### Environment Variables

When using Docker Compose or running the container directly, configure HiveMind using these environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HIVEMIND_GIT_URL` | **Yes** | - | Git repository URL (HTTPS) |
| `HIVEMIND_GIT_BRANCH` | No | `main` | Git branch to monitor |
| `HIVEMIND_GIT_PATH` | No | `.` | Path within repo where stacks.yml is located |
| `HIVEMIND_GIT_POLL_INTERVAL` | No | `60` | Fallback seconds between Git polls; overrides YAML when set |
| `HIVEMIND_GIT_USERNAME` | No | - | Username for private repos (HTTPS) |
| `HIVEMIND_GIT_PASSWORD` | No | - | Password/token for private repos (HTTPS) |
| `HIVEMIND_WEBHOOK_SECRET` | No | - | Shared HMAC secret for GitHub/Gitea push webhooks |
| `HIVEMIND_WEBHOOK_SECRET_FILE` | No | - | File containing the webhook secret; preferred over the environment variable |

### GitHub and Gitea webhooks

Configure either provider to send push events to:

```text
https://your-hivemind-host/api/webhooks/git
```

Use the same high-entropy secret in the provider and HiveMind. GitHub deliveries are verified
with `X-Hub-Signature-256`; Gitea deliveries are verified with `X-Gitea-Signature`. HiveMind
uses a constant-time HMAC-SHA256 comparison, ignores non-push events and other branches, and
wakes the controller for the configured Git branch. An invalid or unsigned request cannot
trigger reconciliation.

For Swarm, create the external secret expected by `hivemind-stack.yml`:

```bash
openssl rand -hex 32 | docker secret create hivemind_webhook_secret -
```

Polling remains as a recovery mechanism for missed deliveries. A longer value such as `3600`
is recommended when webhooks are enabled.

### Playback-aware Plex and Jellyfin updates

HiveMind can protect existing Plex and Jellyfin stacks from disruptive updates. A protected
update is first attempted during the configured midnight window. If a configured server API
reports that a movie or episode is playing, HiveMind retries with exponential backoff. It
deploys as soon as playback is idle, or forcibly deploys at the scheduled time on the third
day. New stacks are deployed immediately.

All media-update behavior is configured with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `HIVEMIND_MEDIA_UPDATE_ENABLED` | `false` | Enable protected media-stack update scheduling |
| `HIVEMIND_PLEX_STACKS` | `plex` | Comma-separated Plex stack names (case-insensitive) |
| `HIVEMIND_PLEX_URL` | - | Plex base URL, such as `http://plex:32400` |
| `HIVEMIND_PLEX_TOKEN` | - | Plex API token |
| `HIVEMIND_JELLYFIN_STACKS` | `jellyfin` | Comma-separated Jellyfin stack names (case-insensitive) |
| `HIVEMIND_JELLYFIN_URL` | - | Jellyfin base URL, such as `http://jellyfin:8096` |
| `HIVEMIND_JELLYFIN_API_KEY` | - | Jellyfin API key |
| `HIVEMIND_MEDIA_UPDATE_TIMEZONE` | `UTC` | IANA timezone used by the schedule |
| `HIVEMIND_MEDIA_UPDATE_HOUR` | `0` | Scheduled deployment hour (`0` is midnight) |
| `HIVEMIND_MEDIA_UPDATE_MINUTE` | `0` | Scheduled deployment minute |
| `HIVEMIND_MEDIA_UPDATE_WINDOW_MINUTES` | `60` | Window in which a newly seen update may use today's scheduled time |
| `HIVEMIND_MEDIA_UPDATE_BACKOFF_SECONDS` | `300` | Initial playback retry delay |
| `HIVEMIND_MEDIA_UPDATE_MAX_BACKOFF_SECONDS` | `21600` | Maximum playback retry delay |
| `HIVEMIND_MEDIA_UPDATE_MAX_DEFERRAL_DAYS` | `3` | Forced-deployment deadline in scheduled days |
| `HIVEMIND_MEDIA_API_TIMEOUT_SECONDS` | `10` | Plex/Jellyfin request timeout |
| `HIVEMIND_MEDIA_UPDATE_STATE_FILE` | `/var/lib/hivemind/media-update-state.json` | Persistent retry-state path |

If a server URL/token pair is omitted, that stack still waits for its scheduled time but skips
the playback check. If an API is configured but unavailable, HiveMind fails closed and backs
off until the API succeeds or the forced-deployment deadline is reached. Persist the state-file
directory (as the supplied Compose and Swarm manifests do) so the three-day deadline survives
controller restarts.

**Example `.env` file:**
```bash
HIVEMIND_GIT_URL=https://github.com/yourusername/your-infra-repo.git
HIVEMIND_GIT_BRANCH=main
HIVEMIND_GIT_PATH=.
HIVEMIND_GIT_POLL_INTERVAL=60
HIVEMIND_GIT_USERNAME=your-username
HIVEMIND_GIT_PASSWORD=ghp_your_github_token
```

### HiveMind Configuration File (`hivemind-config.yml`)

Alternatively, use a YAML configuration file:

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
    compose_files:
      - stacks/traefik/traefik.yml
      - stacks/traefik/authentik.yml
    enabled: true
    env_file: stacks/traefik/.env  # Optional
    replaces:
      - old-traefik

retired_stacks:
  - old-stack-with-no-replacement
  
  - name: monitoring
    compose_file: stacks/monitoring/docker-compose.yml
    enabled: true
  
  - name: app
    compose_file: stacks/app/docker-compose.yml
    enabled: false  # Disabled stacks won't be deployed
```

## Docker Image

HiveMind is automatically built and published to GitHub Container Registry on every push to `main` that modifies the `src/` directory.

**Available Images:**
- `ghcr.io/yourusername/hivemind:latest` - Latest stable build from main branch
- `ghcr.io/yourusername/hivemind:main` - Main branch build
- `ghcr.io/yourusername/hivemind:main-<sha>` - Specific commit SHA

**Pull the image:**
```bash
docker pull ghcr.io/yourusername/hivemind:latest
```

**Docker Compose Configuration:**

The `docker-compose.yml` includes:
- Volume mount for Docker socket (`/var/run/docker.sock`)
- Environment variable configuration
- Automatic restart policy

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
├── .github/
│   └── workflows/
│       └── docker-publish.yml    # CI/CD for image builds
├── examples/                     # Example configurations
├── Dockerfile                    # Container image
├── docker-compose.yml            # Compose deployment
├── hivemind-stack.yml            # Swarm deployment
├── bootstrap.sh                  # Bootstrap script
└── hivemind-config.yml           # Your config (gitignored)
```
## How It Works

1. **Initialization**: HiveMind clones your Git repository
2. **Triggering**: A signed GitHub/Gitea push webhook wakes HiveMind immediately
3. **Reconciliation**: On changes, it reads `stacks.yml` and compares with deployed stacks
4. **Deployment**: Deploys new/updated stacks, removes disabled stacks
5. **Fallback**: Polling still checks for missed webhook deliveries

## Architecture

```
┌─────────────────┐
│   Git Repo      │
│  (stacks.yml)   │
└────────┬────────┘
         │
         │ Signed webhook / fallback poll
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

Contributions welcome! Please open an issue or PR. I am a single developer in my free time using/making this so bear with me.
