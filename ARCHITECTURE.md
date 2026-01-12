# HiveMind Architecture

## Overview

HiveMind is a GitOps continuous deployment system for Docker Swarm, inspired by Flux CD for Kubernetes. It follows the GitOps principle: **Git as the single source of truth**.

## Core Principles

1. **Declarative**: All infrastructure is declared in Git
2. **Versioned**: Git provides versioning and audit trail
3. **Automated**: Changes are automatically applied
4. **Reconciled**: System continuously converges to desired state

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Git Repository                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  stacks.yml  │  │ compose files│  │  .env files  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└────────────────────────────┬────────────────────────────────┘
                             │
                             │ Git Clone/Pull
                             │ (Poll Interval)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    HiveMind Controller                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Reconciliation Loop                      │  │
│  │  1. Poll Git for changes                             │  │
│  │  2. Parse stacks.yml                                 │  │
│  │  3. Compare with deployed state                      │  │
│  │  4. Apply changes (deploy/update/remove)             │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Git Manager  │  │Stack Manager │  │ State Cache  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└────────────────────────────┬────────────────────────────────┘
                             │
                             │ Docker API
                             │ (docker stack deploy/rm)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      Docker Swarm                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Stack A    │  │   Stack B    │  │   Stack C    │     │
│  │ ┌──────────┐ │  │ ┌──────────┐ │  │ ┌──────────┐ │     │
│  │ │ Service  │ │  │ │ Service  │ │  │ │ Service  │ │     │
│  │ │ Service  │ │  │ │ Service  │ │  │ │ Service  │ │     │
│  │ └──────────┘ │  │ └──────────┘ │  │ └──────────┘ │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. Git Repository Manager

**Responsibilities**:
- Clone repository on first run
- Poll for changes at configured interval
- Pull latest commits
- Handle authentication (HTTP/SSH)
- Track current commit hash

**Implementation**: `GitRepository` class
- Uses subprocess to run git commands
- Supports HTTP basic auth and SSH keys
- Maintains local clone in temp directory

### 2. Stack Manager

**Responsibilities**:
- Parse `stacks.yml` configuration
- Deploy stacks using `docker stack deploy`
- Remove disabled/deleted stacks
- Track deployed stack state
- Calculate compose file hashes for change detection

**Implementation**: `SwarmStackManager` class
- Interfaces with Docker CLI
- Maintains hash cache of deployed stacks
- Handles stack lifecycle (deploy/update/remove)

### 3. Reconciliation Loop

**Responsibilities**:
- Orchestrate Git sync and stack deployment
- Compare desired state (Git) with actual state (Swarm)
- Apply changes to converge to desired state
- Handle errors and retry logic

**Implementation**: `HiveMind` class
- Main control loop
- Configurable poll interval
- Graceful error handling

### 4. State Management

**Current**: In-memory hash cache
**Future**: Persistent state store (SQLite/etcd)

## Data Flow

```
1. Timer Trigger (every poll_interval seconds)
   │
   ▼
2. Git Pull
   │
   ├─ No changes → Skip reconciliation
   │
   └─ Has changes → Continue
      │
      ▼
3. Parse stacks.yml
   │
   ▼
4. For each stack:
   │
   ├─ Stack enabled?
   │  │
   │  ├─ Yes → Check if deployed
   │  │  │
   │  │  ├─ Not deployed → Deploy
   │  │  │
   │  │  └─ Deployed → Check hash
   │  │     │
   │  │     ├─ Changed → Update
   │  │     │
   │  │     └─ Same → Skip
   │  │
   │  └─ No → Check if deployed
   │     │
   │     └─ Deployed → Remove
   │
   ▼
5. Wait for next interval
```

## Configuration Model

### HiveMind Configuration
```yaml
git:
  url: string              # Git repository URL
  branch: string           # Branch to track (default: main)
  path: string             # Path within repo (default: .)
  username: string         # Optional: HTTP auth username
  password: string         # Optional: HTTP auth password
  ssh_key: string          # Optional: SSH key path
  poll_interval: int       # Seconds between polls (default: 60)
```

### Stacks Configuration
```yaml
stacks:
  - name: string           # Stack name (unique)
    compose_file: string   # Path to compose file (relative to repo)
    enabled: boolean       # Deploy this stack? (default: true)
    env_file: string       # Optional: environment file path
```

