from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_dockerfile_does_not_persist_hugging_face_tokens_in_environment():
    dockerfile = (REPOSITORY_ROOT / "src" / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "HF_TOKEN=${HF_TOKEN}" not in dockerfile
    assert "HUGGINGFACE_HUB_TOKEN=${HUGGINGFACE_HUB_TOKEN}" not in dockerfile


def test_deployment_uses_secret_build_arguments_for_hugging_face_tokens():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "deploy-chatbot.yml").read_text(encoding="utf-8")

    assert '--secret-build-arg "HF_TOKEN=$env:HF_TOKEN"' in workflow
    assert '--secret-build-arg "HUGGINGFACE_HUB_TOKEN=$env:HUGGINGFACE_HUB_TOKEN"' in workflow
    assert '--build-arg "HF_TOKEN=$env:HF_TOKEN"' not in workflow
    assert '--build-arg "HUGGINGFACE_HUB_TOKEN=$env:HUGGINGFACE_HUB_TOKEN"' not in workflow
