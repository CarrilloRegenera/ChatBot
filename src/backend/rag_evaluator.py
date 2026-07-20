"""Evaluador offline de retrieval contra ground truth de ConocimientoValidado.

Uso:
    python rag_evaluator.py --top-k 5
    python rag_evaluator.py --top-k 5 --baseline baseline.json
    python rag_evaluator.py --top-k 5 --save baseline.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import db_conn
from rag_service import search_documents_detailed, _expected_domains, _normalize_text

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5


def load_ground_truth() -> List[Dict[str, Any]]:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                cv.Id,
                cv.Pregunta,
                cv.Respuesta,
                cv.Fuentes,
                cv.Contexto,
                i.Ruta
            FROM dbo.ConocimientoValidado cv
            LEFT JOIN dbo.InteraccionesRAG i ON i.Id = cv.InteraccionId
            ORDER BY cv.Id
            """
        )
        rows = cursor.fetchall()

    entries = []
    for row in rows:
        fuentes_raw = row[3] or "[]"
        try:
            fuentes = json.loads(fuentes_raw) if fuentes_raw.strip().startswith("[") else [fuentes_raw]
        except (json.JSONDecodeError, TypeError):
            fuentes = [fuentes_raw] if fuentes_raw else []

        entries.append({
            "id": row[0],
            "question": row[1] or "",
            "answer": row[2] or "",
            "sources": [str(s) for s in fuentes if s],
            "context": row[4] or "",
            "route": row[5] or "",
        })
    return entries


