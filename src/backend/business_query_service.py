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
    ("plan2026", "plan 2026", ("plan 2026",)),
    ("plan2027", "plan 2027", ("plan 2027",)),
    ("plan2028", "plan 2028", ("plan 2028",)),
    ("plan2029", "plan 2029", ("plan 2029",)),
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
        "importeContratadoMes": "importe contratado mensual",
        "importeContratadoPeriodo": "importe contratado",
        "produccionMes": "produccion mensual",
        "produccionPeriodo": "produccion",
        "produccionPrimerCuatrimestre": "produccion primer cuatrimestre",
        "produccionSegundoCuatrimestre": "produccion segundo cuatrimestre",
        "produccionEstimadaTercerCuatrimestre": "produccion estimada tercer cuatrimestre",
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

    licitaciones_hints = (
        "licitacion", "licitaciones", "oferta", "adjudicacion", "adjudicada",
        "fecha de presentacion", "fecha presentacion", "fecha de apertura", "numero de oferta",
    )
    produccion_hints = (
        "produccion", "obra", "control de obras", "cartera", "rentabilidad", "responsable",
        "cuatrimestre", "produccion enero", "produccion febrero", "produccion marzo",
        "codigo de obra", "codigo obra",
    )
    business_hints = (
        "importe contratado", "produccion", "cliente", "estado", "comentarios",
        "tipo obra", "numero proyecto", "numero oferta", "fecha adjudicacion",
        "fecha presentacion", "fecha apertura", "cartera", "responsable", "obra",
    )

    if any(hint in text for hint in licitaciones_hints):
        return "business_licitaciones"
    if any(hint in text for hint in produccion_hints):
        return "business_produccion"
    if "importe contratado" in text:
        return "business_licitaciones"
    if any(hint in text for hint in business_hints):
        return "business_licitaciones"
    return None


def _detect_explicit_scope(text: str) -> str | None:
    if any(hint in text for hint in ("licitacion", "licitaciones", "oferta")):
        return "estudios"
    if any(hint in text for hint in ("obra", "produccion", "control de obras", "codigo de obra", "codigo obra")):
        return "produccion"
    return None


