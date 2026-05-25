import re
import unicodedata
from typing import Any, Dict, List

from appregenera_client import AppRegeneraClientError, get_json, post_json
from config import APPREGENERA_DEV_BYPASS_KEY


MONTH_ALIASES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

MONTH_LABELS = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}

PRODUCTION_MONTH_FIELDS = {
    1: "produccionEnero",
    2: "produccionFebrero",
    3: "produccionMarzo",
    4: "produccionAbril",
    5: "produccionMayo",
    6: "produccionJunio",
    7: "produccionJulio",
    8: "produccionJulioAgosto",
    9: "produccionSeptiembre",
    10: "produccionEstimadaOctubre",
    11: "produccionEstimadaNoviembre",
    12: "produccionEstimadaDiciembre",
}

LICITACION_FIELD_SPECS = [
    ("numeroOferta", "numero de oferta", ("numero de oferta", "numero oferta", "n oferta")),
    ("numeroProyecto", "numero de proyecto", ("numero de proyecto", "numero proyecto", "n proyecto")),
    ("obra", "obra", ("nombre de la obra", "nombre obra")),
    ("cliente", "cliente", ("cliente",)),
    ("tipo", "tipo", ("tipo de licitacion", "tipo licitacion")),
    ("tipoRegistro", "tipo de registro", ("tipo de registro", "tipo registro")),
    ("importeContratado", "importe contratado", ("importe contratado", "importe adjudicado")),
    ("produccion", "produccion", ("produccion",)),
    ("tipoObra", "tipo de obra", ("tipo de obra", "tipo obra")),
    ("observaciones", "observaciones", ("observaciones",)),
    ("enlaceLicitacion", "enlace de licitacion", ("enlace de licitacion", "enlace licitacion", "url licitacion", "enlace")),
    ("situacionOferta", "situacion de la oferta", ("situacion de la oferta", "situacion oferta")),
    ("fechaAdjudicacion", "fecha de adjudicacion", ("fecha de adjudicacion", "fecha adjudicacion", "cuando se adjudico", "cuando fue adjudicada")),
    ("fechaPresentacion", "fecha de presentacion", ("fecha de presentacion", "fecha presentacion", "cuando se presento")),
    ("probabilidadAdjudicacion", "probabilidad de adjudicacion", ("probabilidad de adjudicacion", "probabilidad adjudicacion")),
    ("fechaApertura", "fecha de apertura", ("fecha de apertura", "fecha apertura")),
    ("carteraSiguienteAnio", "cartera del siguiente ano", ("cartera siguiente ano", "cartera del siguiente ano")),
    ("periodoEjecucion", "periodo de ejecucion", ("periodo de ejecucion", "periodo ejecucion", "plazo de ejecucion", "plazo ejecucion")),
    ("plan2026", "plan 2026", ("plan 2026", "pipeline 2026")),
    ("plan2027", "plan 2027", ("plan 2027", "pipeline 2027")),
    ("plan2028", "plan 2028", ("plan 2028", "pipeline 2028")),
    ("plan2029", "plan 2029", ("plan 2029", "pipeline 2029")),
    ("pendiente", "pendiente", ("pendiente",)),
    ("concurso", "concurso", ("concurso",)),
    ("tipologiaObra", "tipologia de obra", ("tipologia de obra", "tipologia obra")),
    ("estado", "estado", ("estado",)),
]

