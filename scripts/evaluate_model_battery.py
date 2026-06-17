import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "src" / "backend"
TMP_DIR = ROOT / "tmp-logs"
LOCAL_SERVER_ROOT = Path(r"C:\Users\jcanete\REGENERA\Regenera Ficheros - Servidor")
PYTHON_EXE = Path(
    r"C:\Users\jcanete\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)


MODEL_PRICING_USD_PER_MILLION = {
    "gpt-5.4-nano": {"input": 0.10, "output": 0.40, "source": "proyecto"},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "source": "proyecto"},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "source": "OpenAI pricing 2026-06-17"},
    "gpt-5.4": {"input": 2.50, "output": 15.00, "source": "OpenAI pricing 2026-06-17"},
    "appregenera_sql": {"input": 0.0, "output": 0.0, "source": "sin coste LLM"},
    "appregenera_http": {"input": 0.0, "output": 0.0, "source": "sin coste LLM"},
    "appregenera": {"input": 0.0, "output": 0.0, "source": "sin coste LLM"},
}


CONFIGS = [
    {
        "name": "current_actual_gpt54nano_gpt41mini",
        "label": "Configuracion actual real (gpt-5.4-nano + gpt-4.1-mini)",
        "base_model": "gpt-5.4-nano",
        "baseline_model": "gpt-5.4-nano",
        "secondary_model": "gpt-4.1-mini",
        "fallback_model": "gpt-4.1-mini",
    },
    {
        "name": "candidate_gpt54mini_gpt54",
        "label": "Candidata (gpt-5.4-mini + gpt-5.4)",
        "base_model": "gpt-5.4-mini",
        "baseline_model": "gpt-5.4-mini",
        "secondary_model": "gpt-5.4",
        "fallback_model": "gpt-5.4",
    },
]


