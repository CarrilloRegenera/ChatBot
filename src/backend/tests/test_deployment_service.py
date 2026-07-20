import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deployment_service


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