PRODUCCION_FIELD_SPECS = [
    ("codigoObra", "codigo de obra", ("codigo de obra", "codigo obra")),
    ("nombreObra", "nombre de la obra", ("nombre de la obra", "nombre obra")),
    ("tipoCliente", "tipo de cliente", ("tipo de cliente", "tipo cliente")),
    ("finalizada", "finalizada", ("finalizada", "finalizado", "esta finalizada", "esta finalizado")),
    ("oculta", "oculta", ("oculta", "oculto", "esta oculta", "esta oculto")),
    ("responsableNombre", "responsable", ("responsable", "quien es el responsable", "responsable de la obra")),
    ("responsableCodigo", "codigo del responsable", ("codigo del responsable", "codigo responsable")),
    ("tipoObra", "tipo de obra", ("tipo de obra", "tipo obra")),
    ("importeContratado", "importe contratado", ("importe contratado", "importe adjudicado")),
    ("rentabilidadPrevista2026", "rentabilidad prevista 2026", ("rentabilidad prevista 2026", "rentabilidad 2026", "rentabilidad prevista", "rentabilidad")),
    ("produccionOrigen2025", "produccion origen 2025", ("produccion origen 2025",)),
    ("produccionOrigenAnosAnteriores", "produccion origen anos anteriores", ("produccion origen anos anteriores",)),
    ("ventaMaster2025", "venta master 2025", ("venta master 2025",)),
    ("porcentajeMateriales", "porcentaje de materiales", ("porcentaje de materiales", "porcentaje materiales")),
    ("porcentajeManoObra", "porcentaje de mano de obra", ("porcentaje de mano de obra", "porcentaje mano de obra")),
    ("cartera2026", "cartera 2026", ("cartera 2026", "cartera")),
    ("pendiente2026", "pendiente 2026", ("pendiente 2026",)),
    ("diferencia", "diferencia", ("diferencia",)),
    ("comentarios", "comentarios", ("comentarios",)),
    ("ultimaSincronizacionExcelUtc", "ultima sincronizacion excel", ("ultima sincronizacion excel", "ultima actualizacion excel")),
    ("licitacionNumeroProyecto", "numero de proyecto", ("numero de proyecto", "numero proyecto", "n proyecto")),
    ("licitacionNumeroOferta", "numero de oferta", ("numero de oferta", "numero oferta", "n oferta")),
    ("licitacionCliente", "cliente", ("cliente",)),
    ("licitacionEstado", "estado", ("estado",)),
]

FIELD_LABELS = {key: label for key, label, _ in LICITACION_FIELD_SPECS + PRODUCCION_FIELD_SPECS}
FIELD_LABELS.update(
    {
        "importeContratado2026": "importe contratado 2026",
        "importeContratado2027": "importe contratado 2027",
        "importeContratado2028": "importe contratado 2028",
        "importeContratado2029": "importe contratado 2029",
        "importeContratadoPrevio": "importe contratado previo",
        "produccion2026": "produccion 2026",
        "produccion2027": "produccion 2027",
        "produccion2028": "produccion 2028",
        "produccion2029": "produccion 2029",
        "produccionPrevio": "produccion previa",
        "produccionPrimerCuatrimestre": "produccion primer cuatrimestre",
        "produccionSegundoCuatrimestre": "produccion segundo cuatrimestre",
        "produccionEstimadaTercerCuatrimestre": "produccion estimada tercer cuatrimestre",
        "periodosMensuales": "periodos mensuales",
        "produccionEnero": "produccion enero",
        "produccionFebrero": "produccion febrero",
        "produccionMarzo": "produccion marzo",
        "produccionAbril": "produccion abril",
        "produccionMayo": "produccion mayo",
        "produccionJunio": "produccion junio",
        "produccionJulio": "produccion julio",
        "produccionJulioAgosto": "produccion julio/agosto",
        "produccionSeptiembre": "produccion septiembre",
        "produccionEstimadaOctubre": "produccion estimada octubre",
        "produccionEstimadaNoviembre": "produccion estimada noviembre",
        "produccionEstimadaDiciembre": "produccion estimada diciembre",
    }
)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def detect_business_route(question: str) -> str | None:
    text = _normalize(question)
    explicit_scope = _detect_explicit_scope(text)
    if explicit_scope == "estudios":
        return "business_licitaciones"
    if explicit_scope == "produccion":
        return "business_produccion"

    if "importe contratado" in text:
        return "business_licitaciones"
    if any(hint in text for hint in ("produccion", "cartera", "rentabilidad", "responsable", "cuatrimestre")):
        return "business_produccion"
    if any(hint in text for hint in ("licitacion", "licitaciones", "oferta", "adjudicacion", "pipeline", "plan ")):
        return "business_licitaciones"
    if any(hint in text for hint in ("cliente", "estado", "numero proyecto", "numero oferta", "tipo obra")):
        return "business_licitaciones"
    return None