## Deployment Models

### Model 1: Standalone Process
```bash
python3 hivemind.py config.yml
```
- Simple deployment
- Good for testing
- No high availability

### Model 2: Docker Service (Recommended)
```yaml
services:
  controller:
    image: hivemind:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - config:/config
    deploy:
      placement:
        constraints:
          - node.role == manager
```
- Runs on manager node
- Automatic restart
- Containerized

### Model 3: Multi-Controller (Future)
- Multiple controllers with leader election
- High availability
- Distributed state

## Security Considerations

### Current Implementation
1. **Git Credentials**: Stored in config file
   - ⚠️ Plaintext passwords
   - ✅ Support for SSH keys
   
2. **Docker Socket**: Direct access required
   - ⚠️ Root-level access to Docker
   - ✅ Runs on manager node only

3. **Network**: No external API
   - ✅ No attack surface
   - ✅ Internal communication only

### Future Improvements
1. **Secret Management**
   - Docker secrets integration
   - External secret providers (Vault)
   - Encrypted configuration

2. **RBAC**
   - Role-based access control
   - Multi-tenancy support
   - Audit logging

3. **API Security**
   - JWT authentication
   - TLS/HTTPS
   - Rate limiting

## Scalability

### Current Limitations
- Single controller instance
- In-memory state
- Sequential stack deployment
- No distributed locking

### Future Enhancements
- Leader election for multi-controller
- Persistent state store
- Parallel stack deployment
- Distributed coordination (etcd/Consul)

## Monitoring & Observability

### Current
- Structured logging (Python logging)
- Console output

### Planned
- Prometheus metrics
  - Reconciliation duration
  - Git sync status
  - Stack deployment success/failure
  - Error rates
- Event stream
  - Deployment events
  - Error events
  - State changes
- Health checks
  - Git connectivity
  - Docker API connectivity
  - Controller health

## Error Handling

### Git Errors
- Network failures → Retry on next interval
- Authentication failures → Log error, continue
- Repository not found → Fatal error

### Docker Errors
- Stack deployment failure → Log error, continue
- Docker daemon unavailable → Retry
- Invalid compose file → Skip stack, log error

### Recovery
- Graceful degradation
- Continue processing other stacks on error
- Automatic retry on next reconciliation

## Comparison with Alternatives

### vs Flux (Kubernetes)
- **Similarities**: GitOps, reconciliation loop, declarative
- **Differences**: Swarm vs K8s, simpler model, no CRDs

### vs Portainer
- **Similarities**: Swarm management, web UI
- **Differences**: GitOps vs manual, no UI (yet), automation-first

### vs Docker Compose
- **Similarities**: Compose file format
- **Differences**: Continuous deployment, Git-based, automated

## Future Architecture Considerations

### Plugin System
```python
class StackPlugin:
    def pre_deploy(self, stack: Stack) -> bool
    def post_deploy(self, stack: Stack) -> None
    def validate(self, stack: Stack) -> List[Error]
```

### Event System
```python
class Event:
    type: EventType
    timestamp: datetime
    stack: str
    message: str
    metadata: dict
```

### Multi-Cluster
```yaml
clusters:
  - name: production
    endpoint: tcp://prod-manager:2377
  - name: staging
    endpoint: tcp://stage-manager:2377
```

## Performance Characteristics

### Resource Usage
- **CPU**: Low (polling + occasional deployments)
- **Memory**: ~50-100MB (Python process)
- **Network**: Minimal (Git pulls only)
- **Disk**: Temporary Git clone (~repo size)

### Timing
- **Poll Interval**: Configurable (default 60s)
- **Git Pull**: ~1-5s (depends on repo size)
- **Stack Deploy**: ~5-30s (depends on stack complexity)
- **Full Reconciliation**: ~10-60s (depends on stack count)

## Testing Strategy

### Unit Tests
- Git operations (mocked)
- Stack parsing
- Hash calculation
- Configuration validation

### Integration Tests
- End-to-end with test Git repo
- Docker stack deployment
- Reconciliation loop

### System Tests
- Multi-stack scenarios
- Error conditions
- Recovery scenarios
