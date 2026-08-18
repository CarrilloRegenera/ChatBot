import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deployment_service


def _completed_run() -> dict:
    return {
        "github_run_id": 12345,
        "status": "completed",
        "conclusion": "success",
        "branch": "sandbox",
        "requested_action": "full",
        "html_url": "https://github.com/CarrilloRegenera/ChatBot/actions/runs/12345",
        "last_notified_conclusion": None,
    }


def test_trigger_full_deploy_rejects_a_branch_other_than_the_configured_one(monkeypatch):
    monkeypatch.setattr(deployment_service, "GITHUB_DEPLOY_BRANCH", "sandbox")

    with pytest.raises(deployment_service.DeploymentConfigurationError, match="sandbox"):
        deployment_service.trigger_full_deploy(
            requested_by_email="admin@example.com",
            requested_by_name="Admin",
            branch="feature/unvalidated",
        )


def test_trigger_full_deploy_dispatches_only_the_configured_branch(monkeypatch):
    monkeypatch.setattr(deployment_service, "GITHUB_DEPLOY_BRANCH", "sandbox")
    requests = []
    monkeypatch.setattr(deployment_service, "_github_request", lambda *args, **kwargs: requests.append((args, kwargs)))

    result = deployment_service.trigger_full_deploy(
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
    )

    assert result["branch"] == "sandbox"
    assert requests[0][1]["payload"]["ref"] == "sandbox"
    assert requests[0][1]["payload"]["inputs"]["branch"] == "sandbox"


def test_completed_run_is_marked_notified_only_after_graph_accepts_the_email(monkeypatch):
    sent = []
    marked = []
    monkeypatch.setattr(deployment_service, "get_notification_settings", lambda: {"recipients": ["it@example.com"]})
    monkeypatch.setattr(
        deployment_service,
        "_send_graph_mail",
        lambda **kwargs: sent.append(kwargs),
    )
    monkeypatch.setattr(
        deployment_service,
        "_mark_notification_sent",
        lambda run_id, conclusion: marked.append((run_id, conclusion)),
    )

    assert deployment_service._maybe_notify(_completed_run()) is True
    assert len(sent) == 1
    assert marked == [(12345, "success")]


def test_completed_run_without_recipients_fails_instead_of_being_marked_notified(monkeypatch):
    monkeypatch.setattr(deployment_service, "get_notification_settings", lambda: {"recipients": []})
    marked = []
    monkeypatch.setattr(
        deployment_service,
        "_mark_notification_sent",
        lambda run_id, conclusion: marked.append((run_id, conclusion)),
    )

    with pytest.raises(deployment_service.DeploymentNotificationError, match="destinatarios"):
        deployment_service._maybe_notify(_completed_run())

    assert marked == []