def _normalize_source(source: str) -> str:
    normalized = _normalize_text(source)
    for prefix in ("documentos/", "data/documentos/", "documentos\\", "data\\documentos\\"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized.split("(")[0].strip()


def _sources_overlap(expected: List[str], retrieved: List[str]) -> int:
    if not expected:
        return 0
    expected_norm = {_normalize_source(s) for s in expected if s}
    if not expected_norm:
        return 0
    hits = 0
    for src in retrieved:
        src_norm = _normalize_source(src)
        if any(exp in src_norm or src_norm in exp for exp in expected_norm):
            hits += 1
    return min(hits, len(expected_norm))


def recall_at_k(expected_sources: List[str], retrieved_sources: List[str]) -> float:
    if not expected_sources:
        return 1.0
    overlap = _sources_overlap(expected_sources, retrieved_sources)
    return overlap / len({_normalize_source(s) for s in expected_sources if s}) if expected_sources else 1.0


def precision_at_k(expected_sources: List[str], retrieved_sources: List[str]) -> float:
    if not retrieved_sources:
        return 0.0
    if not expected_sources:
        return 0.0
    overlap = _sources_overlap(expected_sources, retrieved_sources)
    return overlap / len(retrieved_sources)


def domain_match_ratio(question: str, retrieved_sources: List[str], stats: Dict[str, Any]) -> float:
    expected = _expected_domains(question)
    if not expected:
        return 1.0
    matched = stats.get("domain_match_ratio", 0.0)
    if isinstance(matched, (int, float)):
        return float(matched)
    return 0.0


def evaluate_single(
    question: str,
    expected_sources: List[str],
    top_k: int = DEFAULT_TOP_K,
) -> Dict[str, Any]:
    context, sources, stats = search_documents_detailed(question, n_results=top_k)
    return {
        "question": question,
        "expected_sources": expected_sources,
        "retrieved_sources": sources,
        "recall": recall_at_k(expected_sources, sources),
        "precision": precision_at_k(expected_sources, sources),
        "domain_match": domain_match_ratio(question, sources, stats),
        "selected_count": stats.get("selected_count", 0),
        "expected_domains": stats.get("expected_domains", []),
    }


def evaluate_all(
    ground_truth: List[Dict[str, Any]],
    top_k: int = DEFAULT_TOP_K,
) -> Dict[str, Any]:
    results = []
    for entry in ground_truth:
        if not entry["question"].strip():
            continue
        try:
            result = evaluate_single(entry["question"], entry["sources"], top_k=top_k)
            result["id"] = entry["id"]
            result["route"] = entry.get("route", "")
            results.append(result)
        except Exception:
            logger.exception("Error evaluando pregunta id=%s", entry.get("id"))
            results.append({
                "id": entry.get("id"),
                "question": entry["question"],
                "error": True,
                "recall": 0.0,
                "precision": 0.0,
                "domain_match": 0.0,
            })

    if not results:
        return {"count": 0, "avg_recall": 0.0, "avg_precision": 0.0, "avg_domain_match": 0.0, "results": []}

    valid = [r for r in results if not r.get("error")]
    avg_recall = sum(r["recall"] for r in valid) / len(valid) if valid else 0.0
    avg_precision = sum(r["precision"] for r in valid) / len(valid) if valid else 0.0
    avg_domain = sum(r["domain_match"] for r in valid) / len(valid) if valid else 0.0

    return {
        "count": len(results),
        "errors": len(results) - len(valid),
        "top_k": top_k,
        "avg_recall": round(avg_recall, 4),
        "avg_precision": round(avg_precision, 4),
        "avg_domain_match": round(avg_domain, 4),
        "results": results,
    }


def compare_with_baseline(
    current: Dict[str, Any],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    deltas = {}
    for metric in ("avg_recall", "avg_precision", "avg_domain_match"):
        cur = current.get(metric, 0.0)
        base = baseline.get(metric, 0.0)
        deltas[metric] = round(cur - base, 4)

    regressions = []
    baseline_by_id = {r["id"]: r for r in baseline.get("results", []) if "id" in r}
    for result in current.get("results", []):
        rid = result.get("id")
        if rid and rid in baseline_by_id:
            base_recall = baseline_by_id[rid].get("recall", 0.0)
            if result.get("recall", 0.0) < base_recall - 0.01:
                regressions.append({
                    "id": rid,
                    "question": result.get("question", ""),
                    "recall_before": base_recall,
                    "recall_after": result.get("recall", 0.0),
                })

    return {
        "deltas": deltas,
        "regressions": regressions,
        "improved": all(v >= 0 for v in deltas.values()),
    }


def print_report(report: Dict[str, Any], baseline_comparison: Optional[Dict[str, Any]] = None) -> None:
    print(f"\n{'='*60}")
    print(f"  EVALUACION DE RETRIEVAL — {report['count']} preguntas, top_k={report['top_k']}")
    print(f"{'='*60}")
    print(f"  Recall@{report['top_k']}:        {report['avg_recall']:.4f}")
    print(f"  Precision@{report['top_k']}:     {report['avg_precision']:.4f}")
    print(f"  Domain match:    {report['avg_domain_match']:.4f}")
    if report.get("errors"):
        print(f"  Errores:         {report['errors']}")

    if baseline_comparison:
        print("\n  --- Delta vs baseline ---")
        for metric, delta in baseline_comparison["deltas"].items():
            sign = "+" if delta >= 0 else ""
            print(f"  {metric}: {sign}{delta:.4f}")
        if baseline_comparison["regressions"]:
            print(f"\n  REGRESIONES ({len(baseline_comparison['regressions'])}):")
            for reg in baseline_comparison["regressions"]:
                print(f"    id={reg['id']}: recall {reg['recall_before']:.2f} -> {reg['recall_after']:.2f}  |  {reg['question'][:60]}")
        else:
            print("\n  Sin regresiones.")

    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluador offline de retrieval RAG")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--baseline", type=str, default=None, help="JSON con resultados anteriores para comparar")
    parser.add_argument("--save", type=str, default=None, help="Guardar resultados en JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    print("Cargando ground truth de ConocimientoValidado...")
    ground_truth = load_ground_truth()
    if not ground_truth:
        print("No hay preguntas validadas en ConocimientoValidado. Valida algunas respuestas primero.")
        sys.exit(0)

    print(f"Evaluando {len(ground_truth)} preguntas con top_k={args.top_k}...")
    report = evaluate_all(ground_truth, top_k=args.top_k)

    baseline_comparison = None
    if args.baseline:
        baseline_path = Path(args.baseline)
        if baseline_path.exists():
            with open(baseline_path, "r", encoding="utf-8") as f:
                baseline = json.load(f)
            baseline_comparison = compare_with_baseline(report, baseline)

    print_report(report, baseline_comparison)

    if args.save:
        save_path = Path(args.save)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"Resultados guardados en {save_path}")


if __name__ == "__main__":
    main()