def answer_business_question(
    question: str,
    *,
    user_token: str | None,
    preferred_route: str | None = None,
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
    preferred_module = "estudios" if (preferred_route or detect_business_route(question)) == "business_licitaciones" else "produccion"
    modules_to_try = _build_module_candidates(preferred_module, explicit_scope)

    try:
        first_not_found_message: str | None = None
        first_ambiguous_message: str | None = None

        for module in modules_to_try:
            route = "business_licitaciones" if module == "estudios" else "business_produccion"
            parsed = _parse_question(question, module=module)
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
            if not _result_has_relevant_fields(result):
                if explicit_scope is None and len(modules_to_try) > 1:
                    continue

            response_text = _format_business_response(result, module=module, parsed=parsed)
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

        fallback_message = first_ambiguous_message or first_not_found_message or "No he podido encontrar un dato de negocio que encaje con la pregunta."
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
            "route": route,
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


def _result_has_relevant_fields(result: Dict[str, Any]) -> bool:
    fields = result.get("fields") or []
    if not fields:
        return False
    for item in fields:
        if item.get("value") is not None:
            return True
    return False


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


def _parse_question(question: str, *, module: str) -> Dict[str, Any]:
    normalized = _normalize(question)
    year_match = re.search(r"\b(20\d{2})\b", normalized)
    cuatrimestre = _extract_cuatrimestre(normalized)
    month = _extract_month(normalized)
    reference = _extract_reference(question, normalized)
    fields = _detect_fields(normalized, module=module, year=int(year_match.group(1)) if year_match else None, cuatrimestre=cuatrimestre, month=month)
    primary_field = fields[0] if fields else None
    return {
        "reference": reference,
        "field": primary_field,
        "fields": fields,
        "year": int(year_match.group(1)) if year_match else None,
        "cuatrimestre": cuatrimestre,
        "month": month,
    }


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


def _extract_reference(original_question: str, normalized: str) -> str:
    explicit_code_match = re.search(r"\b(?:proyecto|obra|licitacion|oferta)\s+(\d{4,8})\b", normalized)
    if explicit_code_match:
        return explicit_code_match.group(1)

    numeric_tokens = re.findall(r"\b\d{4,8}\b", normalized)
    if numeric_tokens:
        non_year_tokens = [token for token in numeric_tokens if not re.fullmatch(r"20\d{2}", token)]
        if non_year_tokens:
            return non_year_tokens[0]
        return numeric_tokens[0]

    quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', original_question)
    if quoted:
        return quoted.group(1) or quoted.group(2)

    patterns = (
        r"(?:proyecto|obra|licitacion|oferta)\s+([a-z0-9][a-z0-9\s\-_/&]{2,})",
        r"(?:de|del)\s+([a-z0-9][a-z0-9\s\-_/&]{2,})",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = match.group(1).strip()
            value = re.split(r"\b(en|del|de|para|con|durante)\b", value)[0].strip()
            if value:
                return value
    return original_question.strip()


def _detect_fields(text: str, *, module: str, year: int | None, cuatrimestre: int | None, month: int | None) -> List[str]:
    fields: List[str] = []
    text_without_type_client = text.replace("tipo de cliente", " ").replace("tipo cliente", " ")

    if "importe contratado" in text or "importe adjudicado" in text:
        if module == "estudios":
            if month and year:
                fields.append("importeContratadoMes")
            elif year in {2026, 2027, 2028, 2029} and not cuatrimestre:
                fields.append(f"importeContratado{year}")
            elif any(token in text for token in ("previo", "anteriores", "anterior")):
                fields.append("importeContratadoPrevio")
            else:
                fields.append("importeContratado")
        else:
            fields.append("importeContratado")

    if "produccion" in text:
        if module == "estudios":
            if year in {2026, 2027, 2028, 2029} and not cuatrimestre:
                fields.append(f"produccion{year}")
            elif any(token in text for token in ("previa", "previo", "anteriores", "anterior")):
                fields.append("produccionPrevio")
            else:
                fields.append("produccion")
        elif month:
            fields.append("produccionMes")
        elif cuatrimestre:
            fields.append("produccionPeriodo")
        else:
            fields.append("produccion")

    if module == "estudios":
        for plan_year in (2026, 2027, 2028, 2029):
            if f"plan {plan_year}" in text:
                fields.append(f"plan{plan_year}")
    else:
        if "primer cuatrimestre" in text and "produccion" not in text:
            fields.append("produccionPrimerCuatrimestre")
        if "segundo cuatrimestre" in text and "produccion" not in text:
            fields.append("produccionSegundoCuatrimestre")
        if "tercer cuatrimestre" in text and "produccion" not in text:
            fields.append("produccionEstimadaTercerCuatrimestre")

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
        fields = ["importeContratado", "cliente", "estado"] if module == "estudios" else ["importeContratado", "nombreObra", "licitacionEstado"]
    return fields[:5]


def _format_business_response(result: Dict[str, Any], *, module: str, parsed: Dict[str, Any]) -> str:
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
        return "No se ha encontrado el dato solicitado en la entidad consultada."

    prefix = "Licitacion" if module == "estudios" else "Obra"
    name = entity.get("displayName") or entity.get("primaryCode") or parsed["reference"]
    code = entity.get("primaryCode")
    period_text = _build_period_text(parsed)

    if len(fields) == 1:
        item = fields[0]
        label = FIELD_LABELS.get(item["key"], item["key"])
        return f"{prefix} {code or ''} {name}{period_text}: {label} = {_format_value(item.get('value'))}."

    summary = "; ".join(
        f"{FIELD_LABELS.get(item['key'], item['key'])} = {_format_value(item.get('value'))}"
        for item in fields
    )
    return f"{prefix} {code or ''} {name}{period_text}: {summary}."


def _build_period_text(parsed: Dict[str, Any]) -> str:
    parts: List[str] = []
    if parsed.get("year"):
        parts.append(str(parsed["year"]))
    if parsed.get("cuatrimestre"):
        parts.append(f"C{parsed['cuatrimestre']}")
    if parsed.get("month"):
        parts.append(f"mes {parsed['month']}")
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
