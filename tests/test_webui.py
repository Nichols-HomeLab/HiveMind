"""Tests for the HiveMind Git webhook endpoint."""

import hashlib
import hmac
import json
from unittest.mock import Mock

from src.webui import _build_app


SECRET = "high-entropy-webhook-secret"


def _payload(ref="refs/heads/main"):
    return json.dumps({"ref": ref}, separators=(",", ":")).encode("utf-8")


def _digest(payload):
    return hmac.new(SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def test_gitea_push_triggers_reconciliation():
    trigger = Mock()
    client = _build_app(trigger, SECRET, "main").test_client()
    payload = _payload()

    response = client.post(
        "/api/webhooks/git",
        data=payload,
        content_type="application/json",
        headers={
            "X-Gitea-Event": "push",
            "X-Gitea-Signature": _digest(payload),
        },
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "accepted"
    trigger.assert_called_once_with()


def test_github_push_triggers_reconciliation():
    trigger = Mock()
    client = _build_app(trigger, SECRET, "main").test_client()
    payload = _payload()

    response = client.post(
        "/api/webhooks/git",
        data=payload,
        content_type="application/json",
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": f"sha256={_digest(payload)}",
        },
    )

    assert response.status_code == 202
    trigger.assert_called_once_with()


def test_webhook_rejects_invalid_signature():
    trigger = Mock()
    client = _build_app(trigger, SECRET, "main").test_client()

    response = client.post(
        "/api/webhooks/git",
        data=_payload(),
        content_type="application/json",
        headers={"X-Gitea-Event": "push", "X-Gitea-Signature": "invalid"},
    )

    assert response.status_code == 401
    trigger.assert_not_called()


def test_webhook_ignores_push_to_other_branch():
    trigger = Mock()
    client = _build_app(trigger, SECRET, "main").test_client()
    payload = _payload("refs/heads/feature")

    response = client.post(
        "/api/webhooks/git",
        data=payload,
        content_type="application/json",
        headers={
            "X-Gitea-Event": "push",
            "X-Gitea-Signature": _digest(payload),
        },
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "ignored"
    trigger.assert_not_called()


def test_webhook_without_secret_is_unavailable():
    client = _build_app(Mock(), "", "main").test_client()

    response = client.post("/api/webhooks/git", json={"ref": "refs/heads/main"})

    assert response.status_code == 503
