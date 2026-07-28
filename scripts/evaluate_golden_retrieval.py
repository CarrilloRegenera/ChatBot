"""Run the versioned golden retrieval set against one RAG backend.

Run it twice with the same corpus/index version to compare Chroma and Azure:
  python scripts/evaluate_golden_retrieval.py --backend chroma --save chroma.json
  python scripts/evaluate_golden_retrieval.py --backend azure_search --save azure.json

The report deliberately measures only evidence available in the golden set:
domain selection, expected phrase coverage, empty retrieval, and latency.  It
does not claim answer faithfulness; that needs chunk-level relevance labels.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = REPO_ROOT / "src" / "backend"
GOLDEN_PATH = BACKEND_PATH / "tests" / "golden_questions.json"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return round(ordered[index], 2)


def evaluate(backend: str, top_k: int) -> dict[str, Any]:
    os.environ["RAG_BACKEND"] = backend
    sys.path.insert(0, str(BACKEND_PATH))
    from rag_service import _normalize_text, search_documents_detailed  # pylint: disable=import-outside-toplevel

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    results = []
    latencies = []
    for item in golden:
        started = time.perf_counter()
        context, sources, stats = search_documents_detailed(item["question"], n_results=top_k)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        latencies.append(latency_ms)
        context_normalized = _normalize_text(context)
        selected_domains = set(stats.get("selected_domains") or [])
        expected_domains = set(item["expected_domains"])
        expected_phrases = item.get("expected_phrase_queries") or []
        phrase_hits = [
            phrase
            for phrase in expected_phrases
            if _normalize_text(phrase) in context_normalized
        ]
        results.append({
            "id": item["id"],
            "domain": item["domain"],
            "category": item["category"],
            "latency_ms": latency_ms,
            "selected_count": len(sources),
            "selected_domains": sorted(selected_domains),
            "expected_domains": sorted(expected_domains),
            "domain_hit": bool(selected_domains & expected_domains),
            "expected_phrase_count": len(expected_phrases),
            "expected_phrase_hits": phrase_hits,
            "phrase_coverage": (len(phrase_hits) / len(expected_phrases)) if expected_phrases else None,
            "sources": sources,
        })

    phrase_cases = [result for result in results if result["expected_phrase_count"]]
    return {
        "backend": backend,
        "top_k": top_k,
        "golden_path": str(GOLDEN_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "count": len(results),
        "empty_retrieval_rate": round(sum(not result["selected_count"] for result in results) / len(results), 4),
        "domain_hit_rate": round(sum(result["domain_hit"] for result in results) / len(results), 4),
        "phrase_coverage": round(
            sum(result["phrase_coverage"] for result in phrase_cases) / len(phrase_cases), 4
        ) if phrase_cases else None,
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluación reproducible del golden set de retrieval")
    parser.add_argument("--backend", choices=("chroma", "azure_search"), required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--save", type=Path)
    args = parser.parse_args()

    report = evaluate(args.backend, max(args.top_k, 1))
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False, indent=2))
    if args.save:
        args.save.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Resultados guardados en {args.save}")


if __name__ == "__main__":
    main()
