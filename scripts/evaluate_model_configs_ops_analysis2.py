import argparse
import html
import json
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "src" / "backend"
TMP_DIR = ROOT / "tmp-logs"
DATASET_PATH = Path(__file__).with_name("eval_battery_ops_analysis2.json")
PYTHON_EXE = Path(
    r"C:\Users\jcanete\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)
DESKTOP_HTML = Path(r"C:\Users\jcanete\Desktop\Comparativa_Modelos_Chatbot_OPS_Analisis_2.html")
JSON_OUTPUT = TMP_DIR / "comparativa_modelos_chatbot_ops_analisis_2.json"
CHROMA_PATH = TMP_DIR / "chroma_ops_analysis2"
LOCAL_SERVER_ROOT = Path(r"C:\Users\jcanete\REGENERA\Regenera Ficheros - Servidor")

USER_HEADERS = {
    "x-user-id": "3",
    "x-user-name": "ops_local_test",
    "x-user-email": "ops_local_test@regeneraenergy.es",
    "x-auth-provider": "local",
}

MODEL_PRICING_USD_PER_MILLION = {
    "gpt-5.4-nano": {"input": 0.10, "output": 0.40, "source": "proyecto"},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "source": "proyecto"},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "source": "https://openai.com/api/pricing/ (2026-06-17)"},
    "gpt-5.4": {"input": 2.50, "output": 15.00, "source": "https://openai.com/api/pricing/ (2026-06-17)"},
    "appregenera_sql": {"input": 0.0, "output": 0.0, "source": "sin coste LLM"},
    "appregenera_http": {"input": 0.0, "output": 0.0, "source": "sin coste LLM"},
    "appregenera": {"input": 0.0, "output": 0.0, "source": "sin coste LLM"},
}


def read_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    raise RuntimeError(f"Define la variable de entorno requerida: {name}")


def normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^\w\s./:-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def ensure_documents_path() -> Path:
    info_dirs = [path for path in LOCAL_SERVER_ROOT.iterdir() if path.is_dir() and path.name.startswith("13 Inform")]
    if not info_dirs:
        raise RuntimeError(f"No se ha encontrado la carpeta '13 Inform*' dentro de {LOCAL_SERVER_ROOT}")
    return info_dirs[0] / "07_Plan Digitalizacion" / "Documentacion_Aplicaciones" / "Chatbot"


def read_openai_api_key() -> str:
    value = os.getenv("OPENAI_API_KEY", "").strip()
    if value:
        return value
    start_script = ROOT / "tmp-logs" / "start_backend_local.ps1"
    if start_script.exists():
        content = start_script.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"\$env:OPENAI_API_KEY='([^']+)'", content)
        if match:
            return match.group(1)
    raise RuntimeError("No se ha encontrado OPENAI_API_KEY en el entorno ni en tmp-logs/start_backend_local.ps1")


def load_battery() -> List[Dict[str, Any]]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _extract_default(pattern: str, text: str, fallback: str = "") -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    return match.group(1).strip() if match else fallback


def detect_current_models() -> Dict[str, Any]:
    config_text = (BACKEND_DIR / "config.py").read_text(encoding="utf-8")
    workflow_text = (ROOT / ".github" / "workflows" / "deploy-chatbot.yml").read_text(encoding="utf-8")

    runtime_defaults = {
        "base_model": _extract_default(r'OPENAI_MODEL = os\.getenv\("OPENAI_MODEL", "([^"]+)"\)', config_text),
        "secondary_model": _extract_default(r'or "([^"]+)"\n\)\.strip\(\)', config_text, "gpt-4.1-mini"),
        "fallback_model": "",
        "baseline_model": _extract_default(r'OPENAI_BASELINE_MODEL = \(os\.getenv\("OPENAI_BASELINE_MODEL", "([^"]+)"\)\)', config_text),
    }
    runtime_defaults["fallback_model"] = runtime_defaults["secondary_model"]

    workflow_env = {
        "base_model": _extract_default(r"OPENAI_MODEL:\s*([^\r\n]+)", workflow_text),
        "baseline_model": _extract_default(r"OPENAI_BASELINE_MODEL:\s*([^\r\n]+)", workflow_text),
        "secondary_model": _extract_default(r"LLM_SECONDARY_MODEL:\s*([^\r\n]+)", workflow_text),
        "fallback_model": _extract_default(r"LLM_FALLBACK_MODEL:\s*([^\r\n]+)", workflow_text),
    }

    effective = workflow_env if workflow_env["base_model"] else runtime_defaults
    return {
        "runtime_defaults": runtime_defaults,
        "workflow_env": workflow_env,
        "effective_current": effective,
        "matches_between_code_and_workflow": runtime_defaults == workflow_env if workflow_env["base_model"] else False,
    }