BATTERY: List[Dict[str, Any]] = [
    {
        "id": "bus-01",
        "mode": "business",
        "question": "Cual es el cliente del proyecto 26018?",
        "expected_route": "business_produccion",
        "expected_module": "produccion",
        "expected_terms": ["regenera ops", "26018"],
    },
    {
        "id": "bus-02",
        "mode": "business",
        "question": "y el 26013?",
        "expected_route": "business_produccion",
        "expected_module": "produccion",
        "expected_terms": ["regenera ops dos", "26013"],
    },
    {
        "id": "bus-03",
        "mode": "business",
        "question": "Cuales son las licitaciones mas recientes relacionadas con OPS?",
        "expected_route": "business_licitaciones",
        "expected_module": "estudios",
        "expected_terms": ["ops", "est-098-2026"],
    },
    {
        "id": "bus-04",
        "mode": "business",
        "question": "dame solo las adjudicadas",
        "expected_route": "business_licitaciones",
        "expected_module": "estudios",
        "expected_terms": ["adjudicada", "26018"],
    },
    {
        "id": "bus-05",
        "mode": "business",
        "question": "Que proyectos tenemos en curso para el cliente REGENERA OPS?",
        "expected_route": "business_produccion",
        "expected_module": "produccion",
        "expected_terms": ["24032", "26018"],
    },
    {
        "id": "bus-06",
        "mode": "business",
        "question": "Cual es el importe adjudicado o previsto del proyecto OPS de 26018?",
        "expected_route": "business_licitaciones",
        "expected_module": "estudios",
        "expected_terms": ["12.312.500,00", "26018"],
    },
    {
        "id": "bus-07",
        "mode": "business",
        "question": "y del 26013?",
        "expected_route": "business_licitaciones",
        "expected_module": "estudios",
        "expected_terms": ["10.139.500,00", "26013"],
    },
    {
        "id": "bus-08",
        "mode": "business",
        "question": "Dame el top de proyectos o licitaciones vinculados a OPS con su cliente.",
        "expected_route": "business_licitaciones",
        "expected_module": "estudios",
        "expected_terms": ["cliente =", "26018"],
    },
    {
        "id": "bus-09",
        "mode": "business",
        "question": "Que cliente tiene EST-098-2026?",
        "expected_route": "business_licitaciones",
        "expected_module": "estudios",
        "expected_terms": ["regenera ops", "est-098-2026"],
    },
    {
        "id": "bus-10",
        "mode": "business",
        "question": "Que produccion tiene en 2027 la obra 26018?",
        "expected_route": "business_produccion",
        "expected_module": "produccion",
        "expected_terms": ["2027", "no hay datos"],
    },
    {
        "id": "tec-01",
        "mode": "technical",
        "question": "Que regula el reglamento de lineas electricas de alta tension?",
        "expected_route": "knowledge",
        "expected_source_prefix": "alta_tension/A16436-16554.pdf",
        "expected_terms": ["lineas electricas", "alta tension"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-02",
        "mode": "technical",
        "question": "Cuando y por quien deben realizarse inspecciones periodicas en lineas de AT?",
        "expected_route": "knowledge",
        "expected_source_prefix": "alta_tension/A16436-16554.pdf",
        "expected_terms": ["inspecciones", "periodicas"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-03",
        "mode": "technical",
        "question": "Que es el REBT?",
        "expected_route": "knowledge",
        "expected_source_prefix": "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf",
        "expected_terms": ["rebt", "baja tension"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-04",
        "mode": "technical",
        "question": "Que diferencia hay entre un esquema TT y un TN-S?",
        "expected_route": "knowledge",
        "expected_source_prefix": "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf",
        "expected_terms": ["tt", "tn-s"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-05",
        "mode": "technical",
        "question": "Que exige el REBT sobre la proteccion contra contactos indirectos?",
        "expected_route": "knowledge",
        "expected_source_prefix": "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf",
        "expected_terms": ["contactos indirectos", "proteccion"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-06",
        "mode": "technical",
        "question": "Que incluye el mantenimiento de inversores en la planta fotovoltaica?",
        "expected_route": "knowledge",
        "expected_source_prefix": "fotovoltaica_om/Manual-de-Manteminiento.pdf",
        "expected_terms": ["inversores", "mantenimiento"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-07",
        "mode": "technical",
        "question": "Como se comprueba el sistema de aviso de alarmas en el manual fotovoltaico?",
        "expected_route": "knowledge",
        "expected_source_prefix": "fotovoltaica_om/Manual-de-Manteminiento.pdf",
        "expected_terms": ["alarmas", "aviso"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-08",
        "mode": "technical",
        "question": "Que precaucion pide el manual para intervenir cajas de campo?",
        "expected_route": "knowledge",
        "expected_source_prefix": "fotovoltaica_om/Manual-de-Manteminiento.pdf",
        "expected_terms": ["cajas de campo", "precaucion"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-09",
        "mode": "technical",
        "question": "Que recomienda el manual sobre pruebas semanales sin carga en grupos electrogenos?",
        "expected_route": "knowledge",
        "expected_source_prefix": "grupos_electrogenos/Manual Grupos Electrogenos Diesel_ESP.pdf",
        "expected_terms": ["pruebas semanales", "sin carga"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-10",
        "mode": "technical",
        "question": "En epoca fria, que condicion de temperatura pide para grupos automaticos y que ayuda al arranque?",
        "expected_route": "knowledge",
        "expected_source_prefix": "grupos_electrogenos/Manual Grupos Electrogenos Diesel_ESP.pdf",
        "expected_terms": ["arranque", "temperatura"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-11",
        "mode": "technical",
        "question": "Que debe comprobarse en la recepcion y antes de mover el grupo electrogeno?",
        "expected_route": "knowledge",
        "expected_source_prefix": "grupos_electrogenos/Manual Grupos Electrogenos Diesel_ESP.pdf",
        "expected_terms": ["recepcion", "mover"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-12",
        "mode": "technical",
        "question": "Como clasifica la guia BT-40 las instalaciones generadoras?",
        "expected_route": "knowledge",
        "expected_source_prefix": "guias_tecnicas/Guia_bt_40_sep13R1 (1).pdf",
        "expected_terms": ["instalaciones generadoras", "clasifica"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-13",
        "mode": "technical",
        "question": "Que diferencia hay entre instalaciones generadoras aisladas, asistidas e interconectadas?",
        "expected_route": "knowledge",
        "expected_source_prefix": "guias_tecnicas/Guia_bt_40_sep13R1 (1).pdf",
        "expected_terms": ["aisladas", "interconectadas"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-14",
        "mode": "technical",
        "question": "Que condiciones de conexion a red destaca la guia BT-40?",
        "expected_route": "knowledge",
        "expected_source_prefix": "guias_tecnicas/Guia_bt_40_sep13R1 (1).pdf",
        "expected_terms": ["conexion a red", "condiciones"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-15",
        "mode": "technical",
        "question": "Que exige el RITE sobre el programa de mantenimiento preventivo?",
        "expected_route": "knowledge",
        "expected_source_prefix": "rite/RITE IT3.pdf",
        "expected_terms": ["mantenimiento preventivo", "rite"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-16",
        "mode": "technical",
        "question": "Que periodicidad tiene la limpieza de evaporadores segun la IT 3?",
        "expected_route": "knowledge",
        "expected_source_prefix": "rite/RITE IT3.pdf",
        "expected_terms": ["evaporadores", "periodicidad"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-17",
        "mode": "technical",
        "question": "Que operaciones recoge la tabla 3.1 del RITE IT3?",
        "expected_route": "knowledge",
        "expected_source_prefix": "rite/RITE IT3.pdf",
        "expected_terms": ["tabla 3.1", "operaciones"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-18",
        "mode": "technical",
        "question": "Que cambia el RITE 2021 sobre BACS?",
        "expected_route": "knowledge",
        "expected_source_prefix": "rite/RITE-2021-BOE-A-2021-4572.pdf",
        "expected_terms": ["bacs", "2021"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-19",
        "mode": "technical",
        "question": "Cual es el objeto del RITE consolidado?",
        "expected_route": "knowledge",
        "expected_source_prefix": "rite/RITE-BOE-A-2007-15820-consolidado.pdf",
        "expected_terms": ["objeto", "rite"],
        "part": "legacy_technical",
    },
    {
        "id": "tec-20",
        "mode": "technical",
        "question": "Que inspecciones periodicas preve el RITE consolidado?",
        "expected_route": "knowledge",
        "expected_source_prefix": "rite/RITE-BOE-A-2007-15820-consolidado.pdf",
        "expected_terms": ["inspecciones", "periodicas"],
        "part": "legacy_technical",
    },
    {
        "id": "ops-21",
        "mode": "technical",
        "question": "Que regula la IEC/ISO/IEEE 80005-1 en OPS?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/01_normativa_base/BS ISO IEC IEEE 80005-1_2012.pdf",
        "expected_terms": ["80005-1", "hvsc"],
        "part": "ops_technical",
    },
    {
        "id": "ops-22",
        "mode": "technical",
        "question": "Que incluye el alcance de la conexion shore-to-ship segun la IEC 80005-1?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/01_normativa_base/BS ISO IEC IEEE 80005-1_2012.pdf",
        "expected_terms": ["shore", "ship"],
        "part": "ops_technical",
    },
    {
        "id": "ops-23",
        "mode": "technical",
        "question": "A que no aplica la IEC 80005-1?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/01_normativa_base/BS ISO IEC IEEE 80005-1_2012.pdf",
        "expected_terms": ["no aplica", "mantenimiento"],
        "part": "ops_technical",
    },
    {
        "id": "ops-24",
        "mode": "technical",
        "question": "Que regula la IEC/IEEE 80005-2?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/01_normativa_base/iecieee80005-2_2016.pdf",
        "expected_terms": ["80005-2", "data communication"],
        "part": "ops_technical",
    },
    {
        "id": "ops-25",
        "mode": "technical",
        "question": "Que interfaces de datos cubre la 80005-2 para monitoring and control?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/01_normativa_base/iecieee80005-2_2016.pdf",
        "expected_terms": ["interfaces", "monitoring"],
        "part": "ops_technical",
    },
    {
        "id": "ops-26",
        "mode": "technical",
        "question": "En que se diferencia la 80005-2 de la 80005-1?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/01_normativa_base/iecieee80005-2_2016.pdf",
        "expected_terms": ["80005-2", "80005-1"],
        "part": "ops_technical",
    },
    {
        "id": "ops-27",
        "mode": "technical",
        "question": "Que recoge la guia EMSA Part 1 sobre SSE?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/02_guias_implantacion/EMSA Guidance on SSE_PART1.pdf",
        "expected_terms": ["emsa", "sse"],
        "part": "ops_technical",
    },
    {
        "id": "ops-28",
        "mode": "technical",
        "question": "Que dice la guia EMSA Part 1 sobre calidad de energia o compatibilidad red-buque?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/02_guias_implantacion/EMSA Guidance on SSE_PART1.pdf",
        "expected_terms": ["calidad de energia", "compatibilidad"],
        "part": "ops_technical",
    },
    {
        "id": "ops-29",
        "mode": "technical",
        "question": "En OPS, que documento trata la compatibilidad red-buque y la calidad de energia?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/02_guias_implantacion/EMSA Guidance on SSE_PART1.pdf",
        "expected_terms": ["emsa", "calidad de energia"],
        "part": "ops_technical",
    },
    {
        "id": "ops-30",
        "mode": "technical",
        "question": "Que cubre la guia EMSA Part 2 sobre SSE?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/02_guias_implantacion/EMSA Guidance on SSE_PART2_Version 2.pdf",
        "expected_terms": ["part 2", "sse"],
        "part": "ops_technical",
    },
    {
        "id": "ops-31",
        "mode": "technical",
        "question": "Que recomendaciones hace la guia EMSA Part 2 sobre operacion o seguridad?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/02_guias_implantacion/EMSA Guidance on SSE_PART2_Version 2.pdf",
        "expected_terms": ["operacion", "seguridad"],
        "part": "ops_technical",
    },
    {
        "id": "ops-32",
        "mode": "technical",
        "question": "En OPS, que documento baja al detalle de operacion practica y seguridad?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/02_guias_implantacion/EMSA Guidance on SSE_PART2_Version 2.pdf",
        "expected_terms": ["part 2", "operacion"],
        "part": "ops_technical",
    },
    {
        "id": "ops-33",
        "mode": "technical",
        "question": "Que es el Anexo 1 de EOPSA?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/03_checklists_operacion/EOPSA_Checklist_OPS_ES.pdf",
        "expected_terms": ["anexo 1", "checklist"],
        "part": "ops_technical",
    },
    {
        "id": "ops-34",
        "mode": "technical",
        "question": "Que informacion pide el checklist OPS sobre el origen de la fuente de alimentacion?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/03_checklists_operacion/EOPSA_Checklist_OPS_ES.pdf",
        "expected_terms": ["fuente de alimentacion", "origen"],
        "part": "ops_technical",
    },
    {
        "id": "ops-35",
        "mode": "technical",
        "question": "Que solicita el checklist OPS sobre automatizacion y supervision?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/03_checklists_operacion/EOPSA_Checklist_OPS_ES.pdf",
        "expected_terms": ["automatizacion", "supervision"],
        "part": "ops_technical",
    },
    {
        "id": "ops-36",
        "mode": "technical",
        "question": "Que es la guia EOPSA para una licitacion OPS completa y exitosa?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/03_checklists_operacion/EOPSA_Guia_OPS_ES_Completa.pdf",
        "expected_terms": ["licitacion", "exitosa"],
        "part": "ops_technical",
    },
    {
        "id": "ops-37",
        "mode": "technical",
        "question": "Que apartados cubre la guia EOPSA sobre operacion, subestacion y CMS?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/03_checklists_operacion/EOPSA_Guia_OPS_ES_Completa.pdf",
        "expected_terms": ["subestacion", "cms"],
        "part": "ops_technical",
    },
    {
        "id": "ops-38",
        "mode": "technical",
        "question": "Que documento de OPS serviria como checklist completo para una licitacion?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/03_checklists_operacion/EOPSA_Guia_OPS_ES_Completa.pdf",
        "expected_terms": ["checklist", "licitacion"],
        "part": "ops_technical",
    },
    {
        "id": "ops-39",
        "mode": "technical",
        "question": "Que resume el survey sectorial sobre OPS?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/04_resumen_sectorial/On_shore_power_supply_summary-surveys_final.pdf",
        "expected_terms": ["survey", "ops"],
        "part": "ops_technical",
    },
    {
        "id": "ops-40",
        "mode": "technical",
        "question": "Que documento sectorial habla de successful OPS connections y panoramica global?",
        "expected_route": "knowledge",
        "expected_source_prefix": "ops/04_resumen_sectorial/On_shore_power_supply_summary-surveys_final.pdf",
        "expected_terms": ["successful ops connections", "survey"],
        "part": "ops_technical",
    },
]


USER_HEADERS = {
    "x-user-id": "3",
    "x-user-name": "ops_local_test",
    "x-user-email": "ops_local_test@regeneraenergy.es",
    "x-auth-provider": "local",
}


def normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
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


def build_backend_env(config: Dict[str, Any], port: int) -> Dict[str, str]:
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
            "CHROMA_DB_PATH": str(ROOT / "tmp-logs" / "chroma_ops15"),
            "SYNC_DOCUMENTS_ON_STARTUP": "false",
            "RAG_BACKEND": "chroma",
            "APPREGENERA_SQL_CONNECTION_STRING": (
                "Server=tcp:sql-appregenera-pro.database.windows.net,1433;"
                "Initial Catalog=AppRegenera;Persist Security Info=False;"
                "User ID=itadminregenera;Password=Regenera_app_database_xyz_$%&;"
                "MultipleActiveResultSets=False;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;"
            ),
            "APPREGENERA_ALLOWED_MODULES": "estudios,produccion",
            "APPREGENERA_DEV_BYPASS_KEY": "regenera-chatbot-business-2026-05-25",
            "LOG_LEVEL": "INFO",
            "WEBSITE_PORT": str(port),
        }
    )
    return env


def start_backend(config: Dict[str, Any], port: int) -> subprocess.Popen:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = TMP_DIR / f"eval-{config['name']}-stdout.log"
    stderr_path = TMP_DIR / f"eval-{config['name']}-stderr.log"
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
        payload = response.json()
        return int(payload["conversation_id"])
    raise RuntimeError(f"No se pudo crear la conversacion para el modo {mode}")


def ask_question(base_url: str, conversation_id: int, question: str) -> Dict[str, Any]:
    for _ in range(5):
        response = requests.post(
            f"{base_url}/messages",
            json={"conversation_id": conversation_id, "question": question},
            headers=USER_HEADERS,
            timeout=240,
        )
        if response.status_code == 503:
            time.sleep(2)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"No se ha podido obtener respuesta para la pregunta: {question}")


def extract_source_prefix(source: Any) -> str:
    if isinstance(source, dict):
        module = str(source.get("module") or "").strip()
        label = str(source.get("source") or "").strip()
        return f"{label}|{module}" if module else label
    text = str(source or "").strip()
    return text.split(" (", 1)[0]


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = MODEL_PRICING_USD_PER_MILLION.get((model or "").strip().lower())
    if not pricing:
        return 0.0
    return (prompt_tokens / 1_000_000) * pricing["input"] + (completion_tokens / 1_000_000) * pricing["output"]


def score_item(item: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    route = str(payload.get("route") or "")
    response_text = str(payload.get("response") or "")
    normalized_response = normalize(response_text)
    sources = payload.get("sources") or []
    source_prefixes = [extract_source_prefix(source) for source in sources]
    expected_terms = [normalize(term) for term in item.get("expected_terms") or []]
    matched_terms = [term for term in expected_terms if term in normalized_response]

    route_weight = 40
    source_weight = 30
    content_weight = 30

    route_score = route_weight if route == item["expected_route"] else 0

    source_score = 0
    expected_source_prefix = item.get("expected_source_prefix")
    expected_module = item.get("expected_module")
    if expected_source_prefix:
        source_score = source_weight if any(prefix.startswith(expected_source_prefix) for prefix in source_prefixes) else 0
    elif expected_module:
        source_score = source_weight if any(prefix.endswith(f"|{expected_module}") for prefix in source_prefixes) else 0
    elif item.get("allow_empty_sources"):
        source_score = source_weight

    if expected_terms:
        content_score = round((len(matched_terms) / len(expected_terms)) * content_weight, 2)
    else:
        content_score = content_weight

    total_score = round(route_score + source_score + content_score, 2)
    if total_score >= 85:
        verdict = "correcta"
    elif total_score >= 50:
        verdict = "parcial"
    else:
        verdict = "incorrecta"

    return {
        "route_score": route_score,
        "source_score": source_score,
        "content_score": content_score,
        "total_score": total_score,
        "matched_terms": matched_terms,
        "missing_terms": [term for term in expected_terms if term not in matched_terms],
        "source_prefixes": source_prefixes,
        "verdict": verdict,
    }


def summarize_results(config: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    overall_score = round(sum(row["score"]["total_score"] for row in rows) / len(rows), 2)
    by_mode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_part: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    final_model_counts = Counter()
    base_model_counts = Counter()
    model_costs = defaultdict(float)
    model_tokens = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    for row in rows:
        item = row["item"]
        by_mode[item["mode"]].append(row)
        by_part[item.get("part") or item["mode"]].append(row)
        trace = row.get("trace") or {}
        base_model = str(trace.get("base_model") or row.get("db", {}).get("base_model") or "")
        final_model = str(trace.get("final_model") or row.get("db", {}).get("final_model") or "")
        if not final_model and item["mode"] == "business":
            business_path = str(trace.get("path") or "").strip().lower()
            final_model = "appregenera_sql" if business_path == "sql" else ("appregenera_http" if business_path == "http" else "appregenera")
            base_model = final_model
        usage_breakdown = trace.get("usage_breakdown") or {}
        base_usage = usage_breakdown.get("base") or {}
        final_usage = usage_breakdown.get("final") or {}
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
        if row.get("db", {}).get("model") == "appregenera_sql":
            final_model_counts["appregenera_sql"] += 0

    def summarize_bucket(bucket_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(bucket_rows)
        score = round(sum(row["score"]["total_score"] for row in bucket_rows) / total, 2)
        verdicts = Counter(row["score"]["verdict"] for row in bucket_rows)
        return {
            "questions": total,
            "score_0_100": score,
            "correctas": verdicts.get("correcta", 0),
            "parciales": verdicts.get("parcial", 0),
            "incorrectas": verdicts.get("incorrecta", 0),
        }

    breakdown_by_mode = {key: summarize_bucket(value) for key, value in sorted(by_mode.items())}
    breakdown_by_part = {key: summarize_bucket(value) for key, value in sorted(by_part.items())}

    final_model_share = {
        model: {
            "count": count,
            "share_pct": round((count / len(rows)) * 100, 2),
        }
        for model, count in final_model_counts.items()
    }
    base_model_share = {
        model: {
            "count": count,
            "share_pct": round((count / len(rows)) * 100, 2),
        }
        for model, count in base_model_counts.items()
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

    return {
        "config_name": config["name"],
        "config_label": config["label"],
        "base_model": config["base_model"],
        "secondary_model": config["secondary_model"],
        "overall_score_0_100": overall_score,
        "breakdown_by_mode": breakdown_by_mode,
        "breakdown_by_part": breakdown_by_part,
        "final_model_share": final_model_share,
        "base_model_share": base_model_share,
        "cost_breakdown": cost_breakdown,
        "total_estimated_cost_usd": round(sum(model_costs.values()), 6),
    }


def run_config(config: Dict[str, Any], port: int, battery: List[Dict[str, Any]]) -> Dict[str, Any]:
    base_url = f"http://127.0.0.1:{port}"
    process = start_backend(config, port)
    try:
        wait_ready(base_url)
        conversations = {
            "business": create_conversation(base_url, f"battery negocio {config['name']}", "business"),
            "technical": create_conversation(base_url, f"battery tecnico {config['name']}", "technical"),
        }
        rows = []
        for item in battery:
            payload = ask_question(base_url, conversations[item["mode"]], item["question"])
            score = score_item(item, payload)
            rows.append(
                {
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
        return {
            "summary": summarize_results(config, rows),
            "rows": rows,
        }
    finally:
        stop_backend(process)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta una bateria comparativa de 50 preguntas sobre el chatbot.")
    parser.add_argument(
        "--configs",
        nargs="*",
        default=[config["name"] for config in CONFIGS],
        help="Nombres de configuracion a ejecutar",
    )
    parser.add_argument("--port-base", type=int, default=8010)
    parser.add_argument("--max-questions", type=int, default=0, help="Limita la bateria a las primeras N preguntas")
    parser.add_argument(
        "--output",
        default=str(TMP_DIR / "model_battery_eval_results.json"),
        help="Ruta del JSON de salida",
    )
    args = parser.parse_args()

    selected_configs = [config for config in CONFIGS if config["name"] in set(args.configs)]
    if not selected_configs:
        raise SystemExit("No se ha seleccionado ninguna configuracion valida")

    battery = BATTERY[: args.max_questions] if args.max_questions and args.max_questions > 0 else BATTERY

    results = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "battery_size": len(battery),
        "business_questions": sum(1 for item in battery if item["mode"] == "business"),
        "technical_questions": sum(1 for item in battery if item["mode"] == "technical"),
        "notes": {
            "actual_current_config": "sandbox usa gpt-5.4-nano + gpt-4.1-mini, no gpt-4.1-nano + gpt-4.1-mini",
            "pricing_sources": MODEL_PRICING_USD_PER_MILLION,
        },
        "runs": {},
    }

    for index, config in enumerate(selected_configs):
        port = args.port_base + index
        print(f"[RUN] {config['label']} en puerto {port}", flush=True)
        results["runs"][config["name"]] = run_config(config, port, battery)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: run["summary"] for name, run in results["runs"].items()}, ensure_ascii=False, indent=2))
    print(f"Resultados guardados en {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
