import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_evaluator import (
    recall_at_k,
    precision_at_k,
    _sources_overlap,
    _normalize_source,
    compare_with_baseline,
)


class TestNormalizeSource:
    def test_strips_path_prefix(self):
        assert _normalize_source("documentos/ops/file.pdf") == "ops/file.pdf"

    def test_strips_page_suffix(self):
        assert _normalize_source("file.pdf (pag. 15, Seccion)") == "file.pdf"

    def test_strips_both(self):
        assert _normalize_source("data/documentos/rite/doc.pdf (pag. 2)") == "rite/doc.pdf"

    def test_empty(self):
        assert _normalize_source("") == ""

    def test_no_prefix(self):
        assert _normalize_source("manual.pdf") == "manual.pdf"


class TestSourcesOverlap:
    def test_exact_match(self):
        expected = ["ops/file.pdf"]
        retrieved = ["documentos/ops/file.pdf (pag. 3)"]
        assert _sources_overlap(expected, retrieved) == 1

    def test_no_match(self):
        expected = ["ops/file.pdf"]
        retrieved = ["rite/other.pdf"]
        assert _sources_overlap(expected, retrieved) == 0

    def test_partial_match(self):
        expected = ["ops/a.pdf", "ops/b.pdf"]
        retrieved = ["documentos/ops/a.pdf (pag. 1)", "rite/c.pdf"]
        assert _sources_overlap(expected, retrieved) == 1

    def test_empty_expected(self):
        assert _sources_overlap([], ["a.pdf"]) == 0

    def test_empty_retrieved(self):
        assert _sources_overlap(["a.pdf"], []) == 0


class TestRecallAtK:
    def test_perfect_recall(self):
        expected = ["a.pdf"]
        retrieved = ["documentos/a.pdf (pag. 1)", "b.pdf"]
        assert recall_at_k(expected, retrieved) == 1.0

    def test_zero_recall(self):
        expected = ["a.pdf"]
        retrieved = ["b.pdf", "c.pdf"]
        assert recall_at_k(expected, retrieved) == 0.0

    def test_partial_recall(self):
        expected = ["a.pdf", "b.pdf"]
        retrieved = ["documentos/a.pdf (pag. 1)", "c.pdf"]
        assert recall_at_k(expected, retrieved) == 0.5

    def test_no_expected_returns_one(self):
        assert recall_at_k([], ["a.pdf"]) == 1.0

    def test_no_retrieved(self):
        assert recall_at_k(["a.pdf"], []) == 0.0


class TestPrecisionAtK:
    def test_perfect_precision(self):
        expected = ["a.pdf", "b.pdf"]
        retrieved = ["documentos/a.pdf", "documentos/b.pdf"]
        assert precision_at_k(expected, retrieved) == 1.0

    def test_zero_precision(self):
        expected = ["a.pdf"]
        retrieved = ["b.pdf", "c.pdf"]
        assert precision_at_k(expected, retrieved) == 0.0

    def test_half_precision(self):
        expected = ["a.pdf"]
        retrieved = ["documentos/a.pdf", "c.pdf"]
        assert precision_at_k(expected, retrieved) == 0.5

    def test_no_retrieved_returns_zero(self):
        assert precision_at_k(["a.pdf"], []) == 0.0

    def test_no_expected_returns_zero(self):
        assert precision_at_k([], ["a.pdf"]) == 0.0


class TestCompareWithBaseline:
    def test_improvement(self):
        current = {"avg_recall": 0.8, "avg_precision": 0.7, "avg_domain_match": 0.9, "results": []}
        baseline = {"avg_recall": 0.6, "avg_precision": 0.5, "avg_domain_match": 0.8, "results": []}
        comparison = compare_with_baseline(current, baseline)
        assert comparison["improved"] is True
        assert comparison["deltas"]["avg_recall"] == 0.2
        assert comparison["deltas"]["avg_precision"] == 0.2
        assert comparison["deltas"]["avg_domain_match"] == 0.1

    def test_regression_detected(self):
        current = {
            "avg_recall": 0.5, "avg_precision": 0.5, "avg_domain_match": 0.5,
            "results": [{"id": 1, "question": "test?", "recall": 0.3}],
        }
        baseline = {
            "avg_recall": 0.8, "avg_precision": 0.8, "avg_domain_match": 0.8,
            "results": [{"id": 1, "question": "test?", "recall": 0.9}],
        }
        comparison = compare_with_baseline(current, baseline)
        assert comparison["improved"] is False
        assert len(comparison["regressions"]) == 1
        assert comparison["regressions"][0]["id"] == 1

    def test_no_regression_within_tolerance(self):
        current = {
            "avg_recall": 0.79, "avg_precision": 0.8, "avg_domain_match": 0.8,
            "results": [{"id": 1, "question": "test?", "recall": 0.80}],
        }
        baseline = {
            "avg_recall": 0.8, "avg_precision": 0.8, "avg_domain_match": 0.8,
            "results": [{"id": 1, "question": "test?", "recall": 0.81}],
        }
        comparison = compare_with_baseline(current, baseline)
        assert len(comparison["regressions"]) == 0

    def test_empty_results(self):
        current = {"avg_recall": 0.0, "avg_precision": 0.0, "avg_domain_match": 0.0, "results": []}
        baseline = {"avg_recall": 0.0, "avg_precision": 0.0, "avg_domain_match": 0.0, "results": []}
        comparison = compare_with_baseline(current, baseline)
        assert comparison["improved"] is True
        assert len(comparison["regressions"]) == 0