def build_backend_env(config: Dict[str, str], port: int) -> Dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "OPENAI_API_KEY": read_openai_api_key(),
            "OPENAI_MODEL": config["base_model"],
            "OPENAI_BASELINE_MODEL": config["baseline_model"],
            "LLM_SECONDARY_MODEL": config["secondary_model"],
            "LLM_FALLBACK_MODEL": config["fallback_model"],
            "SQL_SERVER": r"(localdb)\MSSQLLocalDB",
            "SQL_DATABASE": "ChatBot",
            "SQL_ENCRYPT": "no",
            "SQL_TRUST_SERVER_CERTIFICATE": "yes",
            "ALLOW_LOCAL_SQL_FALLBACK": "0",
            "RUN_SCHEMA_MIGRATIONS": "1",
            "ENTRA_ENABLED": "0",
            "ALLOWED_ORIGINS": f"http://127.0.0.1:{port},http://localhost:{port}",
            "DOCUMENTS_PATH": str(ensure_documents_path()),
            "CHROMA_DB_PATH": str(CHROMA_PATH),
            "SYNC_DOCUMENTS_ON_STARTUP": "false",
            "RAG_BACKEND": "chroma",
            "APPREGENERA_SQL_CONNECTION_STRING": read_required_env("APPREGENERA_SQL_CONNECTION_STRING"),
            "APPREGENERA_ALLOWED_MODULES": "estudios,produccion",
            "APPREGENERA_DEV_BYPASS_KEY": read_required_env("APPREGENERA_DEV_BYPASS_KEY"),
            "LOG_LEVEL": "INFO",
            "WEBSITE_PORT": str(port),
        }
    )
    return env


def prepare_index(base_config: Dict[str, str]) -> Dict[str, Any]:
    if CHROMA_PATH.exists():
        return {"reused": True, "path": str(CHROMA_PATH)}
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    env = build_backend_env(base_config, 8099)
    code = (
        "from load_docs import load_documents\n"
        "total = load_documents(reset=True)\n"
        "print(total)\n"
    )
    result = subprocess.run(
        [str(PYTHON_EXE), "-c", code],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Fallo indexando documentos:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return {
        "reused": False,
        "path": str(CHROMA_PATH),
        "stdout_tail": result.stdout.strip()[-800:],
    }


def start_backend(config: Dict[str, str], port: int) -> subprocess.Popen:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = TMP_DIR / f"ops-analysis2-{config['name']}-stdout.log"
    stderr_path = TMP_DIR / f"ops-analysis2-{config['name']}-stderr.log"
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PYTHON_EXE), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND_DIR),
        env=build_backend_env(config, port),
        stdout=stdout_file,
        stderr=stderr_file,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    process._codex_stdout_file = stdout_file  # type: ignore[attr-defined]
    process._codex_stderr_file = stderr_file  # type: ignore[attr-defined]
    return process


def stop_backend(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
        except Exception:
            process.kill()
            process.wait(timeout=10)
    for attr in ("_codex_stdout_file", "_codex_stderr_file"):
        handle = getattr(process, attr, None)
        if handle:
            handle.close()


def wait_ready(base_url: str, timeout_secs: int = 90) -> None:
    deadline = time.time() + timeout_secs
    last_error = ""
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=5)
            if response.status_code == 200:
                return
            last_error = f"status {response.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"El backend no ha quedado listo a tiempo: {last_error}")


def create_conversation(base_url: str, title: str, mode: str) -> int:
    for _ in range(15):
        response = requests.post(
            f"{base_url}/conversations",
            json={"user_id": 3, "title": title, "chat_mode": mode},
            headers=USER_HEADERS,
            timeout=30,
        )
        if response.status_code == 503:
            time.sleep(2)
            continue
        response.raise_for_status()
        return int(response.json()["conversation_id"])
    raise RuntimeError(f"No se pudo crear la conversación para el modo {mode}")


def ask_question(base_url: str, conversation_id: int, question: str) -> Dict[str, Any]:
    for _ in range(5):
        response = requests.post(
            f"{base_url}/messages",
            json={"conversation_id": conversation_id, "question": question},
            headers=USER_HEADERS,
            timeout=300,
        )
        if response.status_code == 503:
            time.sleep(2)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"No se ha podido obtener respuesta para la pregunta: {question}")


def extract_source_label(source: Any) -> str:
    if isinstance(source, dict):
        module = str(source.get("module") or "").strip()
        label = str(source.get("source") or "").strip()
        return f"{label}|{module}" if module else label
    return str(source or "").strip()


def extract_source_prefix(source: Any) -> str:
    label = extract_source_label(source)
    return re.sub(r"\s+\(pag\..*$", "", label, flags=re.IGNORECASE).strip()


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = MODEL_PRICING_USD_PER_MILLION.get((model or "").strip().lower())
    if not pricing:
        return 0.0
    return (prompt_tokens / 1_000_000) * pricing["input"] + (completion_tokens / 1_000_000) * pricing["output"]


def _match_terms(text: str, terms: List[str]) -> Dict[str, Any]:
    normalized_text = normalize(text)
    matched = [term for term in terms if normalize(term) in normalized_text]
    return {
        "matched": matched,
        "missing": [term for term in terms if term not in matched],
        "ratio": (len(matched) / len(terms)) if terms else 1.0,
    }