def _detect_explicit_scope(text: str) -> str | None:
    if any(hint in text for hint in ("licitacion", "licitaciones", "oferta", "pipeline", "plan ")):
        return "estudios"
    if any(hint in text for hint in ("obra", "control de obras", "codigo de obra", "codigo obra", "cartera", "rentabilidad")):
        return "produccion"
    return None


def answer_business_question(
    question: str,
    *,
    user_token: str | None,
    preferred_route: str | None = None,
    history: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    if not user_token and not APPREGENERA_DEV_BYPASS_KEY:
        return {
            "response": (
                "Para consultar datos de Licitaciones o Produccion necesitas iniciar sesion corporativa con Microsoft. "
                "El modo documental puede seguir funcionando sin esa sesion."
            ),
            "route": "business_auth_required",
            "confidence": 1.0,
            "sources": [],
        }

    normalized = _normalize(question)
    explicit_scope = _detect_explicit_scope(normalized)
    aggregate_request = _is_global_aggregate_request(normalized)
    if aggregate_request:
        return {
            "response": (
                "Las comparativas globales entre todos los proyectos aun no estan soportadas por el conector actual de AppRegenera. "
                "Ahora mismo puedo responder mejor si indicas un proyecto, obra o licitacion concreta."
            ),
            "route": preferred_route or detect_business_route(question) or "business_licitaciones",
            "confidence": 0.9,
            "sources": [],
        }

    preferred_module = "estudios" if (preferred_route or detect_business_route(question)) == "business_licitaciones" else "produccion"
    modules_to_try = _build_module_candidates(preferred_module, explicit_scope)

    try:
        first_not_found_message: str | None = None
        first_ambiguous_message: str | None = None
        first_no_data_message: str | None = None

        for module in modules_to_try:
            route = "business_licitaciones" if module == "estudios" else "business_produccion"
            parsed = _parse_question(question, module=module, history=history or [])
            if not parsed["reference"]:
                continue

            search_path = "/api/chatbot/licitaciones/search" if module == "estudios" else "/api/chatbot/produccion/search"
            query_path = "/api/chatbot/licitaciones/query" if module == "estudios" else "/api/chatbot/produccion/query"

            matches = get_json(search_path, params={"q": parsed["reference"], "take": 5}, user_token=user_token) or []
            if not matches:
                if first_not_found_message is None:
                    first_not_found_message = (
                        f"No he encontrado ninguna coincidencia en el modulo de {'Licitaciones' if module == 'estudios' else 'Produccion'} "
                        f"para '{parsed['reference']}'."
                    )
                continue

            if len(matches) > 1:
                if first_ambiguous_message is None:
                    options = "; ".join(f"{item['primaryCode']} - {item['displayName']}" for item in matches[:5])
                    first_ambiguous_message = (
                        f"He encontrado varias coincidencias en {'Licitaciones' if module == 'estudios' else 'Produccion'}: "
                        f"{options}. Indica un codigo o nombre mas concreto."
                    )
                continue

            match = matches[0]
            payload = {
                "entityId": match["id"],
                "reference": parsed["reference"],
                "field": None,
                "fields": parsed["fields"],
                "year": parsed["year"],
                "cuatrimestre": parsed["cuatrimestre"],
                "month": parsed["month"],
            }
            result = post_json(query_path, payload, user_token=user_token)
            result = _enrich_result_with_match(result, match, module=module)
            response_text = _format_business_response(result, module=module, parsed=parsed)

            if response_text is None:
                if first_no_data_message is None:
                    first_no_data_message = _build_no_data_message(match, module=module, parsed=parsed)
                if explicit_scope is None and len(modules_to_try) > 1 and not parsed.get("per_month"):
                    continue
                response_text = first_no_data_message

            return {
                "response": response_text,
                "route": route,
                "confidence": 1.0,
                "sources": [
                    {
                        "source": "AppRegenera",
                        "module": module,
                        "entity": match["displayName"],
                        "code": match["primaryCode"],
                    }
                ],
            }

        fallback_message = (
            first_ambiguous_message
            or first_no_data_message
            or first_not_found_message
            or "No he podido encontrar un dato de negocio que encaje con la pregunta."
        )
        return {
            "response": fallback_message,
            "route": preferred_route or detect_business_route(question) or "business_licitaciones",
            "confidence": 0.95,
            "sources": [],
        }
    except AppRegeneraClientError as exc:
        if exc.status_code == 403:
            message = "No tienes permisos para consultar ese dato en AppRegenera."
        else:
            message = f"No he podido consultar AppRegenera: {exc}"
        return {
            "response": message,
            "route": preferred_route or detect_business_route(question) or "business_licitaciones",
            "confidence": 0.0,
            "sources": [],
        }


def _build_module_candidates(preferred_module: str, explicit_scope: str | None) -> List[str]:
    if explicit_scope == "estudios":
        return ["estudios"]
    if explicit_scope == "produccion":
        return ["produccion"]
    other = "produccion" if preferred_module == "estudios" else "estudios"
    return [preferred_module, other]


def _parse_question(question: str, *, module: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = _normalize(question)
    year_match = re.search(r"\b(20\d{2})\b", normalized)
    year = int(year_match.group(1)) if year_match else None
    cuatrimestre = _extract_cuatrimestre(normalized)
    month = _extract_month(normalized)
    reference = _resolve_reference(question, normalized, history)
    per_month = _is_per_month_request(normalized)
    fields = _detect_fields(
        normalized,
        module=module,
        year=year,
        cuatrimestre=cuatrimestre,
        month=month,
        per_month=per_month,
    )
    return {
        "reference": reference,
        "fields": fields,
        "year": year,
        "cuatrimestre": cuatrimestre,
        "month": month,
        "per_month": per_month,
    }


def _resolve_reference(original_question: str, normalized: str, history: List[Dict[str, Any]]) -> str | None:
    reference = _extract_reference(original_question, normalized)
    if reference:
        return reference

    for item in reversed(history or []):
        history_question = str(item.get("question") or "")
        history_normalized = _normalize(history_question)
        history_reference = _extract_reference(history_question, history_normalized)
        if history_reference:
            return history_reference
    return None


def _extract_reference(original_question: str, normalized: str) -> str | None:
    explicit_code_match = re.search(r"\b(?:proyecto|obra|licitacion|oferta)\s+(\d{4,8})\b", normalized)
    if explicit_code_match:
        return explicit_code_match.group(1)

    numeric_tokens = re.findall(r"\b\d{4,8}\b", normalized)
    non_year_tokens = [token for token in numeric_tokens if not re.fullmatch(r"20\d{2}", token)]
    if non_year_tokens:
        return non_year_tokens[0]

    quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', original_question)
    if quoted:
        value = (quoted.group(1) or quoted.group(2) or "").strip()
        return value or None
    return None


def _extract_cuatrimestre(text: str) -> int | None:
    patterns = (
        (r"\b(c1|primer cuatrimestre|1er cuatrimestre|cuatrimestre 1|cuatrimestre uno)\b", 1),
        (r"\b(c2|segundo cuatrimestre|2do cuatrimestre|cuatrimestre 2|cuatrimestre dos)\b", 2),
        (r"\b(c3|tercer cuatrimestre|3er cuatrimestre|cuatrimestre 3|cuatrimestre tres)\b", 3),
    )
    for pattern, value in patterns:
        if re.search(pattern, text):
            return value
    return None


def _extract_month(text: str) -> int | None:
    for name, month in MONTH_ALIASES.items():
        if re.search(rf"\b{name}\b", text):
            return month
    return None


def _is_per_month_request(text: str) -> bool:
    return any(
        token in text
        for token in (
            "cada mes",
            "por mes",
            "mes a mes",
            "en cada mes",
            "todos los meses",
        )
    )


def _is_global_aggregate_request(text: str) -> bool:
    return (
        any(token in text for token in ("cual es el proyecto con mas", "que proyecto tiene mas", "proyecto con mayor"))
        and any(token in text for token in ("importe contratado", "produccion", "cartera", "pipeline", "plan"))
    )


def _detect_fields(
    text: str,
    *,
    module: str,
    year: int | None,
    cuatrimestre: int | None,
    month: int | None,
    per_month: bool,
) -> List[str]:
    fields: List[str] = []
    text_without_type_client = text.replace("tipo de cliente", " ").replace("tipo cliente", " ")

    if "pipeline" in text or "plan " in text:
        for plan_year in (2026, 2027, 2028, 2029):
            if str(plan_year) in text:
                fields.append(f"plan{plan_year}")
                break

    if "importe contratado" in text or "importe adjudicado" in text:
        if module == "estudios":
            if year in {2026, 2027, 2028, 2029} and not cuatrimestre and not month:
                fields.append(f"importeContratado{year}")
            elif any(token in text for token in ("previo", "anteriores", "anterior")):
                fields.append("importeContratadoPrevio")
            else:
                fields.append("importeContratado")
        else:
            fields.append("importeContratado")

    if "produccion" in text:
        if module == "estudios":
            if year in {2026, 2027, 2028, 2029} and not cuatrimestre and not month:
                fields.append(f"produccion{year}")
            elif any(token in text for token in ("previa", "previo", "anteriores", "anterior")):
                fields.append("produccionPrevio")
            else:
                fields.append("produccion")
        else:
            if per_month:
                fields.extend(_monthly_field_keys())
                fields.append("periodosMensuales")
            elif month:
                fields.append(PRODUCTION_MONTH_FIELDS.get(month, "periodosMensuales"))
            elif cuatrimestre == 1:
                fields.append("produccionPrimerCuatrimestre")
            elif cuatrimestre == 2:
                fields.append("produccionSegundoCuatrimestre")
            elif cuatrimestre == 3:
                fields.append("produccionEstimadaTercerCuatrimestre")
            else:
                fields.append("periodosMensuales")

    if module == "produccion" and "cartera" in text and year == 2026:
        fields.append("cartera2026")
    if module == "produccion" and "pendiente" in text and year == 2026:
        fields.append("pendiente2026")

    field_specs = LICITACION_FIELD_SPECS if module == "estudios" else PRODUCCION_FIELD_SPECS
    for canonical, _, aliases in field_specs:
        for alias in aliases:
            haystack = text_without_type_client if alias == "cliente" and module == "produccion" else text
            if alias in haystack and canonical not in fields:
                fields.append(canonical)
                break

    if not fields and re.search(r"\bcual es la obra\b|\bque obra\b|\bnombre de la obra\b|\bnombre obra\b", text):
        fields.append("obra" if module == "estudios" else "nombreObra")

    if not fields:
        fields = ["importeContratado", "cliente", "estado"] if module == "estudios" else ["nombreObra", "importeContratado"]
    return _dedupe(fields)[:16]


def _monthly_field_keys() -> List[str]:
    return [
        PRODUCTION_MONTH_FIELDS[1],
        PRODUCTION_MONTH_FIELDS[2],
        PRODUCTION_MONTH_FIELDS[3],
        PRODUCTION_MONTH_FIELDS[4],
        PRODUCTION_MONTH_FIELDS[5],
        PRODUCTION_MONTH_FIELDS[6],
        PRODUCTION_MONTH_FIELDS[7],
        PRODUCTION_MONTH_FIELDS[8],
        PRODUCTION_MONTH_FIELDS[9],
        PRODUCTION_MONTH_FIELDS[10],
        PRODUCTION_MONTH_FIELDS[11],
        PRODUCTION_MONTH_FIELDS[12],
    ]


def _dedupe(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _enrich_result_with_match(result: Dict[str, Any], match: Dict[str, Any], *, module: str) -> Dict[str, Any]:
    fields = result.get("fields") or []
    if module == "produccion":
        for item in fields:
            if item.get("key") == "finalizada" and item.get("value") is None:
                status = (match.get("status") or "").strip().lower()
                if status == "finalizada":
                    item["value"] = True
                elif status == "activa":
                    item["value"] = False
    return result


def _format_business_response(result: Dict[str, Any], *, module: str, parsed: Dict[str, Any]) -> str | None:
    status = result.get("status")
    if status == "ambiguous":
        matches = result.get("ambiguousMatches") or []
        options = "; ".join(f"{item['primaryCode']} - {item['displayName']}" for item in matches[:5])
        return f"He encontrado varias coincidencias: {options}. Indica un codigo o nombre mas concreto."
    if status == "not_found":
        return result.get("message") or "No se ha encontrado informacion."

    entity = result.get("entity") or {}
    fields = result.get("fields") or []
    if not fields:
        return None

    prefix = "Licitacion" if module == "estudios" else "Obra"
    name = entity.get("displayName") or entity.get("primaryCode") or parsed["reference"]
    code = entity.get("primaryCode")
    period_text = _build_period_text(parsed)

    if module == "produccion" and parsed.get("per_month"):
        monthly_text = _format_monthly_breakdown(fields, name=name, code=code, period_text=period_text)
        if monthly_text:
            return monthly_text
        return None

    relevant_fields = [item for item in fields if item.get("value") is not None and item.get("value") != []]
    if not relevant_fields:
        return None

    if len(relevant_fields) == 1:
        item = relevant_fields[0]
        label = FIELD_LABELS.get(item["key"], item["key"])
        return f"{prefix} {code or ''} {name}{period_text}: {label} = {_format_value(item.get('value'))}."

    summary = "; ".join(
        f"{FIELD_LABELS.get(item['key'], item['key'])} = {_format_value(item.get('value'))}"
        for item in relevant_fields
    )
    return f"{prefix} {code or ''} {name}{period_text}: {summary}."


def _format_monthly_breakdown(fields: List[Dict[str, Any]], *, name: str, code: str | None, period_text: str) -> str | None:
    monthly_chunks: List[str] = []
    for month_number in range(1, 13):
        key = PRODUCTION_MONTH_FIELDS[month_number]
        item = next((field for field in fields if field.get("key") == key), None)
        value = item.get("value") if item else None
        if value is not None:
            monthly_chunks.append(f"{MONTH_LABELS[month_number]} = {_format_value(value)}")

    if not monthly_chunks:
        return None

    return f"Obra {code or ''} {name}{period_text}: " + "; ".join(monthly_chunks) + "."


def _build_no_data_message(match: Dict[str, Any], *, module: str, parsed: Dict[str, Any]) -> str:
    prefix = "Licitacion" if module == "estudios" else "Obra"
    name = match.get("displayName") or parsed["reference"]
    code = match.get("primaryCode") or parsed["reference"]
    period_text = _build_period_text(parsed)

    if module == "produccion" and parsed.get("per_month"):
        return (
            f"{prefix} {code} {name}{period_text}: no hay produccion mensual cargada en AppRegenera para ese periodo."
        )

    if parsed.get("month") and module == "produccion":
        return (
            f"{prefix} {code} {name}{period_text}: no hay dato cargado para ese mes en AppRegenera."
        )

    requested_labels = [FIELD_LABELS.get(field, field) for field in parsed.get("fields") or []]
    labels_text = ", ".join(requested_labels[:4]) if requested_labels else "el dato solicitado"
    return f"{prefix} {code} {name}{period_text}: no hay datos disponibles para {labels_text}."


def _build_period_text(parsed: Dict[str, Any]) -> str:
    parts: List[str] = []
    if parsed.get("year"):
        parts.append(str(parsed["year"]))
    if parsed.get("cuatrimestre"):
        parts.append(f"C{parsed['cuatrimestre']}")
    if parsed.get("month"):
        parts.append(MONTH_LABELS.get(parsed["month"], f"mes {parsed['month']}"))
    return f" ({', '.join(parts)})" if parts else ""


def _format_value(value: Any) -> str:
    if value is None:
        return "sin dato"
    if isinstance(value, bool):
        return "si" if value else "no"
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
        year, month, day = value[:10].split("-")
        return f"{day}/{month}/{year}"
    if isinstance(value, list):
        return f"{len(value)} elementos"
    return str(value)
