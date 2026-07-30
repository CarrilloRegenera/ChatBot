import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "evaluate_golden_retrieval.py"
SPEC = importlib.util.spec_from_file_location("evaluate_golden_retrieval", SCRIPT_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


def test_golden_retrieval_runner_uses_versioned_dataset_and_percentiles():
    assert runner.GOLDEN_PATH.is_file()
    assert runner._percentile([], 0.95) == 0.0
    assert runner._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
    assert runner._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0


def test_normalized_source_hit_matches_source_path_without_page_label():
    assert runner._normalized_source_hit(
        ["ops/guia.pdf (pag. 2)"],
        ["ops/guia.pdf"],
        lambda value: value.lower(),
    ) is True
    assert runner._normalized_source_hit([], ["ops/guia.pdf"], lambda value: value.lower()) is False
    assert runner._normalized_source_hit([], [], lambda value: value.lower()) is None