def _match_patterns(text: str, patterns: List[str]) -> Dict[str, Any]:
    matched = [pattern for pattern in patterns if re.search(pattern, text or "", flags=re.IGNORECASE)]
    return {
        "matched": matched,
        "missing": [pattern for pattern in patterns if pattern not in matched],
        "ratio": (len(matched) / len(patterns)) if patterns else 1.0,
    }


def score_item(item: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    route = str(payload.get("route") or "")
    response_text = str(payload.get("response") or "")
    sources = payload.get("sources") or []
    source_prefixes = [extract_source_prefix(source) for source in sources]
    trace = payload.get("trace") or {}
    business_path = str(trace.get("path") or "").strip().lower()
    business_module = str(trace.get("module") or "").strip().lower()

    route_score = 25 if route == item.get("expected_route") else 0

    source_score = 0.0
    if item["mode"] == "technical" and item.get("expected_source_prefixes"):
        if any(
            actual.startswith(expected)
            for actual in source_prefixes
            for expected in item.get("expected_source_prefixes", [])
        ):
            source_score = 25.0
        elif source_prefixes:
            source_score = 8.0
    elif item["mode"] == "business" and item.get("expected_business_module"):
        module_ok = business_module == item.get("expected_business_module", "").lower()
        path_ok = business_path == item.get("expected_business_path", "").lower() if item.get("expected_business_path") else True
        if module_ok and path_ok:
            source_score = 25.0
        elif module_ok or path_ok:
            source_score = 12.5
    elif item.get("expected_route") in {"business_scope_mismatch", "technical_scope_mismatch", "mixed_scope"}:
        source_score = 25.0

    term_match = _match_terms(response_text, item.get("expected_terms", []))
    pattern_match = _match_patterns(response_text, item.get("expected_patterns", []))

    content_weights = []
    if item.get("expected_terms"):
        content_weights.append(term_match["ratio"])
    if item.get("expected_patterns"):
        content_weights.append(pattern_match["ratio"])
    content_ratio = sum(content_weights) / len(content_weights) if content_weights else 1.0
    content_score = round(content_ratio * 50.0, 2)

    total_score = round(route_score + source_score + content_score, 2)
    if total_score >= 85:
        verdict = "correcta"
    elif total_score >= 60:
        verdict = "parcial"
    else:
        verdict = "incorrecta"

    expected_doc = "N/A"
    if item["mode"] == "technical" and item.get("expected_source_prefixes"):
        expected_doc = " | ".join(item["expected_source_prefixes"])
    elif item["mode"] == "business":
        expected_doc = "N/A"

    return {
        "expected_doc": expected_doc,
        "route_score": route_score,
        "source_score": source_score,
        "content_score": content_score,
        "total_score": total_score,
        "verdict": verdict,
        "matched_terms": term_match["matched"],
        "missing_terms": term_match["missing"],
        "matched_patterns": pattern_match["matched"],
        "missing_patterns": pattern_match["missing"],
        "source_prefixes": source_prefixes,
        "business_path": business_path,
        "business_module": business_module,
    }


def summarize_rows(config: Dict[str, str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    overall_score = round(sum(row["score"]["total_score"] for row in rows) / len(rows), 2)
    overall_conf = round(sum(float(row.get("confidence") or 0.0) for row in rows) / len(rows), 4)
    by_part: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_mode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_difficulty: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    final_model_counts = Counter()
    base_model_counts = Counter()
    model_costs = defaultdict(float)
    model_tokens = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    escalated_rows = 0
    technical_rows = 0
    technical_source_misses = 0
    high_conf_incorrect = []

    for row in rows:
        item = row["item"]
        part = item.get("part") or item["mode"]
        by_part[part].append(row)
        by_mode[item["mode"]].append(row)
        by_difficulty[item.get("difficulty", "unknown")].append(row)
        score = row["score"]
        if item["mode"] == "technical":
            technical_rows += 1
            if item.get("expected_source_prefixes") and not any(
                actual.startswith(expected)
                for actual in score.get("source_prefixes", [])
                for expected in item.get("expected_source_prefixes", [])
            ):
                technical_source_misses += 1
        if score["verdict"] == "incorrecta" and float(row.get("confidence") or 0.0) >= 0.75:
            high_conf_incorrect.append({"id": item["id"], "question": item["question"], "confidence": row.get("confidence")})

        trace = row.get("trace") or {}
        base_model = str(trace.get("base_model") or "")
        final_model = str(trace.get("final_model") or "")
        if not final_model and item["mode"] == "business":
            final_model = "appregenera_sql" if score["business_path"] == "sql" else ("appregenera_http" if score["business_path"] == "http" else "appregenera")
            base_model = final_model
        usage_breakdown = trace.get("usage_breakdown") or {}
        base_usage = usage_breakdown.get("base") or {}
        final_usage = usage_breakdown.get("final") or {}
        if bool(trace.get("escalated", False)):
            escalated_rows += 1
        if base_model:
            base_model_counts[base_model] += 1
            model_costs[base_model] += estimate_cost(
                base_model,
                int(base_usage.get("prompt_tokens", 0) or 0),
                int(base_usage.get("completion_tokens", 0) or 0),
            )
            model_tokens[base_model]["prompt_tokens"] += int(base_usage.get("prompt_tokens", 0) or 0)
            model_tokens[base_model]["completion_tokens"] += int(base_usage.get("completion_tokens", 0) or 0)
            model_tokens[base_model]["total_tokens"] += int(base_usage.get("total_tokens", 0) or 0)
        if final_model:
            final_model_counts[final_model] += 1
            if final_model != base_model or not base_usage:
                model_costs[final_model] += estimate_cost(
                    final_model,
                    int(final_usage.get("prompt_tokens", 0) or 0),
                    int(final_usage.get("completion_tokens", 0) or 0),
                )
                model_tokens[final_model]["prompt_tokens"] += int(final_usage.get("prompt_tokens", 0) or 0)
                model_tokens[final_model]["completion_tokens"] += int(final_usage.get("completion_tokens", 0) or 0)
                model_tokens[final_model]["total_tokens"] += int(final_usage.get("total_tokens", 0) or 0)

    def summarize_bucket(bucket_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(bucket_rows)
        verdicts = Counter(row["score"]["verdict"] for row in bucket_rows)
        return {
            "questions": total,
            "score_0_100": round(sum(row["score"]["total_score"] for row in bucket_rows) / total, 2),
            "avg_confidence": round(sum(float(row.get("confidence") or 0.0) for row in bucket_rows) / total, 4),
            "correctas": verdicts.get("correcta", 0),
            "parciales": verdicts.get("parcial", 0),
            "incorrectas": verdicts.get("incorrecta", 0),
        }

    cost_breakdown = {
        model: {
            "estimated_cost_usd": round(model_costs[model], 6),
            "prompt_tokens": model_tokens[model]["prompt_tokens"],
            "completion_tokens": model_tokens[model]["completion_tokens"],
            "total_tokens": model_tokens[model]["total_tokens"],
            "pricing_source": MODEL_PRICING_USD_PER_MILLION.get(model.lower(), {}).get("source", ""),
        }
        for model in sorted(model_costs)
    }

    base_model = config["base_model"]
    secondary_model = config["secondary_model"]
    base_only_questions = sum(
        1
        for row in rows
        if (row.get("trace") or {}).get("final_model") == base_model and not bool((row.get("trace") or {}).get("escalated", False))
    )

    return {
        "config_name": config["name"],
        "config_label": config["label"],
        "base_model": base_model,
        "secondary_model": secondary_model,
        "overall_score_0_100": overall_score,
        "overall_avg_confidence": overall_conf,
        "breakdown_by_mode": {key: summarize_bucket(value) for key, value in sorted(by_mode.items())},
        "breakdown_by_part": {key: summarize_bucket(value) for key, value in sorted(by_part.items())},
        "breakdown_by_difficulty": {key: summarize_bucket(value) for key, value in sorted(by_difficulty.items())},
        "total_estimated_cost_usd": round(sum(model_costs.values()), 6),
        "cost_breakdown": cost_breakdown,
        "base_model_share": {
            model: {"count": count, "share_pct": round((count / len(rows)) * 100, 2)}
            for model, count in base_model_counts.items()
        },
        "final_model_share": {
            model: {"count": count, "share_pct": round((count / len(rows)) * 100, 2)}
            for model, count in final_model_counts.items()
        },
        "base_only_questions_pct": round((base_only_questions / len(rows)) * 100, 2),
        "escalated_questions_pct": round((escalated_rows / len(rows)) * 100, 2),
        "technical_escalated_questions_pct": round((escalated_rows / technical_rows) * 100, 2) if technical_rows else 0.0,
        "technical_wrong_top_source_pct": round((technical_source_misses / technical_rows) * 100, 2) if technical_rows else 0.0,
        "high_confidence_incorrect": high_conf_incorrect[:12],
    }


def run_config(config: Dict[str, str], port: int, battery: List[Dict[str, Any]]) -> Dict[str, Any]:
    base_url = f"http://127.0.0.1:{port}"
    process = start_backend(config, port)
    try:
        wait_ready(base_url)
        conversations = {
            "business": create_conversation(base_url, f"ops analysis2 negocio {config['name']}", "business"),
            "technical": create_conversation(base_url, f"ops analysis2 tecnico {config['name']}", "technical"),
        }
        rows = []
        for idx, item in enumerate(battery, start=1):
            payload = ask_question(base_url, conversations[item["mode"]], item["question"])
            score = score_item(item, payload)
            rows.append(
                {
                    "index": idx,
                    "item": item,
                    "response": payload.get("response"),
                    "route": payload.get("route"),
                    "confidence": payload.get("confidence"),
                    "sources": payload.get("sources") or [],
                    "trace": payload.get("trace") or {},
                    "interaction_id": payload.get("interaction_id"),
                    "score": score,
                }
            )
        return {"summary": summarize_rows(config, rows), "rows": rows}
    finally:
        stop_backend(process)


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def fmt_usd(value: float) -> str:
    return f"${value:,.6f}"


def _primary_source(row: Dict[str, Any]) -> str:
    sources = row.get("sources") or []
    return extract_source_prefix(sources[0]) if sources else ""


def generate_html(results: Dict[str, Any], output_path: Path) -> None:
    cfg_names = list(results["runs"].keys())
    left = results["runs"][cfg_names[0]]
    right = results["runs"][cfg_names[1]]
    left_sum = left["summary"]
    right_sum = right["summary"]

    def part_score(summary: Dict[str, Any], key: str) -> str:
        bucket = summary["breakdown_by_part"].get(key) or {"score_0_100": 0, "avg_confidence": 0, "correctas": 0, "parciales": 0, "incorrectas": 0, "questions": 0}
        return f"{bucket['score_0_100']:.2f} / conf {bucket['avg_confidence']:.3f} / C-P-I {bucket['correctas']}-{bucket['parciales']}-{bucket['incorrectas']}"

    def part_bucket(summary: Dict[str, Any], key: str) -> Dict[str, Any]:
        return summary["breakdown_by_part"].get(key) or {"score_0_100": 0, "avg_confidence": 0, "correctas": 0, "parciales": 0, "incorrectas": 0, "questions": 0}

    def mode_bucket(summary: Dict[str, Any], key: str) -> Dict[str, Any]:
        return summary["breakdown_by_mode"].get(key) or {"score_0_100": 0, "avg_confidence": 0, "correctas": 0, "parciales": 0, "incorrectas": 0, "questions": 0}

    anomaly_lines = []
    for name, run in results["runs"].items():
        summary = run["summary"]
        anomaly_lines.append(
            {
                "config": summary["config_label"],
                "high_conf_bad": summary["high_confidence_incorrect"],
                "wrong_source_pct": summary["technical_wrong_top_source_pct"],
            }
        )

    rows_html = []
    left_rows_by_id = {row["item"]["id"]: row for row in left["rows"]}
    right_rows_by_id = {row["item"]["id"]: row for row in right["rows"]}
    for item in results["battery"]:
        lrow = left_rows_by_id[item["id"]]
        rrow = right_rows_by_id[item["id"]]
        rows_html.append(
            "<tr>"
            f"<td>{lrow['index']}</td>"
            f"<td>{html.escape(item['question'])}</td>"
            f"<td>{html.escape(item['part'])}</td>"
            f"<td>{html.escape(lrow['score']['expected_doc'])}</td>"
            f"<td>{html.escape(lrow['score']['verdict'])}</td>"
            f"<td>{lrow['score']['total_score']:.2f}</td>"
            f"<td>{float(lrow.get('confidence') or 0.0):.3f}</td>"
            f"<td>{html.escape(str(lrow.get('route') or ''))}</td>"
            f"<td>{html.escape(str((lrow.get('trace') or {}).get('final_model') or (lrow['score']['business_path'] and ('appregenera_' + lrow['score']['business_path'])) or ''))}</td>"
            f"<td>{html.escape(_primary_source(lrow))}</td>"
            f"<td>{html.escape(str(lrow.get('response') or ''))}</td>"
            f"<td>{html.escape(', '.join(lrow['score']['missing_terms'][:4]) or str((lrow.get('trace') or {}).get('escalation_reason') or ''))}</td>"
            f"<td>{html.escape(rrow['score']['verdict'])}</td>"
            f"<td>{rrow['score']['total_score']:.2f}</td>"
            f"<td>{float(rrow.get('confidence') or 0.0):.3f}</td>"
            f"<td>{html.escape(str(rrow.get('route') or ''))}</td>"
            f"<td>{html.escape(str((rrow.get('trace') or {}).get('final_model') or (rrow['score']['business_path'] and ('appregenera_' + rrow['score']['business_path'])) or ''))}</td>"
            f"<td>{html.escape(_primary_source(rrow))}</td>"
            f"<td>{html.escape(str(rrow.get('response') or ''))}</td>"
            f"<td>{html.escape(', '.join(rrow['score']['missing_terms'][:4]) or str((rrow.get('trace') or {}).get('escalation_reason') or ''))}</td>"
            "</tr>"
        )

    def render_cost_table(summary: Dict[str, Any]) -> str:
        lines = []
        for model, data in summary["cost_breakdown"].items():
            lines.append(
                "<tr>"
                f"<td>{html.escape(model)}</td>"
                f"<td>{fmt_usd(data['estimated_cost_usd'])}</td>"
                f"<td>{data['prompt_tokens']}</td>"
                f"<td>{data['completion_tokens']}</td>"
                f"<td>{data['total_tokens']}</td>"
                f"<td>{html.escape(data['pricing_source'])}</td>"
                "</tr>"
            )
        return "".join(lines)

    html_text = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Comparativa modelos ChatBot OPS análisis 2</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --panel: #fffdf8;
      --ink: #1f2933;
      --muted: #5b6572;
      --line: #d8cbb8;
      --ok: #2d6a4f;
      --warn: #b76e00;
      --bad: #a33a3a;
      --accent: #244b7a;
    }}
    body {{ font-family: Georgia, 'Segoe UI', serif; background: linear-gradient(180deg, #efe7d9 0%, #f7f4ee 100%); color: var(--ink); margin: 0; }}
    .wrap {{ max-width: 1520px; margin: 0 auto; padding: 28px; }}
    h1, h2, h3 {{ margin: 0 0 12px; }}
    h1 {{ font-size: 34px; }}
    h2 {{ font-size: 22px; margin-top: 28px; }}
    p, li {{ line-height: 1.45; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 16px; }}
    .card, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 18px; box-shadow: 0 10px 24px rgba(75, 57, 33, 0.06); }}
    .metric {{ font-size: 28px; font-weight: 700; color: var(--accent); }}
    .muted {{ color: var(--muted); font-size: 14px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 16px; overflow: hidden; }}
    th, td {{ border: 1px solid var(--line); padding: 9px 10px; vertical-align: top; font-size: 13px; }}
    th {{ background: #eadfcf; position: sticky; top: 0; }}
    .ok {{ color: var(--ok); font-weight: 700; }}
    .warn {{ color: var(--warn); font-weight: 700; }}
    .bad {{ color: var(--bad); font-weight: 700; }}
    .kicker {{ text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-size: 12px; }}
    .mono {{ font-family: Consolas, monospace; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <div class="kicker">Análisis local independiente</div>
      <h1>Comparativa de modelos del ChatBot OPS</h1>
      <p>Generado el {html.escape(results['generated_at'])}. Batería nueva de {len(results['battery'])} preguntas: 10 de negocio y 40 de técnico/documentación, incluyendo follow-ups, ambigüedad, límites y aislamiento entre dominios.</p>
      <p><strong>Configuración real detectada en código:</strong> base/L1 <span class="mono">{html.escape(results['current_models']['effective_current']['base_model'])}</span>, secundario/L2 <span class="mono">{html.escape(results['current_models']['effective_current']['secondary_model'])}</span>. Coincidencia config.py/workflow: <strong>{'sí' if results['current_models']['matches_between_code_and_workflow'] else 'no'}</strong>.</p>
    </div>

    <div class="cards">
      <div class="card"><div class="kicker">Mejor calidad</div><div class="metric">{html.escape(results['executive']['best_quality'])}</div><div class="muted">Mayor score global 0-100.</div></div>
      <div class="card"><div class="kicker">Mejor coste/rendimiento</div><div class="metric">{html.escape(results['executive']['best_efficiency'])}</div><div class="muted">Score global dividido por coste total estimado.</div></div>
      <div class="card"><div class="kicker">Índice reutilizado</div><div class="metric">{'Sí' if results['index_info'].get('reused') else 'No'}</div><div class="muted mono">{html.escape(results['index_info']['path'])}</div></div>
      <div class="card"><div class="kicker">Coste batería</div><div class="metric">{fmt_usd(left_sum['total_estimated_cost_usd'])} / {fmt_usd(right_sum['total_estimated_cost_usd'])}</div><div class="muted">Config 1 / Config 2</div></div>
    </div>

    <h2>Resumen ejecutivo</h2>
    <div class="grid">
      <div class="panel">
        <h3>Configuración 1</h3>
        <p><strong>{html.escape(left_sum['config_label'])}</strong></p>
        <p>Score global: <strong>{left_sum['overall_score_0_100']:.2f}</strong> | Confianza media: <strong>{left_sum['overall_avg_confidence']:.3f}</strong> | Coste total: <strong>{fmt_usd(left_sum['total_estimated_cost_usd'])}</strong></p>
        <ul>
          <li>Negocio: {part_score(left_sum, 'business')}</li>
          <li>Negocio aislamiento: {part_score(left_sum, 'business_isolation')}</li>
          <li>Técnico total: {mode_bucket(left_sum, 'technical')['score_0_100']:.2f} / conf {mode_bucket(left_sum, 'technical')['avg_confidence']:.3f}</li>
          <li>Técnico legacy: {part_score(left_sum, 'legacy_technical')}</li>
          <li>OPS: {part_score(left_sum, 'ops_technical')}</li>
          <li>Aislamiento técnico: {part_score(left_sum, 'technical_isolation')}</li>
          <li>Base solo: {fmt_pct(left_sum['base_only_questions_pct'])} | Escaladas: {fmt_pct(left_sum['escalated_questions_pct'])}</li>
        </ul>
      </div>
      <div class="panel">
        <h3>Configuración 2</h3>
        <p><strong>{html.escape(right_sum['config_label'])}</strong></p>
        <p>Score global: <strong>{right_sum['overall_score_0_100']:.2f}</strong> | Confianza media: <strong>{right_sum['overall_avg_confidence']:.3f}</strong> | Coste total: <strong>{fmt_usd(right_sum['total_estimated_cost_usd'])}</strong></p>
        <ul>
          <li>Negocio: {part_score(right_sum, 'business')}</li>
          <li>Negocio aislamiento: {part_score(right_sum, 'business_isolation')}</li>
          <li>Técnico total: {mode_bucket(right_sum, 'technical')['score_0_100']:.2f} / conf {mode_bucket(right_sum, 'technical')['avg_confidence']:.3f}</li>
          <li>Técnico legacy: {part_score(right_sum, 'legacy_technical')}</li>
          <li>OPS: {part_score(right_sum, 'ops_technical')}</li>
          <li>Aislamiento técnico: {part_score(right_sum, 'technical_isolation')}</li>
          <li>Base solo: {fmt_pct(right_sum['base_only_questions_pct'])} | Escaladas: {fmt_pct(right_sum['escalated_questions_pct'])}</li>
        </ul>
      </div>
    </div>

    <h2>Comparativa clara</h2>
    <table>
      <thead>
        <tr>
          <th>Métrica</th>
          <th>Config 1</th>
          <th>Config 2</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>Score global</td><td>{left_sum['overall_score_0_100']:.2f}</td><td>{right_sum['overall_score_0_100']:.2f}</td></tr>
        <tr><td>Negocio</td><td>{part_bucket(left_sum, 'business')['score_0_100']:.2f}</td><td>{part_bucket(right_sum, 'business')['score_0_100']:.2f}</td></tr>
        <tr><td>Técnico total</td><td>{mode_bucket(left_sum, 'technical')['score_0_100']:.2f}</td><td>{mode_bucket(right_sum, 'technical')['score_0_100']:.2f}</td></tr>
        <tr><td>Técnico legacy</td><td>{part_bucket(left_sum, 'legacy_technical')['score_0_100']:.2f}</td><td>{part_bucket(right_sum, 'legacy_technical')['score_0_100']:.2f}</td></tr>
        <tr><td>OPS</td><td>{part_bucket(left_sum, 'ops_technical')['score_0_100']:.2f}</td><td>{part_bucket(right_sum, 'ops_technical')['score_0_100']:.2f}</td></tr>
        <tr><td>Confianza media global</td><td>{left_sum['overall_avg_confidence']:.3f}</td><td>{right_sum['overall_avg_confidence']:.3f}</td></tr>
        <tr><td>Correctas</td><td>{mode_bucket(left_sum, 'business')['correctas'] + mode_bucket(left_sum, 'technical')['correctas']}</td><td>{mode_bucket(right_sum, 'business')['correctas'] + mode_bucket(right_sum, 'technical')['correctas']}</td></tr>
        <tr><td>Parciales</td><td>{mode_bucket(left_sum, 'business')['parciales'] + mode_bucket(left_sum, 'technical')['parciales']}</td><td>{mode_bucket(right_sum, 'business')['parciales'] + mode_bucket(right_sum, 'technical')['parciales']}</td></tr>
        <tr><td>Incorrectas</td><td>{mode_bucket(left_sum, 'business')['incorrectas'] + mode_bucket(left_sum, 'technical')['incorrectas']}</td><td>{mode_bucket(right_sum, 'business')['incorrectas'] + mode_bucket(right_sum, 'technical')['incorrectas']}</td></tr>
        <tr><td>% resueltas por base</td><td>{fmt_pct(left_sum['base_only_questions_pct'])}</td><td>{fmt_pct(right_sum['base_only_questions_pct'])}</td></tr>
        <tr><td>% escaladas</td><td>{fmt_pct(left_sum['escalated_questions_pct'])}</td><td>{fmt_pct(right_sum['escalated_questions_pct'])}</td></tr>
        <tr><td>% técnico con fuente principal incorrecta</td><td>{fmt_pct(left_sum['technical_wrong_top_source_pct'])}</td><td>{fmt_pct(right_sum['technical_wrong_top_source_pct'])}</td></tr>
        <tr><td>Coste total</td><td>{fmt_usd(left_sum['total_estimated_cost_usd'])}</td><td>{fmt_usd(right_sum['total_estimated_cost_usd'])}</td></tr>
      </tbody>
    </table>

    <h2>Costes y tokens</h2>
    <div class="grid">
      <div class="panel">
        <h3>{html.escape(left_sum['config_label'])}</h3>
        <table><thead><tr><th>Modelo</th><th>Coste</th><th>Prompt</th><th>Completion</th><th>Total</th><th>Fuente precio</th></tr></thead><tbody>{render_cost_table(left_sum)}</tbody></table>
      </div>
      <div class="panel">
        <h3>{html.escape(right_sum['config_label'])}</h3>
        <table><thead><tr><th>Modelo</th><th>Coste</th><th>Prompt</th><th>Completion</th><th>Total</th><th>Fuente precio</th></tr></thead><tbody>{render_cost_table(right_sum)}</tbody></table>
      </div>
    </div>

    <h2>Observaciones clave</h2>
    <div class="grid">
      <div class="panel">
        <h3>Anomalías Config 1</h3>
        <p>Fuentes principales erróneas en técnico: <strong>{fmt_pct(left_sum['technical_wrong_top_source_pct'])}</strong></p>
        <p>Incorrectas con confianza alta:</p>
        <ul>{''.join(f"<li>{html.escape(x['id'])}: conf {float(x['confidence']):.3f} — {html.escape(x['question'])}</li>" for x in left_sum['high_confidence_incorrect']) or '<li>Ninguna</li>'}</ul>
      </div>
      <div class="panel">
        <h3>Anomalías Config 2</h3>
        <p>Fuentes principales erróneas en técnico: <strong>{fmt_pct(right_sum['technical_wrong_top_source_pct'])}</strong></p>
        <p>Incorrectas con confianza alta:</p>
        <ul>{''.join(f"<li>{html.escape(x['id'])}: conf {float(x['confidence']):.3f} — {html.escape(x['question'])}</li>" for x in right_sum['high_confidence_incorrect']) or '<li>Ninguna</li>'}</ul>
      </div>
    </div>

    <h2>Tabla completa de preguntas</h2>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>nº</th>
            <th>Pregunta</th>
            <th>Ámbito</th>
            <th>Documento esperado</th>
            <th>Config 1: veredicto</th>
            <th>Config 1: score</th>
            <th>Config 1: confianza</th>
            <th>Config 1: ruta</th>
            <th>Config 1: modelo final</th>
            <th>Config 1: fuente real</th>
            <th>Config 1: respuesta</th>
            <th>Config 1: observaciones</th>
            <th>Config 2: veredicto</th>
            <th>Config 2: score</th>
            <th>Config 2: confianza</th>
            <th>Config 2: ruta</th>
            <th>Config 2: modelo final</th>
            <th>Config 2: fuente real</th>
            <th>Config 2: respuesta</th>
            <th>Config 2: observaciones</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>"""
    output_path.write_text(html_text, encoding="utf-8")


def build_executive(results: Dict[str, Any]) -> Dict[str, str]:
    runs = results["runs"]
    best_quality = max(runs.values(), key=lambda run: run["summary"]["overall_score_0_100"])["summary"]["config_label"]
    best_efficiency = max(
        runs.values(),
        key=lambda run: (
            run["summary"]["overall_score_0_100"] / max(run["summary"]["total_estimated_cost_usd"], 0.000001)
        ),
    )["summary"]["config_label"]
    return {
        "best_quality": best_quality,
        "best_efficiency": best_efficiency,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Comparativa independiente de configuraciones de modelo para ChatBot OPS")
    parser.add_argument("--max-questions", type=int, default=0, help="Limita la batería a las primeras N preguntas")
    parser.add_argument("--port-base", type=int, default=8110)
    parser.add_argument("--json-output", default=str(JSON_OUTPUT))
    parser.add_argument("--html-output", default=str(DESKTOP_HTML))
    args = parser.parse_args()

    current_models = detect_current_models()
    current_effective = current_models["effective_current"]

    configs = [
        {
            "name": "current_real_pair",
            "label": f"Configuración actual real ({current_effective['base_model']} + {current_effective['secondary_model']})",
            "base_model": current_effective["base_model"],
            "baseline_model": current_effective["baseline_model"],
            "secondary_model": current_effective["secondary_model"],
            "fallback_model": current_effective["fallback_model"],
        },
        {
            "name": "candidate_gpt54mini_gpt54",
            "label": "Configuración candidata (gpt-5.4-mini + gpt-5.4)",
            "base_model": "gpt-5.4-mini",
            "baseline_model": "gpt-5.4-mini",
            "secondary_model": "gpt-5.4",
            "fallback_model": "gpt-5.4",
        },
    ]

    battery = load_battery()
    if args.max_questions and args.max_questions > 0:
        battery = battery[: args.max_questions]

    index_info = prepare_index(configs[0])

    results = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "battery": battery,
        "battery_size": len(battery),
        "business_questions": sum(1 for item in battery if item["mode"] == "business"),
        "technical_questions": sum(1 for item in battery if item["mode"] == "technical"),
        "current_models": current_models,
        "index_info": index_info,
        "pricing": MODEL_PRICING_USD_PER_MILLION,
        "runs": {},
    }

    for index, config in enumerate(configs):
        port = args.port_base + index
        print(f"[RUN] {config['label']} en puerto {port}", flush=True)
        results["runs"][config["name"]] = run_config(config, port, battery)

    results["executive"] = build_executive(results)

    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    html_output = Path(args.html_output)
    html_output.parent.mkdir(parents=True, exist_ok=True)
    generate_html(results, html_output)

    print(json.dumps({name: run["summary"] for name, run in results["runs"].items()}, ensure_ascii=False, indent=2))
    print(f"JSON guardado en {json_output}")
    print(f"HTML guardado en {html_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
