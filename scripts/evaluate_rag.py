"""Evaluador de calidad RAG con umbrales mínimos.

Ejecutar antes de un PR o tras cambios en dominios, chunking o scoring:

    cd src/backend
    python ../../scripts/evaluate_rag.py [--top-k 3] [--baseline baseline.json] [--save baseline.json]

Sale con código 1 si las métricas están por debajo de los umbrales mínimos.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Los umbrales se calibran en la primera ejecución y se ajustan si el corpus cambia.
# Valores conservadores iniciales — actualizar una vez se tenga baseline real.
MIN_RECALL = 0.40
MIN_PRECISION = 0.25

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "backend"))

from rag_evaluator import compare_with_baseline, evaluate_all, load_ground_truth, print_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluación de calidad RAG con umbrales mínimos")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--baseline", type=str, default=None)
    parser.add_argument("--save", type=str, default=None)
    args = parser.parse_args()

    import logging
    logging.basicConfig(level=logging.WARNING)

    print("Cargando ground truth de ConocimientoValidado...")
    ground_truth = load_ground_truth()
    if not ground_truth:
        print("AVISO: No hay preguntas validadas. Valida algunas respuestas antes de evaluar.")
        sys.exit(0)

    print(f"Evaluando {len(ground_truth)} preguntas con top_k={args.top_k}...")
    report = evaluate_all(ground_truth, top_k=args.top_k)

    baseline_comparison = None
    if args.baseline:
        baseline_path = Path(args.baseline)
        if baseline_path.exists():
            with open(baseline_path, encoding="utf-8") as f:
                baseline = json.load(f)
            baseline_comparison = compare_with_baseline(report, baseline)

    print_report(report, baseline_comparison)

    if args.save:
        save_path = Path(args.save)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"Resultados guardados en {save_path}")

    failed = False
    if report["avg_recall"] < MIN_RECALL:
        print(f"FALLO: avg_recall={report['avg_recall']:.4f} < umbral={MIN_RECALL}")
        failed = True
    if report["avg_precision"] < MIN_PRECISION:
        print(f"FALLO: avg_precision={report['avg_precision']:.4f} < umbral={MIN_PRECISION}")
        failed = True
    if baseline_comparison and baseline_comparison["regressions"]:
        print(f"FALLO: {len(baseline_comparison['regressions'])} regresión(es) respecto al baseline")
        failed = True

    if failed:
        sys.exit(1)
    print("OK — métricas dentro de umbrales.")


if __name__ == "__main__":
    main()
