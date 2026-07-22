"""HiveMind Web UI — Flask dashboard for viewing and managing Docker Swarm stacks."""
import hashlib
import hmac
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Maps service_id -> replica count saved before a scale-to-zero
_replica_cache: dict[str, int] = {}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HiveMind Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
    <style>
        body { background-color: #0d1117; color: #c9d1d9; }
        .navbar { background-color: #161b22 !important; border-bottom: 1px solid #30363d; }
        .stack-card {
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            overflow: hidden;
        }
        .stack-card-header {
            background: transparent;
            border-bottom: 1px solid #30363d;
            padding: 12px 16px;
        }
        .service-row td {
            padding: 8px 12px;
            vertical-align: middle;
            border-color: #21262d;
        }
        .service-row:last-child td { border-bottom: none; }
        .service-row:hover { background: rgba(255,255,255,0.03); }
        .stat-card {
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 16px 20px;
        }
        .stat-value { font-size: 2rem; font-weight: 600; line-height: 1; }
        .badge-running  { background-color: #238636; }
        .badge-degraded { background-color: #9e6a03; }
        .badge-stopped  { background-color: #6e7681; }
        .image-text {
            font-family: monospace;
            font-size: 0.75rem;
            color: #8b949e;
            max-width: 220px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            display: block;
        }
        .btn-toggle { min-width: 72px; font-size: 0.8rem; padding: 3px 10px; }
        .spinner-sm  { width: 14px; height: 14px; border-width: 2px; }
    </style>
</head>
<body>

<nav class="navbar navbar-dark px-3 py-2">
    <span class="navbar-brand fw-semibold mb-0">
        <i class="bi bi-hexagon-fill text-warning me-2"></i>HiveMind
    </span>
    <div class="ms-auto d-flex align-items-center gap-3">
        <span id="last-update" class="text-muted small"></span>
        <button class="btn btn-sm btn-outline-secondary" onclick="loadStacks()">
            <i class="bi bi-arrow-clockwise"></i>
        </button>
        <div class="form-check form-switch mb-0">
            <input class="form-check-input" type="checkbox" id="auto-refresh" checked onchange="toggleAutoRefresh()">
            <label class="form-check-label text-muted small" for="auto-refresh">Auto</label>
        </div>
    </div>
</nav>

<div class="container-fluid py-4 px-4">
    <div class="row g-3 mb-4" id="stats-row"></div>
    <div id="stacks-container">
        <div class="text-center py-5">
            <div class="spinner-border text-warning" role="status"></div>
            <div class="mt-2 text-muted small">Loading stacks…</div>
        </div>
    </div>
    <div id="error-banner" class="alert alert-danger d-none mt-3 small"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let _refreshTimer = null;

function toggleAutoRefresh() {
    clearInterval(_refreshTimer);
    if (document.getElementById('auto-refresh').checked) {
        _refreshTimer = setInterval(loadStacks, 30000);
    }
}

async function loadStacks() {
    try {
        const res = await fetch('/api/stacks');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        renderStats(data.stacks || []);
        renderStacks(data.stacks || []);
        document.getElementById('last-update').textContent =
            'Updated ' + new Date().toLocaleTimeString();
        document.getElementById('error-banner').classList.add('d-none');
    } catch (e) {
        const banner = document.getElementById('error-banner');
        banner.textContent = 'Failed to load: ' + e.message;
        banner.classList.remove('d-none');
    }
}

function renderStats(stacks) {
    let services = 0, running = 0, stopped = 0;
    for (const s of stacks) {
        for (const svc of s.services) {
            services++;
            if (svc.status === 'running') running++;
            else if (svc.status === 'stopped') stopped++;
        }
    }
    document.getElementById('stats-row').innerHTML = `
        <div class="col-6 col-md-3">
            <div class="stat-card">
                <div class="text-muted small mb-2"><i class="bi bi-layers me-1"></i>Stacks</div>
                <div class="stat-value text-info">${stacks.length}</div>
            </div>
        </div>
        <div class="col-6 col-md-3">
            <div class="stat-card">
                <div class="text-muted small mb-2"><i class="bi bi-box me-1"></i>Services</div>
                <div class="stat-value">${services}</div>
            </div>
        </div>
        <div class="col-6 col-md-3">
            <div class="stat-card">
                <div class="text-muted small mb-2"><i class="bi bi-play-circle me-1"></i>Running</div>
                <div class="stat-value text-success">${running}</div>
            </div>
        </div>
        <div class="col-6 col-md-3">
            <div class="stat-card">
                <div class="text-muted small mb-2"><i class="bi bi-stop-circle me-1"></i>Stopped</div>
                <div class="stat-value text-secondary">${stopped}</div>
            </div>
        </div>`;
}

function statusBadge(svc) {
    if (!svc.is_scalable) return '<span class="badge bg-secondary">global</span>';
    if (svc.status === 'running')
        return `<span class="badge badge-running">${svc.running_replicas}/${svc.desired_replicas}</span>`;
    if (svc.status === 'degraded')
        return `<span class="badge badge-degraded">${svc.running_replicas}/${svc.desired_replicas}</span>`;
    return '<span class="badge badge-stopped">0</span>';
}

function toggleBtn(svc) {
    if (!svc.is_scalable) return '<span class="text-muted small">global</span>';
    const on = svc.desired_replicas > 0;
    return `<button class="btn btn-sm btn-toggle ${on ? 'btn-outline-danger' : 'btn-outline-success'}"
                id="toggle-${svc.id}"
                onclick="toggleService('${svc.id}', '${svc.name}', this)">
                <i class="bi ${on ? 'bi-stop-fill' : 'bi-play-fill'} me-1"></i>${on ? 'Stop' : 'Start'}
            </button>`;
}

function stackHealthBadge(stack) {
    const scalable = stack.services.filter(s => s.is_scalable);
    if (!scalable.length) return '';
    if (scalable.every(s => s.status === 'running'))
        return '<span class="badge badge-running">healthy</span>';
    if (scalable.every(s => s.status === 'stopped'))
        return '<span class="badge badge-stopped">stopped</span>';
    return '<span class="badge badge-degraded">partial</span>';
}

function renderStacks(stacks) {
    const container = document.getElementById('stacks-container');
    if (!stacks.length) {
        container.innerHTML = `<div class="text-center text-muted py-5">
            <i class="bi bi-inbox fs-1 d-block mb-2"></i>No stacks found</div>`;
        return;
    }
    const cards = stacks.map(stack => {
        const rows = stack.services.map(svc => `
            <tr class="service-row">
                <td>
                    <div class="fw-medium small">${svc.name}</div>
                    <span class="image-text" title="${svc.image}">${svc.image}</span>
                </td>
                <td class="text-center">${statusBadge(svc)}</td>
                <td class="text-end pe-3">${toggleBtn(svc)}</td>
            </tr>`).join('');
        const count = stack.services.length;
        return `
        <div class="col-12 col-lg-6 col-xxl-4">
            <div class="stack-card h-100">
                <div class="stack-card-header d-flex align-items-center justify-content-between">
                    <span class="fw-semibold">
                        <i class="bi bi-layers text-warning me-2"></i>${stack.name}
                    </span>
                    <div class="d-flex align-items-center gap-2">
                        <span class="text-muted small">${count} service${count !== 1 ? 's' : ''}</span>
                        ${stackHealthBadge(stack)}
                    </div>
                </div>
                <table class="table table-borderless mb-0"><tbody>${rows}</tbody></table>
            </div>
        </div>`;
    }).join('');
    container.innerHTML = `<div class="row g-3">${cards}</div>`;
}

async function toggleService(serviceId, serviceName, btn) {
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-sm" role="status"></span>';
    try {
        const res = await fetch(`/api/services/${serviceId}/toggle`, {method: 'POST'});
        const data = await res.json();
        if (!res.ok) {
            alert('Failed to toggle ' + serviceName + ': ' + (data.error || 'Unknown error'));
            btn.innerHTML = original;
            btn.disabled = false;
            return;
        }
        setTimeout(loadStacks, 800);
    } catch (e) {
        alert('Error: ' + e.message);
        btn.innerHTML = original;
        btn.disabled = false;
    }
}

loadStacks();
_refreshTimer = setInterval(loadStacks, 30000);
</script>
</body>
</html>"""


def _build_app(
    reconcile_trigger: Optional[Callable[[], None]] = None,
    webhook_secret: Optional[str] = None,
    webhook_branch: str = "main",
):
    from flask import Flask, jsonify, request, render_template_string

    app = Flask(__name__)
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)

    def _client():
        import docker
        return docker.from_env()

    def _shorten_image(image: str) -> str:
        if "@" in image:
            image = image.split("@")[0]
        for prefix in ("docker.io/library/", "docker.io/", "ghcr.io/"):
            if image.startswith(prefix):
                image = image[len(prefix):]
                break
        return image

    @app.route("/")
    def index():
        return render_template_string(HTML_TEMPLATE)

    @app.route("/api/stacks")
    def api_stacks():
        try:
            client = _client()
            services = client.services.list()
            stacks: dict[str, dict] = {}

            for svc in services:
                spec = svc.attrs.get("Spec", {})
                labels = spec.get("Labels", {})
                stack_name = labels.get("com.docker.stack.namespace") or "_standalone"

                mode = spec.get("Mode", {})
                replicated = mode.get("Replicated")
                is_scalable = replicated is not None
                desired_replicas = replicated.get("Replicas", 0) if is_scalable else None

                tasks = svc.tasks(filters={"desired-state": "running"})
                running = sum(
                    1 for t in tasks
                    if t.get("Status", {}).get("State") == "running"
                )

                image = _shorten_image(
                    spec.get("TaskTemplate", {})
                        .get("ContainerSpec", {})
                        .get("Image", "unknown")
                )

                if is_scalable:
                    if desired_replicas == 0:
                        status = "stopped"
                    elif running >= desired_replicas:
                        status = "running"
                    else:
                        status = "degraded"
                else:
                    status = "running" if running > 0 else "stopped"

                entry = {
                    "id": svc.id,
                    "name": svc.name,
                    "image": image,
                    "desired_replicas": desired_replicas,
                    "running_replicas": running,
                    "is_scalable": is_scalable,
                    "status": status,
                }

                if stack_name not in stacks:
                    stacks[stack_name] = {"name": stack_name, "services": []}
                stacks[stack_name]["services"].append(entry)

            result = sorted(stacks.values(), key=lambda s: s["name"])
            for s in result:
                s["services"].sort(key=lambda x: x["name"])

            return jsonify({"stacks": result})

        except Exception as e:
            logger.exception("Failed to list stacks")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/services/<service_id>/toggle", methods=["POST"])
    def api_toggle(service_id):
        try:
            client = _client()
            svc = client.services.get(service_id)
            spec = svc.attrs.get("Spec", {})
            replicated = spec.get("Mode", {}).get("Replicated")

            if replicated is None:
                return jsonify({"error": "Cannot scale global-mode services"}), 400

            current = replicated.get("Replicas", 0)
            if current > 0:
                _replica_cache[service_id] = current
                svc.scale(0)
                return jsonify({"enabled": False, "replicas": 0})
            else:
                target = _replica_cache.get(service_id, 1)
                svc.scale(target)
                return jsonify({"enabled": True, "replicas": target})

        except Exception as e:
            logger.exception("Failed to toggle service %s", service_id)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/webhooks/git", methods=["POST"])
    def api_git_webhook():
        if not webhook_secret:
            return jsonify({"error": "Git webhook is not configured"}), 503

        body = request.get_data(cache=True)
        expected_digest = hmac.new(
            webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        gitea_signature = request.headers.get("X-Gitea-Signature")
        github_signature = request.headers.get("X-Hub-Signature-256")

        if gitea_signature:
            signature_valid = hmac.compare_digest(expected_digest, gitea_signature)
        elif github_signature:
            signature_valid = hmac.compare_digest(
                f"sha256={expected_digest}",
                github_signature,
            )
        else:
            signature_valid = False

        if not signature_valid:
            return jsonify({"error": "Invalid webhook signature"}), 401

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid JSON payload"}), 400

        event = (
            request.headers.get("X-Gitea-Event")
            or request.headers.get("X-GitHub-Event")
            or ""
        ).casefold()
        if event == "ping":
            return jsonify({"status": "ok"})
        if event != "push":
            return jsonify({"status": "ignored", "reason": "not a push event"}), 202

        expected_ref = f"refs/heads/{webhook_branch}"
        if payload.get("ref") != expected_ref:
            return jsonify({"status": "ignored", "reason": "branch does not match"}), 202

        if reconcile_trigger is None:
            return jsonify({"error": "Reconciliation trigger is unavailable"}), 503

        reconcile_trigger()
        return jsonify({"status": "accepted"}), 202

    return app


def start(
    host: str = "0.0.0.0",
    port: int = 8080,
    reconcile_trigger: Optional[Callable[[], None]] = None,
    webhook_secret: Optional[str] = None,
    webhook_branch: str = "main",
) -> threading.Thread:
    app = _build_app(
        reconcile_trigger=reconcile_trigger,
        webhook_secret=webhook_secret,
        webhook_branch=webhook_branch,
    )

    def _run():
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

    thread = threading.Thread(target=_run, daemon=True, name="webui")
    thread.start()
    logger.info("Web UI listening on http://%s:%d", host, port)
    return thread
