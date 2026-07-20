from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_memory_distance_default_matches_the_deployment_value():
    config = (REPOSITORY_ROOT / "src" / "backend" / "config.py").read_text(encoding="utf-8")
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "deploy-chatbot.yml").read_text(encoding="utf-8")

    assert 'MEMORY_MAX_DISTANCE = float(os.getenv("MEMORY_MAX_DISTANCE", "0.6"))' in config
    assert 'MEMORY_MAX_DISTANCE: "0.6"' in workflow
