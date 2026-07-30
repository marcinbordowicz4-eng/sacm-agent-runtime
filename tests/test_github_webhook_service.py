import hashlib
import hmac
import json

from sacm.core.github_webhook_service import GitHubWebhookService


def _payload() -> dict:
    return {
        "action": "labeled",
        "label": {"name": "sacm"},
        "issue": {"title": "Fix checkout", "body": "Tests are failing."},
        "repository": {"full_name": "owner/repository", "default_branch": "main"},
    }


def test_webhook_signature_requires_configured_secret(monkeypatch):
    body = b"{}"
    monkeypatch.setenv("SACM_GITHUB_WEBHOOK_SECRET", "secret")
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert GitHubWebhookService.verify_signature(body, signature)
    assert not GitHubWebhookService.verify_signature(body, "sha256=invalid")


def test_labeled_issue_creates_mapped_durable_run(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SACM_GITHUB_REPOSITORIES_JSON",
        json.dumps({"owner/repository": str(tmp_path)}),
    )

    service = GitHubWebhookService(db)
    run_id = service.create_run(_payload(), delivery_id="delivery-1")

    assert run_id
    assert service.create_run(_payload(), delivery_id="delivery-1") == run_id
    assert service.create_run(
        {**_payload(), "label": {"name": "unrelated"}}, delivery_id="delivery-2"
    ) is None
