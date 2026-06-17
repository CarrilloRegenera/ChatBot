import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List

from appregenera_client import AppRegeneraClientError, get_json, post_json
from appregenera_sql_service import (
    first_non_null,
    get_cierre_detail as sql_get_cierre_detail,
    get_licitacion_detail as sql_get_licitacion_detail,
    get_produccion_detail as sql_get_produccion_detail,
    is_available as appregenera_sql_available,
    parse_decimal,
    query_cierre_aggregate as sql_query_cierre_aggregate,
    query_licitaciones_aggregate as sql_query_licitaciones_aggregate,
    query_produccion_aggregate as sql_query_produccion_aggregate,
    search_licitaciones as sql_search_licitaciones,
    search_licitaciones_by_client_text as sql_search_licitaciones_by_client_text,
    search_produccion as sql_search_produccion,
)
from config import APPREGENERA_DEV_BYPASS_KEY
from ops_domain import OPS_DOCUMENTARY_HINTS, is_ops_standard_reference
from routing_signals import has_concrete_business_reference, has_explicit_technical_reference, is_mixed_scope_query
from business_query_schema import (
    BUSINESS_SCHEMA,
    CLOSURE_FIELD_HINTS,
    FIELD_LABELS,
    LICITACION_FIELD_SPECS,
    LICITACION_IMPORTE_YEARS,
    LICITACION_PLAN_YEARS,
    LICITACION_PRODUCCION_YEARS,
    MONTH_ALIASES,
    MONTH_LABELS,
    PRODUCTION_MONTH_FIELDS,
    PRODUCCION_AGGREGATE_KEYWORDS,
    PRODUCCION_FIELD_SPECS,
    PRODUCCION_LINKED_YEARS,
    PRODUCCION_ONLY_FALLBACK_BLOCK_FIELDS,
    RAW_BUSINESS_SCHEMA,
    SOURCE_INFO_PATTERNS,
    STUDIES_AGGREGATE_KEYWORDS,
    _FOLLOW_UP_PREFIXES,
    _normalize_static,
    _schema_aggregation_aliases,
    _schema_field_synonyms,
    _schema_module_hints,
    _schema_scope_aliases,
)


logger = logging.getLogger(__name__)

_DOCUMENTARY_HINTS = (
    "manual",
    "pdf",
    "documentacion",
    "documento",
    "normativa",
    "reglamento",
    "norma",
    "apartado",
    "seccion",
    "capitulo",
    "pagina",
    "pag ",
    "segun el manual",
    "segun el pdf",
    "grupo electrogeno",
    "grupos electrogenos",
    "electrogeno",
    "electrogenos",
    "himoinsa",
    "recepcion",
    "remolque",
    "enganche",
    "desenganche",
    "izado",
    "baja carga",
    "pruebas semanales",
    "arranque",
    "calefaccion",
    "mantenimiento",
    *OPS_DOCUMENTARY_HINTS,
)

_STRONG_BUSINESS_ROUTE_HINTS = (
    "control de produccion",
    "cierre",
    "cartera",
    "rentabilidad",
    "produccion estimada",
    "produccion marzo",
    "produccion abril",
    "pipeline",
    "backlog",
    "licitacion",
    "licitaciones",
    "estudio",
    "estudios",
    "oferta",
    "ofertas",
    "adjudicacion",
    "adjudicado",
    "adjudicada",
    "adjudicados",
    "adjudicadas",
    "plan ",
    "importe medio",
    "importe promedio",
    "importe contratado",
    "importe adjudicado",
    "probabilidad de adjudicacion",
    "situacion oferta",
    "periodo de ejecucion",
    "tipologia de obra",
    "codigo de obra",
    "codigo obra",
    "numero proyecto",
    "numero oferta",
)

_PRODUCCION_ENTITY_HINTS = (
    "proyecto",
    "proyectos",
    "obra",
    "obras",
)

_PRODUCCION_METRIC_HINTS = (
    "importe",
    "produccion",
    "cartera",
    "pendiente",
    "rentabilidad",
    "codigo de obra",
    "codigo obra",
    "cliente",
    "tipo obra",
    "top ",
    "ranking",
    "con mas",
    "con mayor",
)

_BUSINESS_PRODUCCION_LISTING_HINTS = (
    "proyecto",
    "proyectos",
    "obra",
    "obras",
    "en curso",
    "actualmente",
    "activa",
    "activas",
    "curso",
)

_BUSINESS_ESTUDIOS_LISTING_HINTS = (
    "licitacion",
    "licitaciones",
    "estudio",
    "estudios",
    "oferta",
    "ofertas",
    "recientes",
    "mas recientes",
    "relacionadas",
    "relacionados",
    "vinculadas",
    "vinculados",
    "top",
    "ranking",
)

_OPS_SPECIALIZED_DOCUMENTARY_HINTS = (
    "estudio de viabilidad",
    "viabilidad ops",
    "estudio tecnico economico",
    "estudio tecnico-economico",
    "demanda energetica",
    "solucion recomendada",
    "muelle objetivo",
    "buques objetivo",
    "potencia instalada necesaria",
    "anteproyecto",
    "anteproyecto ops",
    "infraestructura electrica",
    "memoria de infraestructura",
    "comunicacion de interes",
    "conformidad de estado miembro",
    "subvencion afif",
    "ayudas afif",
    "cef-t",
    "funding tender opportunities",
)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^\w\s/-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    typo_replacements = {
        "imorte": "importe",
        "impote": "importe",
        "liictacion": "licitacion",
        "liictaciones": "licitaciones",
        "liciitacion": "licitacion",
        "liciitaciones": "licitaciones",
    }
    typo_replacements.update({str(key): str(value) for key, value in (BUSINESS_SCHEMA.get("typos") or {}).items()})
    normalized = " ".join(typo_replacements.get(token, token) for token in normalized.split())
    return normalized


def _business_trace(
    *,
    path: str,
    module: str,
    route: str,
    parsed: Dict[str, Any] | None,
    outcome: str,
) -> Dict[str, Any]:
    parsed = parsed or {}
    aggregate = parsed.get("aggregate") or {}
    return {
        "path": path,
        "module": module,
        "route": route,
        "outcome": outcome,
        "intent": parsed.get("intent"),
        "reference": parsed.get("reference"),
        "metric": parsed.get("metric") or aggregate.get("metric"),
        "scope": parsed.get("scope") or aggregate.get("scope"),
        "filter_text": parsed.get("filter_text") or aggregate.get("filter_text"),
        "group_by": parsed.get("group_by"),
        "fields": parsed.get("fields") or [],
        "year": parsed.get("year"),
        "cuatrimestre": parsed.get("cuatrimestre"),
        "month": parsed.get("month"),
        "per_month": bool(parsed.get("per_month")),
        "per_year": bool(parsed.get("per_year")),
        "aggregate": {
            "kind": aggregate.get("kind"),
            "metric": aggregate.get("metric"),
            "scope": aggregate.get("scope"),
            "filter_text": aggregate.get("filter_text"),
            "top_n": aggregate.get("top_n"),
            "periodo": aggregate.get("periodo"),
            "area": aggregate.get("area"),
        } if aggregate else None,
    }


def _with_business_trace(result: Dict[str, Any], trace: Dict[str, Any]) -> Dict[str, Any]:
    result["trace"] = trace
    logger.info(
        "[BUSINESS] path=%s module=%s route=%s outcome=%s intent=%s ref=%s metric=%s fields=%s year=%s month=%s scope=%s group_by=%s filter=%s",
        trace.get("path"),
        trace.get("module"),
        trace.get("route"),
        trace.get("outcome"),
        trace.get("intent") or "-",
        trace.get("reference") or "-",
        trace.get("metric") or "-",
        ",".join(trace.get("fields") or []) or "-",
        trace.get("year") or "-",
        trace.get("month") or "-",
        trace.get("scope") or "-",
        trace.get("group_by") or "-",
        trace.get("filter_text") or "-",
    )
    return result


def _infer_metric_from_fields(fields: List[str]) -> str | None:
    if any(field.startswith("importeContratado") for field in fields):
        return "importecontratado"
    if any(field.startswith("plan20") for field in fields):
        return "pipeline"
    if any(field.startswith("produccion") or field.startswith("licitacionProduccion") for field in fields):
        return "produccion"
    if any(field.startswith("cartera") for field in fields):
        return "cartera"
    if any(field.startswith("pendiente") for field in fields):
        return "pendiente"
    return fields[0].lower() if fields else None


def _extract_business_intent(question: str, *, module: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    parsed = _parse_question(question, module=module, history=history)
    aggregate = parsed.get("aggregate") or {}
    fields = parsed.get("fields") or []

    if aggregate:
        intent = "ranking" if aggregate.get("kind") == "top" else "aggregate"
        metric = aggregate.get("metric")
    elif parsed.get("reference"):
        intent = "detail"
        metric = _infer_metric_from_fields(fields)
    else:
        intent = "unknown"
        metric = _infer_metric_from_fields(fields)

    group_by = None
    if parsed.get("per_year"):
        group_by = "year"
    elif parsed.get("per_month"):
        group_by = "month"

    parsed.update(
        {
            "intent": intent,
            "module": module,
            "metric": metric,
            "scope": aggregate.get("scope"),
            "filter_text": aggregate.get("filter_text") or parsed.get("filter_text"),
            "group_by": group_by,
        }
    )
    return parsed


def _looks_documentary_question(text: str) -> bool:
    return any(hint in text for hint in _DOCUMENTARY_HINTS)


def _looks_ops_specialized_documentary_question(text: str) -> bool:
    return any(hint in text for hint in _OPS_SPECIALIZED_DOCUMENTARY_HINTS)


def _has_strong_business_signal(text: str, reference: str | None) -> bool:
    if is_ops_standard_reference(reference, text):
        return False
    if _looks_ops_specialized_documentary_question(text):
        return False
    if reference:
        return True
    if any(hint in text for hint in _STRONG_BUSINESS_ROUTE_HINTS):
        return True
    if "cliente" in text and any(token in text for token in ("tiene", "tenemos", "es", "del cliente")):
        return True
    if any(token in text for token in ("cliente", "estado", "en curso", "actualmente")) and any(
        entity in text for entity in ("proyecto", "proyectos", "obra", "obras", "licitacion", "licitaciones", "estudio", "estudios")
    ):
        return True
    return False


def detect_business_route(question: str) -> str | None:
    text = _normalize(question)
    reference = _extract_reference(question, text)
    if is_ops_standard_reference(reference, text):
        reference = None
    strong_business_signal = _has_strong_business_signal(text, reference)
    if is_mixed_scope_query(text, has_business_signal=strong_business_signal) and not has_concrete_business_reference(text):
        return None
    if has_explicit_technical_reference(text) and not has_concrete_business_reference(text):
        return None
    if _looks_ops_specialized_documentary_question(text) and not reference:
        return None
    if _looks_documentary_question(text) and not strong_business_signal:
        return None
    explicit_scope = _detect_explicit_scope(text)
    if explicit_scope == "estudios":
        return "business_licitaciones"
    if explicit_scope == "produccion":
        return "business_produccion"
    if reference:
        reference_module = _detect_reference_module(reference)
        if reference_module == "estudios":
            return "business_licitaciones"
        if reference_module == "produccion":
            return "business_produccion"
        if re.fullmatch(r"\d{5}", reference):
            return "business_produccion"
    if (
        any(hint in text for hint in ("proyecto", "proyectos", "obra", "obras"))
        and any(hint in text for hint in ("cliente", "en curso", "actualmente", "estado", "importe", "produccion"))
        and "licitacion" not in text
        and "licitaciones" not in text
    ):
        return "business_produccion"
    if (
        any(hint in text for hint in ("licitacion", "licitaciones", "estudio", "estudios", "oferta", "ofertas"))
        and any(hint in text for hint in ("recientes", "mas recientes", "relacionadas", "relacionados", "vinculadas", "vinculados", "top", "ranking", "cliente"))
    ):
        return "business_licitaciones"

    if any(hint in text for hint in ("control de produccion", "cierre", "cartera", "rentabilidad", "produccion estimada", "produccion marzo", "produccion abril")):
        return "business_produccion"
    if any(hint in text for hint in _PRODUCCION_ENTITY_HINTS) and any(metric in text for metric in _PRODUCCION_METRIC_HINTS):
        return "business_produccion"
    if any(hint in text for hint in _schema_module_hints("cierre")):
        return "business_produccion"
    if any(hint in text for hint in _schema_module_hints("produccion") if hint not in {"obra", "obras"}):
        return "business_produccion"
    if any(hint in text for hint in ("pipeline", "backlog", "licitacion", "licitaciones", "estudio", "oferta", "adjudicacion", "adjudicado", "adjudicada", "adjudicados", "adjudicadas", "plan ")):
        return "business_licitaciones"
    if any(hint in text for hint in _schema_module_hints("estudios")):
        return "business_licitaciones"
    if any(hint in text for hint in ("cliente", "estado", "numero proyecto", "numero oferta", "tipo obra", "concurso")):
        return "business_licitaciones"
    if any(hint in text for hint in ("importe medio", "importe promedio", "importe contratado", "importe adjudicado", "probabilidad de adjudicacion", "situacion oferta", "periodo de ejecucion", "tipologia de obra")):
        return "business_licitaciones"
    return None


def _detect_explicit_scope(text: str) -> str | None:
    if any(hint in text for hint in ("licitacion", "licitaciones", "estudio", "estudios", "oferta", "pipeline", "backlog", "plan ")):
        return "estudios"
    if any(hint in text for hint in ("control de produccion", "cierre", "codigo de obra", "codigo obra", "cartera", "rentabilidad")):
        return "produccion"
    if re.search(r"\b(?:en|de|del)\s+produccion\b", text):
        return "produccion"
    if re.search(r"\bobras?\s+en\s+produccion\b", text):
        return "produccion"
    if _contains_cierre_hint(text):
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
        route = preferred_route or detect_business_route(question) or "business_licitaciones"
        return _with_business_trace({
            "response": (
                "Para consultar datos de Licitaciones o Produccion necesitas iniciar sesion corporativa con Microsoft. "
                "El modo documental puede seguir funcionando sin esa sesion."
            ),
            "route": "business_auth_required",
            "confidence": 1.0,
            "sources": [],
        }, _business_trace(path="auth", module="", route=route, parsed=None, outcome="auth_required"))

    if appregenera_sql_available():
        try:
            sql_result = _answer_business_question_sql(
                question,
                preferred_route=preferred_route,
                history=history or [],
            )
            if sql_result:
                return sql_result
        except Exception:
            logger.exception("Fallo en consulta SQL de AppRegenera; se intentara fallback HTTP")

    return _answer_business_question_http(
        question,
        user_token=user_token,
        preferred_route=preferred_route,
        history=history or [],
    )


def _answer_business_question_sql(
    question: str,
    *,
    preferred_route: str | None,
    history: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    normalized = _normalize(question)
    explicit_scope = _detect_explicit_scope(normalized)
    preferred_module = "estudios" if (preferred_route or detect_business_route(question)) == "business_licitaciones" else "produccion"
    reference_hint = _detect_reference_module(_extract_reference(question, normalized))
    if reference_hint == "estudios" and explicit_scope == "produccion":
        explicit_scope = None
    if _is_source_info_request(normalized):
        source_module = _infer_source_module_from_history(history, fallback=preferred_module)
        route = "business_licitaciones" if source_module == "estudios" else "business_produccion"
        parsed = _extract_business_intent(question, module=source_module, history=history or [])
        parsed["intent"] = "source_info"
        return _with_business_trace(
            _build_source_info_response(parsed, module=source_module, route=route),
            _business_trace(path="sql", module=source_module, route=route, parsed=parsed, outcome="source_info"),
        )
    modules_to_try = _build_module_candidates(preferred_module, explicit_scope, reference_hint)

    aggregate_fallback: Dict[str, Any] | None = None
    for index, module in enumerate(modules_to_try):
        route = "business_licitaciones" if module == "estudios" else "business_produccion"
        parsed = _extract_business_intent(question, module=module, history=history or [])

        if parsed.get("aggregate"):
            aggregate_result = _answer_aggregate_sql(parsed, module=module, route=route)
            if aggregate_result:
                traced_result = _with_business_trace(
                    aggregate_result,
                    _business_trace(path="sql", module=module, route=route, parsed=parsed, outcome="aggregate"),
                )
                if (
                    explicit_scope is None
                    and index < len(modules_to_try) - 1
                    and traced_result.get("is_zero_value")
                ):
                    aggregate_fallback = traced_result
                    continue
                return traced_result

        yearly_result = _answer_yearly_aggregate_sql(parsed, module=module, route=route)
        if yearly_result:
            return _with_business_trace(
                yearly_result,
                _business_trace(path="sql", module=module, route=route, parsed=parsed, outcome="yearly_aggregate"),
            )

        if module == "estudios":
            filtered_listing_result = _answer_estudios_filtered_listing_sql(parsed, route=route)
            if filtered_listing_result:
                return _with_business_trace(
                    filtered_listing_result,
                    _business_trace(path="sql", module=module, route=route, parsed=parsed, outcome="filtered_listing"),
                )
            detail_result = _answer_estudios_detail_sql(parsed, route=route)
        else:
            filtered_listing_result = _answer_produccion_filtered_listing_sql(parsed, route=route)
            if filtered_listing_result:
                return _with_business_trace(
                    filtered_listing_result,
                    _business_trace(path="sql", module=module, route=route, parsed=parsed, outcome="filtered_listing"),
                )
            detail_result = _answer_produccion_detail_sql(parsed, route=route, normalized_question=normalized)

        if detail_result:
            if (
                explicit_scope is None
                and index < len(modules_to_try) - 1
                and not detail_result.get("has_data", True)
                and not _should_block_cross_module_detail_fallback(module, parsed)
            ):
                continue
            return _with_business_trace(
                detail_result,
                _business_trace(path="sql", module=module, route=route, parsed=parsed, outcome="detail"),
            )

    return aggregate_fallback


def _answer_yearly_aggregate_sql(parsed: Dict[str, Any], *, module: str, route: str) -> Dict[str, Any] | None:
    if module != "estudios" or parsed.get("reference") or parsed.get("group_by") != "year":
        return None

    metric = parsed.get("metric")
    if metric not in {"pipeline", "importecontratado", "produccion"}:
        return None

    if metric == "pipeline":
        years_to_query = LICITACION_PLAN_YEARS
    elif metric == "importecontratado":
        years_to_query = LICITACION_IMPORTE_YEARS
    else:
        years_to_query = LICITACION_PRODUCCION_YEARS or LICITACION_PLAN_YEARS
    if not years_to_query:
        return None

    values: List[str] = []
    is_zero_value = True
    for year in years_to_query:
        rows = sql_query_licitaciones_aggregate(
            select_field=metric,
            agg="sum",
            top=1,
            year=year,
            scope=parsed.get("scope"),
            free_text=parsed.get("filter_text"),
        )
        value = rows[0].get("Valor") if rows else 0.0
        if (parse_decimal(value) or 0.0) != 0.0:
            is_zero_value = False
        values.append(f"{year} = {_format_value(value)}")

    filter_copy = f" para {parsed.get('filter_text')}" if parsed.get("filter_text") else ""
    scope_copy = f" en {parsed.get('scope')}" if parsed.get("scope") else ""
    return {
        "response": f"{_aggregate_label(metric, None).capitalize()} por ano{scope_copy}{filter_copy}: " + "; ".join(values) + ".",
        "route": route,
        "confidence": 1.0,
        "sources": [{"source": "AppRegenera SQL", "module": module}],
        "is_zero_value": is_zero_value,
    }


def _answer_aggregate_sql(parsed: Dict[str, Any], *, module: str, route: str) -> Dict[str, Any] | None:
    aggregate = parsed.get("aggregate") or {}
    metric = aggregate.get("metric")
    if not metric:
        return None

    aggregate_kind = aggregate.get("kind", "top")
    if metric.startswith("cierre:"):
        rows = sql_query_cierre_aggregate(
            campo="" if aggregate_kind == "count" else metric.split(":", 1)[1],
            agg=aggregate_kind,
            top=aggregate.get("top_n", 1),
            periodo=aggregate.get("periodo"),
            area=aggregate.get("area"),
            free_text=aggregate.get("filter_text"),
            order=aggregate.get("order"),
        )
    elif module == "produccion":
        rows = sql_query_produccion_aggregate(
            select_field=metric,
            agg=aggregate_kind,
            top=aggregate.get("top_n", 1),
            free_text=aggregate.get("filter_text"),
            order=aggregate.get("order"),
        )
    else:
        rows = sql_query_licitaciones_aggregate(
            select_field=metric,
            agg=aggregate_kind,
            top=aggregate.get("top_n", 1),
            year=parsed.get("year"),
            scope=aggregate.get("scope"),
            free_text=aggregate.get("filter_text"),
            order=aggregate.get("order"),
        )
    if not rows:
        return {
            "response": "No he encontrado datos agregados que encajen con esa consulta.",
            "route": route,
            "confidence": 0.95,
            "sources": [],
        }

    label = _aggregate_label(metric, parsed.get("year"))
    filter_text = aggregate.get("filter_text")
    scope_label = aggregate.get("scope")
    period_text = _build_period_text(parsed)

    if aggregate_kind in {"sum", "avg", "count"}:
        value = rows[0].get("Valor")
        scope_copy = f" en {scope_label}" if scope_label else ""
        filter_copy = f" para {filter_text}" if filter_text else ""
        top_copy = ""
        if aggregate_kind == "avg" and int(aggregate.get("top_n") or 1) > 1:
            top_copy = (
                f" de las ultimas {aggregate.get('top_n')}"
                if aggregate.get("order") == "latest"
                else f" de las {aggregate.get('top_n')} con mayor {label}"
            )
        numeric_value = parse_decimal(value) or 0.0
        aggregate_copy = "Numero de" if aggregate_kind == "count" else ("Media de" if aggregate_kind == "avg" else "Total de")
        formatted_value = str(int(numeric_value)) if aggregate_kind == "count" else _format_value(value)
        return {
            "response": f"{aggregate_copy} {label}{top_copy}{period_text}{scope_copy}{filter_copy}: {formatted_value}.",
            "route": route,
            "confidence": 1.0,
            "sources": [{"source": "AppRegenera SQL", "module": module}],
            "is_zero_value": numeric_value == 0.0,
        }

    top_rows = rows[: max(1, min(int(aggregate.get("top_n", 1) or 1), 10))]
    if all((parse_decimal(row.get("Valor")) or 0.0) == 0.0 for row in top_rows):
        return {
            "response": f"No hay valores positivos para {label}{period_text}.",
            "route": route,
            "confidence": 1.0,
            "sources": [{"source": "AppRegenera SQL", "module": module}],
            "is_zero_value": True,
        }
    top_rows = [row for row in top_rows if (parse_decimal(row.get("Valor")) or 0.0) != 0.0]

    if len(top_rows) == 1:
        row = top_rows[0]
        code = row.get("NumeroProyecto") or row.get("NumeroOferta") or row.get("CodigoObra") or "-"
        name = row.get("Obra") or row.get("NombreObra") or row.get("Nombre") or row.get("Cliente") or "-"
        entity_name = "la licitacion" if module == "estudios" else "la obra"
        return {
            "response": f"{entity_name.capitalize()} con mayor {label}{period_text} es {code} - {name}: {_format_value(row.get('Valor'))}.",
            "route": route,
            "confidence": 1.0,
            "sources": [{"source": "AppRegenera SQL", "module": module, "code": code, "entity": name}],
            "is_zero_value": (parse_decimal(row.get("Valor")) or 0.0) == 0.0,
        }

    lines = []
    for index, row in enumerate(top_rows, start=1):
        code = row.get("NumeroProyecto") or row.get("NumeroOferta") or row.get("CodigoObra") or "-"
        name = row.get("Obra") or row.get("NombreObra") or row.get("Nombre") or row.get("Cliente") or "-"
        lines.append(f"{index}. {code} - {name}: {_format_value(row.get('Valor'))}")
    return {
        "response": f"Top {len(top_rows)} por {label}{period_text}: " + " | ".join(lines),
        "route": route,
        "confidence": 1.0,
        "sources": [{"source": "AppRegenera SQL", "module": module}],
        "is_zero_value": False,
    }


def _answer_estudios_detail_sql(parsed: Dict[str, Any], *, route: str) -> Dict[str, Any] | None:
    reference = parsed.get("reference")
    if not reference:
        return _answer_estudios_filtered_listing_sql(parsed, route=route)

    matches = sql_search_licitaciones(reference, take=8)
    match = _pick_best_licitacion_match(matches, reference)
    if match == "ambiguous":
        options = "; ".join(_match_label(item, module="estudios") for item in matches[:5])
        return {
            "response": f"He encontrado varias coincidencias en Licitaciones: {options}. Indica un codigo mas concreto.",
            "route": route,
            "confidence": 0.95,
            "sources": [],
        }
    if not match:
        return None

    detail = sql_get_licitacion_detail(str(match["Id"]))
    if not detail:
        return None

    result = _build_estudios_result(detail, parsed)
    response = _format_business_response(result, module="estudios", parsed=parsed)
    if not response:
        response = _build_no_data_message(
            {"primaryCode": detail.get("NumeroProyecto") or detail.get("NumeroOferta"), "displayName": detail.get("Obra")},
            module="estudios",
            parsed=parsed,
        )
        has_data = False
    else:
        has_data = True

    return {
        "response": response,
        "route": route,
        "confidence": 1.0,
        "has_data": has_data,
        "sources": [
            {
                "source": "AppRegenera SQL",
                "module": "estudios",
                "entity": detail.get("Obra") or detail.get("Cliente") or reference,
                "code": detail.get("NumeroProyecto") or detail.get("NumeroOferta") or reference,
            }
        ],
    }


def _answer_produccion_detail_sql(parsed: Dict[str, Any], *, route: str, normalized_question: str) -> Dict[str, Any] | None:
    reference = parsed.get("reference")
    if not reference:
        return None

    if "cierre" in normalized_question or "cierre" in (parsed.get("fields") or []) or _contains_cierre_hint(normalized_question):
        cierre_result = _answer_cierre_sql(parsed, route=route)
        if cierre_result:
            return cierre_result

    matches = sql_search_produccion(reference, take=8)
    match = _pick_best_produccion_match(matches, reference)
    if match == "ambiguous":
        options = "; ".join(_match_label(item, module="produccion") for item in matches[:5])
        return {
            "response": f"He encontrado varias coincidencias en Produccion: {options}. Indica un codigo mas concreto.",
            "route": route,
            "confidence": 0.95,
            "sources": [],
        }
    if not match:
        return None

    detail = sql_get_produccion_detail(str(match["Id"]), year=parsed.get("year"))
    if not detail:
        return None

    result = _build_produccion_result(detail, parsed)
    response = _format_business_response(result, module="produccion", parsed=parsed)
    if not response:
        response = _build_no_data_message(
            {"primaryCode": detail.get("CodigoObra") or detail.get("LicitacionNumeroProyecto"), "displayName": detail.get("NombreObra")},
            module="produccion",
            parsed=parsed,
        )
        has_data = False
    else:
        has_data = True

    return {
        "response": response,
        "route": route,
        "confidence": 1.0,
        "has_data": has_data,
        "sources": [
            {
                "source": "AppRegenera SQL",
                "module": "produccion",
                "entity": detail.get("NombreObra") or reference,
                "code": detail.get("CodigoObra") or detail.get("LicitacionNumeroProyecto") or reference,
            }
        ],
    }


def _answer_produccion_filtered_listing_sql(parsed: Dict[str, Any], *, route: str) -> Dict[str, Any] | None:
    question_text = _normalize(parsed.get("question") or "")
    filter_text = str(parsed.get("filter_text") or "").strip()
    listing_follow_up = bool(parsed.get("listing_follow_up"))
    listing_context = parsed.get("listing_context") or {}
    inherited_active_listing = listing_follow_up and bool(listing_context.get("active"))
    if not filter_text:
        return None
    if not listing_follow_up and not any(token in question_text for token in ("proyecto", "proyectos", "obra", "obras")):
        return None
    if not (
        _looks_like_filtered_listing_request(question_text)
        or _looks_like_active_production_listing_query(question_text)
        or _looks_like_ranked_listing_query(question_text)
        or inherited_active_listing
        or listing_follow_up
    ):
        return None

    exact_client_target = _is_exact_client_target_query(question_text)
    matches = sql_search_produccion(filter_text, take=100)
    if not matches:
        return {
            "response": "No he encontrado proyectos de produccion que encajen con ese filtro.",
            "route": route,
            "confidence": 0.95,
            "sources": [],
        }

    if _is_explicit_client_field_filter(question_text) or exact_client_target:
        matches = _apply_client_filter(matches, filter_text, exact_client_target=exact_client_target)

    matches = _sort_produccion_listing_matches(matches, question_text=question_text)
    if not matches:
        return {
            "response": "No he encontrado proyectos en produccion que encajen con ese filtro.",
            "route": route,
            "confidence": 0.95,
            "sources": [],
        }

    top_matches = matches[:5]
    lines = []
    for index, item in enumerate(top_matches, start=1):
        code = item.get("CodigoObra") or item.get("NumeroProyecto") or item.get("NumeroOferta") or "-"
        obra = item.get("NombreObra") or "-"
        cliente = item.get("Cliente") or "-"
        estado = item.get("Estado") or ("En curso" if item.get("Finalizada") in (False, 0, None) else "Finalizada")
        lines.append(f"{index}. {code} - {obra}: cliente = {cliente}; estado = {estado}")

    qualifier = "en curso" if (_looks_like_active_production_listing_query(question_text) or inherited_active_listing) else "filtrados"
    if _looks_like_ranked_listing_query(question_text):
        qualifier = "top"
    return {
        "response": f"Proyectos {qualifier} relacionados con '{filter_text}': " + " | ".join(lines),
        "route": route,
        "confidence": 1.0,
        "sources": [{"source": "AppRegenera SQL", "module": "produccion"}],
    }


def _answer_estudios_filtered_listing_sql(parsed: Dict[str, Any], *, route: str) -> Dict[str, Any] | None:
    question_text = _normalize(parsed.get("question") or "")
    filter_text = str(parsed.get("filter_text") or "").strip()
    listing_follow_up = bool(parsed.get("listing_follow_up"))
    listing_context = parsed.get("listing_context") or {}
    if not filter_text:
        return None
    if not listing_follow_up and not any(
        token in question_text
        for token in ("licitacion", "licitaciones", "estudio", "estudios", "oferta", "ofertas", "proyecto", "proyectos", "obra", "obras")
    ):
        return None
    explicit_client_filter = _is_explicit_client_field_filter(question_text)
    exact_client_target = _is_exact_client_target_query(question_text)
    is_client_filter_query = explicit_client_filter and any(
        token in question_text for token in ("contiene", "contienen", "incluye", "incluyen", "palabra", "texto", "cliente")
    )
    is_backlog_listing_query = (
        (_looks_like_filtered_listing_request(question_text) or listing_follow_up)
        and any(token in question_text for token in ("adjudicada", "adjudicadas", "adjudicado", "adjudicados", "backlog", "ganada", "ganadas"))
    ) or (listing_follow_up and bool(listing_context.get("backlog")))
    is_related_listing_query = (
        (_looks_like_filtered_listing_request(question_text) or listing_follow_up)
        and any(token in question_text for token in ("relacionadas", "relacionados", "vinculadas", "vinculados", "recientes", "mas recientes", "top", "ranking"))
    ) or (listing_follow_up and bool(listing_context.get("recent") or listing_context.get("ranked")))
    if not is_client_filter_query and not is_backlog_listing_query and not is_related_listing_query and not exact_client_target and not listing_follow_up:
        return None

    if explicit_client_filter or exact_client_target:
        matches = sql_search_licitaciones_by_client_text(filter_text, take=100)
    else:
        matches = sql_search_licitaciones(filter_text, take=100)
    if not matches:
        return {
            "response": "No he encontrado licitaciones que encajen con ese filtro.",
            "route": route,
            "confidence": 0.95,
            "sources": [],
        }

    if explicit_client_filter or exact_client_target:
        matches = _apply_client_filter(matches, filter_text, exact_client_target=exact_client_target and not _is_client_contains_query(question_text))
    if is_backlog_listing_query:
        matches = [
            item
            for item in matches
            if _normalize(str(item.get("Estado") or "")).startswith(("adjudicada", "completada"))
        ]
    if not matches:
        if is_backlog_listing_query:
            return {
                "response": "No he encontrado licitaciones adjudicadas que encajen con ese filtro.",
                "route": route,
                "confidence": 0.95,
                "sources": [],
            }
        return {
            "response": "No he encontrado licitaciones cuyo cliente contenga ese texto.",
            "route": route,
            "confidence": 0.95,
            "sources": [],
        }

    matches = _sort_estudios_listing_matches(matches, question_text=question_text)
    top_matches = matches[:5]
    lines = []
    for index, item in enumerate(top_matches, start=1):
        code = item.get("NumeroProyecto") or item.get("NumeroOferta") or "-"
        obra = item.get("Obra") or "-"
        cliente = item.get("Cliente") or "-"
        if is_backlog_listing_query or is_related_listing_query or _looks_like_recent_listing_query(question_text):
            estado = item.get("Estado") or "-"
            lines.append(f"{index}. {code} - {obra}: cliente = {cliente}; estado = {estado}")
        else:
            lines.append(f"{index}. {code} - {obra}: cliente = {cliente}")
    if is_backlog_listing_query:
        return {
            "response": f"Licitaciones adjudicadas que encajan con '{filter_text}': " + " | ".join(lines),
            "route": route,
            "confidence": 1.0,
            "sources": [{"source": "AppRegenera SQL", "module": "estudios"}],
        }
    if is_related_listing_query:
        qualifier = "mas recientes" if _looks_like_recent_listing_query(question_text) else "top"
        return {
            "response": f"Licitaciones {qualifier} relacionadas con '{filter_text}': " + " | ".join(lines),
            "route": route,
            "confidence": 1.0,
            "sources": [{"source": "AppRegenera SQL", "module": "estudios"}],
        }
    return {
        "response": f"Licitaciones que contienen '{filter_text}'{(' en cliente' if explicit_client_filter else '')}: " + " | ".join(lines),
        "route": route,
        "confidence": 1.0,
        "sources": [{"source": "AppRegenera SQL", "module": "estudios"}],
    }


def _answer_cierre_sql(parsed: Dict[str, Any], *, route: str) -> Dict[str, Any] | None:
    reference = parsed.get("reference")
    if not reference:
        return None

    cierre = sql_get_cierre_detail(reference)
    if not cierre:
        return None

    matches = _match_cierre_fields(cierre, parsed)
    if not matches:
        resumen = _summarize_cierre(cierre)
        if not resumen:
            return None
        return {
            "response": resumen,
            "route": route,
            "confidence": 1.0,
            "sources": [{"source": "AppRegenera SQL", "module": "cierre", "code": reference}],
        }

    fragments = [f"{item['label']} = {_format_value(item['value'])}" for item in matches]
    titulo = cierre.get("Nombre") or cierre.get("Numero") or reference
    periodo = cierre.get("Periodo")
    period_copy = f" ({periodo})" if periodo else ""
    return {
        "response": f"Cierre de {titulo}{period_copy}: " + "; ".join(fragments) + ".",
        "route": route,
        "confidence": 1.0,
        "sources": [{"source": "AppRegenera SQL", "module": "cierre", "code": reference}],
    }


def _answer_business_question_http(
    question: str,
    *,
    user_token: str | None,
    preferred_route: str | None = None,
    history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized = _normalize(question)
    explicit_scope = _detect_explicit_scope(normalized)
    preferred_module = "estudios" if (preferred_route or detect_business_route(question)) == "business_licitaciones" else "produccion"
    reference_hint = _detect_reference_module(_extract_reference(question, normalized))
    modules_to_try = _build_module_candidates(preferred_module, explicit_scope, reference_hint)

    try:
        first_not_found_message: str | None = None
        first_ambiguous_message: str | None = None
        first_no_data_message: str | None = None

        for module in modules_to_try:
            route = "business_licitaciones" if module == "estudios" else "business_produccion"
            parsed = _extract_business_intent(question, module=module, history=history or [])
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
                exact = _pick_exact_http_match(matches, parsed["reference"])
                if exact:
                    matches = [exact]
                else:
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

            return _with_business_trace({
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
            }, _business_trace(path="http", module=module, route=route, parsed=parsed, outcome="detail"))

        fallback_message = (
            first_ambiguous_message
            or first_no_data_message
            or first_not_found_message
            or "No he podido encontrar un dato de negocio que encaje con la pregunta."
        )
        fallback_route = preferred_route or detect_business_route(question) or "business_licitaciones"
        fallback_module = "estudios" if fallback_route == "business_licitaciones" else "produccion"
        fallback_parsed = _extract_business_intent(question, module=fallback_module, history=history or [])
        return _with_business_trace({
            "response": fallback_message,
            "route": fallback_route,
            "confidence": 0.95,
            "sources": [],
        }, _business_trace(path="http", module=fallback_module, route=fallback_route, parsed=fallback_parsed, outcome="fallback"))
    except AppRegeneraClientError as exc:
        if exc.status_code == 403:
            message = "No tienes permisos para consultar ese dato en AppRegenera."
        else:
            message = f"No he podido consultar AppRegenera: {exc}"
        error_route = preferred_route or detect_business_route(question) or "business_licitaciones"
        error_module = "estudios" if error_route == "business_licitaciones" else "produccion"
        error_parsed = _extract_business_intent(question, module=error_module, history=history or [])
        return _with_business_trace({
            "response": message,
            "route": error_route,
            "confidence": 0.0,
            "sources": [],
        }, _business_trace(path="http", module=error_module, route=error_route, parsed=error_parsed, outcome="error"))


def _build_module_candidates(preferred_module: str, explicit_scope: str | None, reference_hint: str | None) -> List[str]:
    if explicit_scope == "estudios":
        return ["estudios"]
    if explicit_scope == "produccion":
        return ["produccion"]
    if reference_hint == "estudios":
        return ["estudios", "produccion"]
    if reference_hint == "produccion":
        return ["produccion", "estudios"]
    other = "produccion" if preferred_module == "estudios" else "estudios"
    return [preferred_module, other]


def _parse_question(question: str, *, module: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = _normalize(question)
    history_context = _extract_history_context(history)
    explicit_reference = _extract_reference(question, normalized)
    filter_text = _extract_filter_text(question, normalized)
    listing_context = _extract_history_listing_context(history, module=module)
    listing_follow_up = bool(
        _looks_like_follow_up(normalized)
        and _looks_like_listing_refinement(normalized, module=module)
        and listing_context
    )
    reference = explicit_reference or _resolve_reference(question, normalized, history)
    if listing_follow_up and explicit_reference is None:
        reference = None
        if not filter_text:
            filter_text = listing_context.get("filter_text")
    years = _extract_explicit_years(normalized, reference)
    year_text = _strip_reference_for_year_detection(normalized, reference)
    year_match = re.search(r"\b(20\d{2})\b", year_text)
    year = int(year_match.group(1)) if year_match else None
    if year is None and _looks_like_follow_up(normalized):
        year = history_context.get("year")
    if year is None and len(years) == 1:
        year = years[0]
    cuatrimestre = _extract_cuatrimestre(normalized)
    month = _extract_month(normalized)
    if month is None and _looks_like_follow_up(normalized):
        month = history_context.get("month")
    per_month = _is_per_month_request(normalized)
    per_year = _is_per_year_request(normalized) or len(years) > 1
    fields = _detect_fields(
        normalized,
        module=module,
        year=year,
        years=years,
        cuatrimestre=cuatrimestre,
        month=month,
        per_month=per_month,
        per_year=per_year,
    )
    inherited_fields = _inherit_follow_up_fields(
        normalized,
        module=module,
        history=history,
        year=year,
        month=month,
        cuatrimestre=cuatrimestre,
    )
    if inherited_fields:
        fields = inherited_fields
    reference_follow_up_fields = _inherit_reference_follow_up_fields(
        normalized,
        module=module,
        history=history,
        reference=reference,
        current_fields=fields,
        year=year,
        month=month,
        cuatrimestre=cuatrimestre,
    )
    if reference_follow_up_fields:
        fields = reference_follow_up_fields
    aggregate = _detect_aggregate(question, normalized, module=module, fields=fields, year=year, reference=reference)
    expected_client = _extract_expected_client(question, normalized)
    return {
        "question": question,
        "reference": reference,
        "fields": fields,
        "filter_text": filter_text,
        "year": year,
        "years": years,
        "cuatrimestre": cuatrimestre,
        "month": month,
        "per_month": per_month,
        "per_year": per_year,
        "aggregate": aggregate,
        "expected_client": expected_client,
        "listing_follow_up": listing_follow_up,
        "listing_context": listing_context,
    }


def _extract_history_context(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in reversed(history or []):
        question = str(item.get("question") or "")
        normalized = _normalize(question)
        year_match = re.search(r"\b(20\d{2})\b", normalized)
        month = _extract_month(normalized)
        reference = _extract_reference(question, normalized)
        if reference or year_match or month:
            return {
                "reference": reference,
                "year": int(year_match.group(1)) if year_match else None,
                "month": month,
            }
    return {}


def _extract_history_listing_context(history: List[Dict[str, Any]], *, module: str) -> Dict[str, Any]:
    entity_tokens = ("proyecto", "proyectos", "obra", "obras") if module == "produccion" else (
        "licitacion",
        "licitaciones",
        "estudio",
        "estudios",
        "oferta",
        "ofertas",
        "proyecto",
        "proyectos",
        "obra",
        "obras",
    )
    for item in reversed(history or []):
        history_question = str(item.get("question") or "")
        history_normalized = _normalize(history_question)
        if not any(token in history_normalized for token in entity_tokens):
            continue
        if module == "produccion":
            is_listing = (
                _looks_like_filtered_listing_request(history_normalized)
                or _looks_like_active_production_listing_query(history_normalized)
                or _looks_like_ranked_listing_query(history_normalized)
            )
        else:
            is_listing = (
                _looks_like_filtered_listing_request(history_normalized)
                or _looks_like_recent_listing_query(history_normalized)
                or _looks_like_ranked_listing_query(history_normalized)
            )
        if not is_listing:
            continue
        filter_text = _extract_filter_text(history_question, history_normalized)
        return {
            "question": history_question,
            "filter_text": filter_text,
            "active": _looks_like_active_production_listing_query(history_normalized),
            "backlog": any(
                token in history_normalized
                for token in ("adjudicada", "adjudicadas", "adjudicado", "adjudicados", "backlog", "ganada", "ganadas")
            ),
            "recent": _looks_like_recent_listing_query(history_normalized),
            "ranked": _looks_like_ranked_listing_query(history_normalized),
        }
    return {}


def _looks_like_listing_refinement(text: str, *, module: str) -> bool:
    if module == "produccion":
        return any(
            token in text
            for token in (
                " en curso",
                " actualmente",
                " activa",
                " activas",
                " cliente",
                " para ",
                " top ",
                " ranking",
                " relacionados",
                " relacionadas",
                " vinculados",
                " vinculadas",
            )
        )
    return any(
        token in text
        for token in (
            " adjudicad",
            " ganad",
            " backlog",
            " cliente",
            " para ",
            " recientes",
            " ultimas",
            " ultimos",
            " top ",
            " ranking",
            " relacionados",
            " relacionadas",
            " vinculados",
            " vinculadas",
            " solo ",
        )
    )


def _looks_like_follow_up(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in _FOLLOW_UP_PREFIXES) or len(text.split()) <= 4


def _resolve_reference(original_question: str, normalized: str, history: List[Dict[str, Any]]) -> str | None:
    reference = _extract_reference(original_question, normalized)
    if reference:
        return reference

    if not (
        _looks_like_follow_up(normalized)
        or _mentions_previous_reference(normalized)
        or _can_inherit_reference_from_context(normalized)
    ):
        return None

    for item in reversed(history or []):
        history_question = str(item.get("question") or "")
        history_normalized = _normalize(history_question)
        history_reference = _extract_reference(history_question, history_normalized)
        if history_reference:
            return history_reference
    return None


def _mentions_previous_reference(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            " este proyecto",
            " esta licitacion",
            " este estudio",
            " esta obra",
            "este proyecto ",
            "esta licitacion ",
            "esta obra ",
            " su ",
            " sus ",
            "este ",
            "esta ",
        )
    )


def _can_inherit_reference_from_context(text: str) -> bool:
    if any(
        token in text
        for token in (
            "top ",
            "ranking",
            "con mas",
            "con mayor",
            "media de ",
            "promedio de ",
            "numero de ",
            "cuantas ",
            "cuantos ",
            "total de ",
            "suma de ",
        )
    ):
        return False
    if any(token in text for token in ("licitaciones", "obras", "proyectos", "estudios", "cierres")):
        return False
    return any(
        token in text
        for token in (
            " importe ",
            " importe contratado",
            " produccion",
            " backlog",
            " pipeline",
            " cliente",
            " concurso",
            " fecha ",
            " tipologia",
            " tipo ",
            " n oferta",
            " numero oferta",
            " numero de oferta",
            " estado",
            " apertura",
            " adjudicacion",
            " presentacion",
            " tiene ",
        )
    )


def _extract_reference(original_question: str, normalized: str) -> str | None:
    reference_patterns = (
        r"\b(est[-\s]?\d{1,4}[-\s]?20\d{2})\b",
        r"\b([a-z]{2,6}-\d{1,5}-20\d{2})\b",
        r"\b(?:proyecto|obra|licitacion|licitacion|oferta|estudio)\s+([a-z0-9-]*\d[a-z0-9-]{3,29})\b",
    )
    for pattern in reference_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper().replace(" ", "-")

    numeric_tokens = re.findall(r"\b\d{4,8}\b", normalized)
    non_year_tokens = [token for token in numeric_tokens if not re.fullmatch(r"20\d{2}", token)]
    if non_year_tokens:
        return non_year_tokens[0]

    quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', original_question)
    if quoted:
        value = (quoted.group(1) or quoted.group(2) or "").strip()
        return value or None
    return None


def _strip_reference_for_year_detection(normalized: str, reference: str | None) -> str:
    if not reference:
        return normalized
    ref = _normalize(reference).replace(" ", "-")
    return re.sub(rf"\b{re.escape(ref)}\b", " ", normalized)


def _extract_explicit_years(text: str, reference: str | None) -> List[int]:
    text_without_reference = _strip_reference_for_year_detection(text, reference)
    years = [int(match) for match in re.findall(r"\b(20\d{2})\b", text_without_reference)]
    return list(dict.fromkeys(years))


def _inherit_follow_up_fields(
    text: str,
    *,
    module: str,
    history: List[Dict[str, Any]],
    year: int | None,
    month: int | None,
    cuatrimestre: int | None,
) -> List[str] | None:
    if not re.fullmatch(r"(?:y\s+)?20\d{2}\??", text):
        return None
    for index in range(len(history or []) - 1, -1, -1):
        previous_question = str((history or [])[index].get("question") or "").strip()
        if not previous_question:
            continue
        previous_parsed = _parse_question(previous_question, module=module, history=(history or [])[:index])
        previous_fields = previous_parsed.get("fields") or []
        narrowed_fields = _narrow_fields_to_period(
            previous_fields,
            module=module,
            year=year,
            month=month,
            cuatrimestre=cuatrimestre,
        )
        if narrowed_fields:
            return narrowed_fields
    return None


def _inherit_reference_follow_up_fields(
    text: str,
    *,
    module: str,
    history: List[Dict[str, Any]],
    reference: str | None,
    current_fields: List[str],
    year: int | None,
    month: int | None,
    cuatrimestre: int | None,
) -> List[str] | None:
    if not reference or not _looks_like_follow_up(text):
        return None

    default_fields = ["importeContratado", "cliente", "estado"] if module == "estudios" else ["nombreObra", "importeContratado"]
    if current_fields != default_fields:
        return None

    for index in range(len(history or []) - 1, -1, -1):
        previous_question = str((history or [])[index].get("question") or "").strip()
        if not previous_question:
            continue
        previous_parsed = _parse_question(previous_question, module=module, history=(history or [])[:index])
        previous_fields = previous_parsed.get("fields") or []
        if not previous_fields:
            continue
        narrowed_fields = _narrow_fields_to_period(
            previous_fields,
            module=module,
            year=year,
            month=month,
            cuatrimestre=cuatrimestre,
        )
        inherited = narrowed_fields or previous_fields
        if inherited:
            return _dedupe(inherited)
    return None


def _narrow_fields_to_period(
    fields: List[str],
    *,
    module: str,
    year: int | None,
    month: int | None,
    cuatrimestre: int | None,
) -> List[str]:
    if month or cuatrimestre:
        return []
    if not year:
        return []

    narrowed = [
        field
        for field in fields
        if field.endswith(str(year))
        or field == f"importeContratado{year}"
        or field == f"produccion{year}"
        or field == f"plan{year}"
        or field == f"licitacionProduccion{year}"
    ]
    if narrowed:
        return _dedupe(narrowed)

    if module == "estudios":
        if "importeContratado" in fields:
            return [f"importeContratado{year}"]
        if "produccion" in fields:
            return [f"produccion{year}"]
    if module == "produccion" and any(field.startswith("licitacionProduccion") or field == "produccionTotal" for field in fields):
        return [f"licitacionProduccion{year}"]
    return []


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
    return any(token in text for token in ("cada mes", "por mes", "mes a mes", "en cada mes", "todos los meses"))


def _is_per_year_request(text: str) -> bool:
    return any(token in text for token in ("cada ano", "por ano", "anual", "en cada ano", "todos los anos"))


def _detect_aggregate(question: str, text: str, *, module: str, fields: List[str], year: int | None, reference: str | None) -> Dict[str, Any] | None:
    top_match = re.search(r"\btop\s+(\d+)\b", text)
    count_match = re.search(r"\b(?:las|los|primeras|primeros|ultimas|ultimos)\s+(\d+)\b", text)
    top_n = int((top_match or count_match).group(1)) if top_match or count_match else 1
    is_top = bool(top_match) or any(
        token in text
        for token in (
            "proyecto con mas",
            "proyecto con mayor",
            "proyectos con mas",
            "proyectos con mayor",
            "obra con mas",
            "obra con mayor",
            "obras con mas",
            "obras con mayor",
            "estudio con mas",
            "estudio con mayor",
            "estudios con mas",
            "estudios con mayor",
            "licitacion con mas",
            "licitacion con mayor",
            "licitaciones con mas",
            "licitaciones con mayor",
            "ranking",
            *_schema_aggregation_aliases("top"),
        )
    )
    is_avg = any(token in text for token in ("importe medio", "importe promedio", "media de ", "promedio de ", "valor medio", *_schema_aggregation_aliases("avg")))
    is_count = _is_count_request(text)
    is_sum = (not is_count) and any(token in text for token in ("cuanto ", "cuanta ", "cuantos ", "cuantas ", "total de ", "suma de ", *_schema_aggregation_aliases("sum")))
    order = "latest" if any(token in text for token in ("ultimas", "ultimos", "recientes", "mas recientes")) else None
    has_specific_reference = bool(reference)
    asks_plural = any(token in text for token in ("proyectos", "obras", "estudios", "cierres"))

    if has_specific_reference and not is_top:
        return None

    if (
        is_count
        and asks_plural
        and _looks_like_filtered_listing_request(text)
        and any(token in text for token in ("adjudicada", "adjudicadas", "adjudicado", "adjudicados"))
    ):
        return None

    metric = _detect_count_metric(text, module) if is_count else _detect_aggregate_metric(text, fields, module=module, year=year)
    if not metric:
        return None
    if not is_top and not is_sum and not is_avg and not is_count:
        return None

    scope = None
    if "pipeline" in text or any(alias in text for alias in _schema_scope_aliases("pipeline")) or any(field.startswith("plan20") for field in fields):
        scope = "pipeline"
    elif "backlog" in text or any(alias in text for alias in _schema_scope_aliases("backlog")) or any(token in text for token in ("adjudicada", "adjudicadas", "adjudicado", "adjudicados")):
        scope = "backlog"

    return {
        "kind": "count" if is_count else ("avg" if is_avg else ("top" if is_top else "sum")),
        "top_n": top_n,
        "metric": metric,
        "scope": scope,
        "filter_text": _extract_filter_text(question, text),
        "periodo": _extract_periodo(text),
        "area": _extract_area(text),
        "order": order,
    }


def _detect_aggregate_metric(text: str, fields: List[str], *, module: str, year: int | None) -> str | None:
    if "cierre" in text or "cierres" in text or _contains_cierre_hint(text):
        for label, field in CLOSURE_FIELD_HINTS.items():
            if label in text:
                return f"cierre:{field}"
    if module == "estudios":
        for metric, aliases in STUDIES_AGGREGATE_KEYWORDS.items():
            if any(alias in text for alias in aliases) or any(alias in text for alias in _schema_field_synonyms("estudios", metric)):
                return metric
        if any(token in text for token in ("importe medio", "importe promedio", "media de importe", "promedio de importe")):
            return "importecontratado"
        if year and any(field.startswith("importeContratado") for field in fields):
            return "importecontratado"
        return None
    if module == "produccion":
        for metric, aliases in PRODUCCION_AGGREGATE_KEYWORDS.items():
            if any(alias in text for alias in aliases) or any(alias in text for alias in _schema_field_synonyms("produccion", metric)):
                return metric
        for field in fields:
            if field in {"produccionTotal", *PRODUCTION_MONTH_FIELDS.values()}:
                return field.lower()
        if "produccion" in text:
            return "producciontotal"
    return None


def _is_count_request(text: str) -> bool:
    count_markers = (
        "cuantas ",
        "cuantos ",
        "numero de ",
        "n de ",
        "cantidad de ",
        "cuenta de ",
    )
    if not any(marker in text for marker in count_markers):
        return False
    if any(entity in text for entity in ("ofertas", "licitaciones", "obras", "cierres", "estudios")):
        return True

    numeric_metric_markers = (
        "importe",
        "media",
        "medio",
        "promedio",
        "total",
        "suma",
        "cartera",
        "rentabilidad",
        "presupuesto",
        "coste",
        "costes",
    )
    return not any(marker in text for marker in numeric_metric_markers)


def _looks_like_filtered_listing_request(text: str) -> bool:
    return bool(
        (
            re.search(r"^(que|cuales|dame|listame|lista|muestrame|indicame|ensename)\s+(?:el\s+|la\s+)?(?:top\s+de\s+)?(?:proyectos|obras|estudios|licitaciones|ofertas)\b", text)
            or re.search(r"^(que|cuales)\s+son\s+las?\s+(?:\d+\s+)?(?:proyectos|obras|estudios|licitaciones|ofertas)\b", text)
        )
        and any(token in text for token in (" son ", " contienen ", " incluyen ", " hay ", " recientes", " relacionadas", " vinculadas", " vinculados", " top ", " ranking ", " cliente"))
    )


def _detect_count_metric(text: str, module: str) -> str:
    if "cierre" in text or "cierres" in text or _contains_cierre_hint(text):
        return "cierre:count"
    return "obras" if module == "produccion" else "licitaciones"


def _extract_periodo(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


def _extract_area(text: str) -> str | None:
    area_patterns = {
        "OI": (r"\bo\s*i\b", r"\boi\b"),
        "MAN": (r"\bman\b",),
        "MT": (r"\bmt\b",),
        "BT": (r"\bbt\b",),
    }
    for area, patterns in area_patterns.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return area
    return None


def _extract_filter_text(original_question: str, normalized: str) -> str | None:
    relation_match = re.search(
        r"\b(?:relacionad[oa]s?|vinculad[oa]s?|asociad[oa]s?)\s+(?:con|a)\s+([a-z0-9][a-z0-9 .&/-]{1,40}?)(?:\s+con\s+su\s+cliente|\s+con\s+cliente|\s*$)",
        normalized,
    )
    if relation_match:
        candidate = re.sub(r"\s+", " ", relation_match.group(1)).strip(" -?.")
        candidate = re.sub(r"^(el|la|los|las)\s+", "", candidate).strip()
        if candidate:
            return candidate.upper() if candidate.isalpha() and len(candidate) <= 12 else candidate

    client_match = re.search(
        r"\b(?:para el|para la|para los|para las|para|del|de la|de los|de las|de)\s+cliente\s+(?:(?:el|la|los|las)\s+)?([a-z0-9][a-z0-9 .&/-]{1,40}?)(?:\s*$)",
        normalized,
    )
    if client_match:
        candidate = re.sub(r"\s+", " ", client_match.group(1)).strip(" -?.")
        candidate = re.sub(r"^(el|la|los|las)\s+", "", candidate).strip()
        if candidate:
            return candidate.upper() if candidate.isalpha() and len(candidate) <= 12 else candidate

    keyword_match = re.search(
        r"\b(?:palabra|texto|cadena)\s+([a-z0-9][a-z0-9 .&/-]{1,40})(?:\s+en\s+su\s+\w+|\s+en\s+\w+|\?|$)",
        normalized,
    )
    if keyword_match:
        candidate = re.sub(r"\s+", " ", keyword_match.group(1)).strip(" -?.")
        candidate = re.sub(r"\s+en\s+su\s+\w+$", "", candidate).strip(" -?.")
        candidate = re.sub(r"\s+en\s+\w+$", "", candidate).strip(" -?.")
        candidate = re.sub(r"^(el|la|los|las)\s+", "", candidate).strip()
        if candidate:
            return candidate.upper() if candidate.isalpha() and len(candidate) <= 12 else candidate

    candidates = re.findall(r"\b(?:de|del|de la|de los|de las|para)\s+([a-z0-9][a-z0-9 .&/-]{1,60})", normalized)
    cleaned_candidates: List[str] = []
    for candidate in candidates:
        cleaned = candidate
        for label in sorted(CLOSURE_FIELD_HINTS, key=len, reverse=True):
            cleaned = re.sub(rf"\b{re.escape(label)}\b", " ", cleaned)
        cleaned = re.sub(r"\b(importe contratado|importe adjudicado|importe medio|importe promedio|importe|media|medio|promedio|valor|mayor|mayores|ultimas|ultimos|recientes|estan|esta|pipeline|backlog|produccion|proyecto|proyectos|obra|obras|estudio|estudios|licitacion|licitaciones|cliente|estado|concurso|cartera|cierre|cierres|presupuesto|presupuestos|diferencia|adjudicada|adjudicadas|adjudicado|adjudicados|ganada|ganadas|ganado|ganados|conseguida|conseguidas|contratada|contratadas|hemos|han|que|nos|tenemos|tienen|llevamos|actualmente|ahora|hay|van|vamos|en|ano|anual|cada|total|por|para|del|de|la|las|los)\b", " ", cleaned)
        cleaned = re.sub(r"\b20\d{2}\b", " ", cleaned)
        cleaned = re.sub(r"\b\d+\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
        if cleaned and len(cleaned) >= 2:
            cleaned_candidates.append(cleaned)

    if cleaned_candidates:
        value = cleaned_candidates[-1]
        return value.upper() if value.isalpha() and len(value) <= 6 else value

    quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', original_question)
    if quoted:
        return (quoted.group(1) or quoted.group(2) or "").strip() or None
    return None


def _extract_expected_client(original_question: str, normalized: str) -> str | None:
    if "cliente" not in normalized or "?" not in original_question:
        return None
    if " es del cliente " not in f" {normalized} ":
        return None
    match = re.search(r"(?:es del cliente)\s+([a-z0-9 .&/-]{2,})\??$", normalized)
    if not match:
        return None
    candidate = re.sub(r"\s+", " ", match.group(1)).strip(" ?.")
    if candidate:
        return candidate.upper()
    return None


def _is_source_info_request(text: str) -> bool:
    return any(pattern in text for pattern in SOURCE_INFO_PATTERNS)


def _build_source_info_response(parsed: Dict[str, Any], *, module: str, route: str) -> Dict[str, Any]:
    source_name = "Estudios" if module == "estudios" else "Produccion"
    reference = parsed.get("reference")
    fields = parsed.get("fields") or []
    fields_text = ", ".join(_field_label(field) for field in fields[:4]) or "el dato consultado"
    ref_text = f" para {reference}" if reference else ""
    return {
        "response": f"Este dato lo estoy consultando desde {source_name}{ref_text}. La pregunta se ha interpretado sobre {fields_text}.",
        "route": route,
        "confidence": 1.0,
        "sources": [{"source": f"AppRegenera SQL {source_name}", "module": module}],
    }


def _is_explicit_client_field_filter(question_text: str) -> bool:
    return any(
        phrase in question_text
        for phrase in (
            "en su cliente",
            "en cliente",
            "cliente contiene",
            "cliente contenga",
            "del cliente ",
            "para el cliente ",
            "para cliente ",
        )
    )


def _is_client_contains_query(question_text: str) -> bool:
    return _is_explicit_client_field_filter(question_text) and any(
        token in question_text
        for token in ("contiene", "contienen", "incluye", "incluyen", "palabra", "texto", "cadena")
    )


def _is_exact_client_target_query(question_text: str) -> bool:
    normalized = f" {question_text.strip()} "
    if any(
        phrase in normalized
        for phrase in (
            " del cliente ",
            " para el cliente ",
            " para la cliente ",
            " para cliente ",
            " cliente es ",
            " cliente sea ",
        )
    ):
        return True
    return bool(re.search(r"(?:^|\s)(?:y\s+)?para\s+[a-z][a-z0-9 .&/-]{1,40}\??\s*$", question_text))


def _canonicalize_client_name(text: str) -> str:
    canonical = _normalize(text)
    canonical = canonical.replace(".", " ")
    canonical = re.sub(
        r"\b(s a|sa|s l|sl|s l u|slu|s a u|sau|sociedad anonima|sociedad limitada|sociedad limitada unipersonal)\b",
        " ",
        canonical,
    )
    canonical = re.sub(r"\s+", " ", canonical).strip()
    return canonical


def _apply_client_filter(
    matches: List[Dict[str, Any]],
    filter_text: str,
    *,
    exact_client_target: bool,
) -> List[Dict[str, Any]]:
    if not matches:
        return []
    needle = _normalize(filter_text)
    if not needle:
        return matches
    if not exact_client_target:
        return [
            item
            for item in matches
            if needle in _normalize(str(item.get("Cliente") or ""))
        ]

    canonical_needle = _canonicalize_client_name(filter_text)
    exact_matches = [
        item
        for item in matches
        if _canonicalize_client_name(str(item.get("Cliente") or "")) == canonical_needle
    ]
    if exact_matches:
        return exact_matches

    return [
        item
        for item in matches
        if canonical_needle and re.search(
            rf"(?<!\w){re.escape(canonical_needle)}(?!\w)",
            _canonicalize_client_name(str(item.get("Cliente") or "")),
        )
    ]


def _looks_like_recent_listing_query(question_text: str) -> bool:
    return any(token in question_text for token in ("recientes", "mas recientes", "ultimas", "ultimos"))


def _looks_like_ranked_listing_query(question_text: str) -> bool:
    return any(token in question_text for token in ("top", "ranking"))


def _looks_like_active_production_listing_query(question_text: str) -> bool:
    return any(token in question_text for token in ("en curso", "actualmente", "activa", "activas"))


def _sort_estudios_listing_matches(matches: List[Dict[str, Any]], *, question_text: str) -> List[Dict[str, Any]]:
    if _looks_like_recent_listing_query(question_text):
        return sorted(
            matches,
            key=lambda item: (
                item.get("FechaAdjudicacion")
                or item.get("FechaPresentacion")
                or item.get("UpdatedDate")
                or item.get("CreatedDate")
                or datetime.min
            ),
            reverse=True,
        )

    if _looks_like_ranked_listing_query(question_text):
        return sorted(
            matches,
            key=lambda item: (
                parse_decimal(item.get("ImporteContratado")) or 0.0,
                parse_decimal(item.get("Produccion")) or 0.0,
                parse_decimal(item.get("Plan2026")) or 0.0,
            ),
            reverse=True,
        )

    return matches


def _sort_produccion_listing_matches(matches: List[Dict[str, Any]], *, question_text: str) -> List[Dict[str, Any]]:
    ordered = matches
    if _looks_like_active_production_listing_query(question_text):
        ordered = [
            item for item in ordered
            if item.get("Finalizada") in (False, 0, None)
            and _normalize(str(item.get("Estado") or "")).strip() not in {"completada", "finalizada"}
        ]

    if _looks_like_ranked_listing_query(question_text):
        ordered = sorted(
            ordered,
            key=lambda item: (
                parse_decimal(item.get("ImporteContratado")) or 0.0,
                parse_decimal(item.get("Cartera2026")) or 0.0,
                parse_decimal(item.get("Pendiente2026")) or 0.0,
            ),
            reverse=True,
        )
    elif _looks_like_recent_listing_query(question_text):
        ordered = sorted(
            ordered,
            key=lambda item: item.get("UpdatedDate") or item.get("CreatedDate") or datetime.min,
            reverse=True,
        )

    return ordered


def _infer_source_module_from_history(history: List[Dict[str, Any]], fallback: str) -> str:
    for item in reversed(history or []):
        question = str(item.get("question") or "")
        route = detect_business_route(question)
        if route == "business_licitaciones":
            return "estudios"
        if route == "business_produccion":
            return "produccion"
    return fallback


def _detect_fields(
    text: str,
    *,
    module: str,
    year: int | None,
    years: List[int],
    cuatrimestre: int | None,
    month: int | None,
    per_month: bool,
    per_year: bool,
) -> List[str]:
    fields: List[str] = []
    text_without_type_client = text.replace("tipo de cliente", " ").replace("tipo cliente", " ")

    if "pipeline" in text or "plan " in text:
        if per_year:
            year_fields = [f"plan{item}" for item in years]
            fields.extend(year_fields or [f"plan{item}" for item in LICITACION_PLAN_YEARS])
        elif year:
            fields.append(f"plan{year}")
        else:
            fields.append(f"plan{LICITACION_PLAN_YEARS[0]}") if LICITACION_PLAN_YEARS else fields.append("plan2026")

    if "backlog" in text:
        if per_year:
            year_fields = [f"produccion{item}" for item in years]
            fields.extend(year_fields or [f"produccion{item}" for item in LICITACION_PRODUCCION_YEARS])
        elif year:
            fields.append(f"produccion{year}")
        else:
            fields.append("produccion")

    if re.search(r"\bimporte\b", text) or "importe contratado" in text or "importe adjudicado" in text:
        if module == "estudios":
            if per_year:
                year_fields = [f"importeContratado{item}" for item in years]
                fields.extend(year_fields or [f"importeContratado{item}" for item in LICITACION_IMPORTE_YEARS])
            elif year and not cuatrimestre and not month:
                fields.append(f"importeContratado{year}")
            elif any(token in text for token in ("previo", "anteriores", "anterior")):
                fields.append("importeContratadoPrevio")
            else:
                fields.append("importeContratado")
        else:
            fields.append("importeContratado")

    production_metric_text = _strip_produccion_scope_mentions(text) if module == "produccion" else text
    if "produccion" in production_metric_text and "backlog" not in text:
        if module == "estudios":
            if per_year:
                year_fields = [f"produccion{item}" for item in years]
                fields.extend(year_fields or [f"produccion{item}" for item in LICITACION_PRODUCCION_YEARS])
            elif year and not cuatrimestre and not month:
                fields.append(f"produccion{year}")
            elif any(token in text for token in ("previa", "previo", "anteriores", "anterior")):
                fields.append("produccionPrevio")
            else:
                fields.append("produccion")
        else:
            if "cierre" in text:
                fields.append("cierre")
            elif per_year:
                year_fields = [f"licitacionProduccion{item}" for item in years]
                fields.extend(year_fields or [f"licitacionProduccion{item}" for item in PRODUCCION_LINKED_YEARS])
            elif "total" in text:
                fields.append("produccionTotal")
            elif per_month:
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

    if module == "produccion" and "cierre" in text and "cierre" not in fields:
        fields.append("cierre")
    if module == "produccion" and _contains_cierre_hint(text) and "cierre" not in fields:
        fields.append("cierre")
    if module == "produccion" and "cartera" in text and year == 2026:
        fields.append("cartera2026")
    if module == "produccion" and "pendiente" in text and year == 2026:
        fields.append("pendiente2026")

    field_specs = LICITACION_FIELD_SPECS if module == "estudios" else PRODUCCION_FIELD_SPECS
    for canonical, _, aliases in field_specs:
        schema_aliases = _schema_field_synonyms(module, canonical)
        for alias in dict.fromkeys((*aliases, *schema_aliases)):
            haystack = text_without_type_client if alias == "cliente" and module == "produccion" else text
            if _alias_in_text(haystack, alias) and canonical not in fields:
                fields.append(canonical)
                break

    if module == "estudios" and per_year:
        generic_yearly_keys = {"importeContratado", "produccion", "produccionPrevio"}
        fields = [field for field in fields if field not in generic_yearly_keys]
    elif module == "estudios":
        if any(re.fullmatch(r"produccion20\d{2}", field) for field in fields):
            fields = [field for field in fields if field != "produccion"]
        if any(re.fullmatch(r"importeContratado20\d{2}", field) for field in fields):
            fields = [field for field in fields if field != "importeContratado"]

    if not fields and re.search(r"\bcual es la obra\b|\bque obra\b|\bnombre de la obra\b|\bnombre obra\b", text):
        fields.append("obra" if module == "estudios" else "nombreObra")

    if "obra" in fields and any(field in fields for field in {"tipologiaObra", "tipoObra"}):
        fields = [field for field in fields if field != "obra"]

    if not fields:
        fields = ["importeContratado", "cliente", "estado"] if module == "estudios" else ["nombreObra", "importeContratado"]
    return _dedupe(fields)[:16]


def _monthly_field_keys() -> List[str]:
    return [PRODUCTION_MONTH_FIELDS[index] for index in range(1, 13)]


def _strip_produccion_scope_mentions(text: str) -> str:
    cleaned = re.sub(r"\b(?:en|de|del)\s+produccion\b", " ", text)
    cleaned = re.sub(r"\bproduccion\s+(?:para|del|de la|de los|de las)\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _dedupe(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _field_label(field_name: str) -> str:
    label = FIELD_LABELS.get(field_name)
    if label:
        return label
    match = re.fullmatch(r"(importeContratado|produccion|plan|licitacionProduccion)(20\d{2})", field_name or "")
    if match:
        base, year = match.groups()
        base_labels = {
            "importeContratado": "importe contratado",
            "produccion": "produccion",
            "plan": "pipeline",
            "licitacionProduccion": "produccion",
        }
        return f"{base_labels.get(base, base)} {year}"
    return field_name


def _should_block_cross_module_detail_fallback(module: str, parsed: Dict[str, Any]) -> bool:
    if module != "produccion":
        return False
    fields = set(parsed.get("fields") or [])
    return bool(fields & PRODUCCION_ONLY_FALLBACK_BLOCK_FIELDS)


def _pick_best_licitacion_match(matches: List[Dict[str, Any]], reference: str) -> Dict[str, Any] | str | None:
    if not matches:
        return None
    ref = _normalize(reference).replace(" ", "-")
    exact: List[Dict[str, Any]] = []
    for item in matches:
        candidates = [
            _normalize(str(item.get("NumeroProyecto") or "")).replace(" ", "-"),
            _normalize(str(item.get("NumeroOferta") or "")).replace(" ", "-"),
        ]
        if ref in candidates:
            exact.append(item)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return "ambiguous"
    return matches[0] if len(matches) == 1 else "ambiguous"


def _pick_best_produccion_match(matches: List[Dict[str, Any]], reference: str) -> Dict[str, Any] | str | None:
    if not matches:
        return None
    ref = _normalize(reference).replace(" ", "-")
    exact: List[Dict[str, Any]] = []
    for item in matches:
        candidates = [
            _normalize(str(item.get("CodigoObra") or "")).replace(" ", "-"),
            _normalize(str(item.get("NumeroProyecto") or "")).replace(" ", "-"),
            _normalize(str(item.get("NumeroOferta") or "")).replace(" ", "-"),
        ]
        if ref in candidates:
            exact.append(item)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return "ambiguous"
    return matches[0] if len(matches) == 1 else "ambiguous"


def _pick_exact_http_match(matches: List[Dict[str, Any]], reference: str) -> Dict[str, Any] | None:
    ref = _normalize(reference).replace(" ", "-")
    for item in matches:
        primary = _normalize(str(item.get("primaryCode") or "")).replace(" ", "-")
        display = _normalize(str(item.get("displayName") or "")).replace(" ", "-")
        if ref in {primary, display}:
            return item
    return None


def _match_label(item: Dict[str, Any], *, module: str) -> str:
    if module == "estudios":
        code = item.get("NumeroProyecto") or item.get("NumeroOferta") or "-"
        name = item.get("Obra") or item.get("Cliente") or "-"
        return f"{code} - {name}"
    code = item.get("CodigoObra") or item.get("NumeroProyecto") or item.get("NumeroOferta") or "-"
    name = item.get("NombreObra") or item.get("Cliente") or "-"
    return f"{code} - {name}"


def _build_estudios_result(detail: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
    fields = []
    for key in parsed.get("fields") or []:
        value = _extract_estudios_value(detail, key, parsed)
        fields.append({"key": key, "value": value})

    if parsed.get("expected_client"):
        actual_client = str(detail.get("Cliente") or "").strip()
        expected = str(parsed["expected_client"]).strip()
        answer = actual_client.upper() == expected.upper()
        fields = [{"key": "cliente", "value": f"{'si' if answer else 'no'}; cliente real = {actual_client or 'sin dato'}"}]

    return {
        "status": "ok",
        "entity": {
            "primaryCode": detail.get("NumeroProyecto") or detail.get("NumeroOferta") or parsed.get("reference"),
            "displayName": detail.get("Obra") or detail.get("Cliente") or parsed.get("reference"),
        },
        "fields": fields,
    }


def _build_produccion_result(detail: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
    fields = []
    for key in parsed.get("fields") or []:
        value = _extract_produccion_value(detail, key)
        if key == "periodosMensuales" and parsed.get("per_month"):
            value = detail.get("PeriodosMensuales") or []
        elif key in PRODUCTION_MONTH_FIELDS.values() and value is None:
            value = _extract_periodo_month_value(detail, parsed)
        fields.append({"key": key, "value": value})

    return {
        "status": "ok",
        "entity": {
            "primaryCode": detail.get("CodigoObra") or detail.get("LicitacionNumeroProyecto") or detail.get("LicitacionNumeroOferta") or parsed.get("reference"),
            "displayName": detail.get("NombreObra") or parsed.get("reference"),
        },
        "fields": fields,
    }


def _extract_periodo_month_value(detail: Dict[str, Any], parsed: Dict[str, Any]) -> Any:
    requested_month = parsed.get("month")
    requested_year = parsed.get("year")
    if not requested_month:
        return None
    for periodo in detail.get("PeriodosMensuales") or []:
        if int(periodo.get("Mes") or 0) != int(requested_month):
            continue
        if requested_year and int(periodo.get("Anio") or 0) != int(requested_year):
            continue
        return periodo.get("Importe")
    return None


def _extract_estudios_value(detail: Dict[str, Any], key: str, parsed: Dict[str, Any]) -> Any:
    if key == "tipo":
        return detail.get("Tipo")
    if key == "tipoRegistro":
        return detail.get("TipoRegistro")
    if key == "numeroOferta":
        return detail.get("NumeroOferta")
    if key == "numeroProyecto":
        return detail.get("NumeroProyecto")
    if key == "obra":
        return detail.get("Obra")
    if key == "cliente":
        return detail.get("Cliente")
    if key == "tipoObra":
        return detail.get("TipoObra")
    if key == "tipologiaObra":
        return detail.get("TipologiaObra")
    if key == "estado":
        return detail.get("Estado")
    if key == "situacionOferta":
        return detail.get("SituacionOferta")
    if key == "probabilidadAdjudicacion":
        return detail.get("ProbabilidadAdjudicacion")
    if key == "fechaPresentacion":
        return detail.get("FechaPresentacion")
    if key == "fechaApertura":
        return detail.get("FechaApertura")
    if key == "fechaAdjudicacion":
        return detail.get("FechaAdjudicacion")
    if key == "periodoEjecucion":
        return detail.get("PeriodoEjecucion")
    if key == "observaciones":
        return detail.get("Observaciones")
    if key == "enlaceLicitacion":
        return detail.get("EnlaceLicitacion")
    if key == "pendiente":
        return detail.get("Pendiente")
    if key == "concurso":
        return detail.get("Concurso")
    if key == "importeContratado":
        return detail.get("ImporteContratado")
    if key == "importeContratadoPrevio":
        return detail.get("ImporteContratadoPrevio")
    if key.startswith("importeContratado20"):
        value = detail.get(key[0].upper() + key[1:])
        return 0.0 if value is None else value
    if key == "produccion":
        return first_non_null([detail.get("Produccion"), detail.get("Produccion2026")])
    if key == "produccionPrevio":
        return detail.get("ProduccionPrevio")
    if key.startswith("produccion20"):
        column = key[0].upper() + key[1:]
        if key == "produccion2026":
            value = first_non_null([detail.get("Produccion2026"), detail.get("Produccion")])
            return 0.0 if value is None else value
        value = detail.get(column)
        return 0.0 if value is None else value
    if key.startswith("plan20"):
        value = detail.get(key[0].upper() + key[1:])
        return 0.0 if value is None else value
    return detail.get(key[0].upper() + key[1:])


def _extract_produccion_value(detail: Dict[str, Any], key: str) -> Any:
    direct_map = {
        "codigoObra": "CodigoObra",
        "nombreObra": "NombreObra",
        "tipoCliente": "TipoCliente",
        "finalizada": "Finalizada",
        "oculta": "Oculta",
        "responsableNombreCompleto": "ResponsableNombreCompleto",
        "responsableCodigo": "ResponsableCodigo",
        "tipoObra": "TipoObra",
        "importeContratado": "ImporteContratado",
        "rentabilidadPrevista2026": "RentabilidadPrevista2026",
        "produccionOrigen2025": "ProduccionOrigen2025",
        "produccionOrigenAnosAnteriores": "ProduccionOrigenAnosAnteriores",
        "ventaMaster2025": "VentaMaster2025",
        "porcentajeMateriales": "PorcentajeMateriales",
        "porcentajeManoObra": "PorcentajeManoObra",
        "cartera2026": "Cartera2026",
        "pendiente2026": "Pendiente2026",
        "diferencia": "Diferencia",
        "comentarios": "Comentarios",
        "licitacionNumeroProyecto": "LicitacionNumeroProyecto",
        "licitacionNumeroOferta": "LicitacionNumeroOferta",
        "licitacionCliente": "LicitacionCliente",
        "licitacionEstado": "LicitacionEstado",
        "produccionPrimerCuatrimestre": "ProduccionPrimerCuatrimestre",
        "produccionSegundoCuatrimestre": "ProduccionSegundoCuatrimestre",
        "produccionPrimerSegundoCuatrimestre": "ProduccionPrimerSegundoCuatrimestre",
        "produccionEstimadaPendiente": "ProduccionEstimadaPendiente",
        "produccionEstimadaTercerCuatrimestre": "ProduccionEstimadaTercerCuatrimestre",
        "periodosMensuales": "PeriodosMensuales",
        "ultimaSincronizacionExcelUtc": "UltimaSincronizacionExcelUtc",
    }
    column = direct_map.get(key)
    if column:
        return detail.get(column)
    if key == "produccionTotal":
        periodos = detail.get("PeriodosMensuales") or []
        total = sum(parse_decimal(item.get("Importe")) or 0.0 for item in periodos)
        if total > 0:
            return total
        return first_non_null(
            [
                detail.get("LicitacionProduccion2026"),
                detail.get("LicitacionProduccion"),
                detail.get("ProduccionPrimerCuatrimestre"),
                detail.get("ProduccionSegundoCuatrimestre"),
                detail.get("ProduccionEstimadaTercerCuatrimestre"),
            ]
        )
    if key in PRODUCTION_MONTH_FIELDS.values():
        db_column = key[0].upper() + key[1:]
        return detail.get(db_column)
    if key.startswith("licitacionProduccion20"):
        value = detail.get(key[0].upper() + key[1:])
        return 0.0 if value is None else value
    return detail.get(key[0].upper() + key[1:])


def _match_cierre_fields(cierre: Dict[str, Any], parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    normalized_question = _normalize(parsed.get("question") or " ".join(parsed.get("fields") or []))
    exact_fields = _extract_exact_cierre_fields(normalized_question)
    if exact_fields:
        exact_values = _collect_exact_cierre_values(cierre, exact_fields)
        if exact_values:
            return exact_values
        return [{"label": field, "value": None} for field in exact_fields]

    tokens = [
        token
        for token in re.split(r"\s+", normalized_question)
        if token
        and len(token) >= 3
        and token not in {"cierre", "produccion", "periodosmensuales", "proyecto", "obra", "del", "de", "la", "el", "cual", "que", "dice", "dato", "desde"}
    ]
    values: List[Dict[str, Any]] = []

    for item in cierre.get("ValoresNormalizados") or []:
        label = str(item.get("Campo") or "").strip()
        value = item.get("Valor")
        if value in (None, ""):
            continue
        if label.startswith("__"):
            continue
        label_norm = _normalize(label)
        score = sum(1 for token in tokens if token in label_norm)
        if score > 0 or not tokens:
            values.append({"label": label, "value": value, "score": score})

    if not values:
        for key, value in (cierre.get("Valores") or {}).items():
            if value in (None, ""):
                continue
            if str(key).startswith("__"):
                continue
            label_norm = _normalize(str(key))
            score = sum(1 for token in tokens if token in label_norm)
            if score > 0 or not tokens:
                values.append({"label": str(key), "value": value, "score": score})

    deduped = []
    seen = set()
    values.sort(key=lambda item: (-item["score"], item["label"]))
    for item in values:
        marker = (item["label"], str(item["value"]))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append({"label": item["label"], "value": item["value"]})
    return deduped[:6]


def _extract_exact_cierre_fields(normalized_question: str) -> List[str]:
    fields = []
    for label, field in CLOSURE_FIELD_HINTS.items():
        if label in normalized_question:
            fields.append(field)
    return _dedupe(fields)


def _collect_exact_cierre_values(cierre: Dict[str, Any], exact_fields: List[str]) -> List[Dict[str, Any]]:
    values_by_label: Dict[str, Any] = {}
    for item in cierre.get("ValoresNormalizados") or []:
        label = str(item.get("Campo") or "").strip()
        value = item.get("Valor")
        if label and value not in (None, "") and not label.startswith("__"):
            values_by_label[label] = value
    for key, value in (cierre.get("Valores") or {}).items():
        label = str(key)
        if label and value not in (None, "") and not label.startswith("__"):
            values_by_label.setdefault(label, value)

    wanted = {_normalize(field).replace(" ", "") for field in exact_fields}
    matches = []
    for label, value in values_by_label.items():
        if _normalize(label).replace(" ", "") in wanted:
            matches.append({"label": label, "value": value})
    return matches


def _summarize_cierre(cierre: Dict[str, Any]) -> str | None:
    values = _match_cierre_fields(cierre, {"fields": []})
    if not values:
        return None
    titulo = cierre.get("Nombre") or cierre.get("Numero") or "la referencia solicitada"
    periodo = cierre.get("Periodo")
    period_copy = f" ({periodo})" if periodo else ""
    resumen = "; ".join(f"{item['label']} = {_format_value(item['value'])}" for item in values[:5])
    return f"Cierre de {titulo}{period_copy}: {resumen}."


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
        label = _field_label(item["key"])
        return f"{prefix} {code or ''} {name}{period_text}: {label} = {_format_value(item.get('value'))}."

    summary = "; ".join(
        f"{_field_label(item['key'])} = {_format_value(item.get('value'))}"
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
        periodos = next((field.get("value") for field in fields if field.get("key") == "periodosMensuales"), None) or []
        normalized_periodos = []
        for periodo in periodos:
            amount = periodo.get("Importe")
            if amount is None:
                continue
            month_value = periodo.get("Mes")
            year_value = periodo.get("Anio")
            month_label = MONTH_LABELS.get(int(month_value or 0), f"mes {month_value}")
            year_copy = f" {year_value}" if year_value else ""
            normalized_periodos.append(f"{month_label}{year_copy} = {_format_value(amount)}")
        if normalized_periodos:
            monthly_chunks = normalized_periodos

    if not monthly_chunks:
        return None

    return f"Obra {code or ''} {name}{period_text}: " + "; ".join(monthly_chunks) + "."


def _build_no_data_message(match: Dict[str, Any], *, module: str, parsed: Dict[str, Any]) -> str:
    prefix = "Licitacion" if module == "estudios" else "Obra"
    name = match.get("displayName") or parsed["reference"]
    code = match.get("primaryCode") or parsed["reference"]
    period_text = _build_period_text(parsed)

    if module == "produccion" and parsed.get("per_month"):
        return f"{prefix} {code} {name}{period_text}: no hay produccion mensual cargada en AppRegenera para ese periodo."

    if parsed.get("month") and module == "produccion":
        return f"{prefix} {code} {name}{period_text}: no hay dato cargado para ese mes en AppRegenera."

    requested_labels = [_field_label(field) for field in parsed.get("fields") or []]
    labels_text = ", ".join(requested_labels[:4]) if requested_labels else "el dato solicitado"
    return f"{prefix} {code} {name}{period_text}: no hay datos disponibles para {labels_text}."


def _build_period_text(parsed: Dict[str, Any]) -> str:
    if not _should_include_period_context(parsed):
        return ""
    parts: List[str] = []
    explicit_years = parsed.get("years") or []
    if explicit_years and len(explicit_years) > 1:
        parts.extend(str(year) for year in explicit_years)
    elif parsed.get("year"):
        parts.append(str(parsed["year"]))
    if parsed.get("cuatrimestre"):
        parts.append(f"C{parsed['cuatrimestre']}")
    if parsed.get("month"):
        parts.append(MONTH_LABELS.get(parsed["month"], f"mes {parsed['month']}"))
    return f" ({', '.join(parts)})" if parts else ""


def _should_include_period_context(parsed: Dict[str, Any]) -> bool:
    if parsed.get("month") or parsed.get("cuatrimestre"):
        return True
    if len(parsed.get("years") or []) > 1:
        return True
    if not parsed.get("year"):
        return False
    for field in parsed.get("fields") or []:
        if re.search(r"(19|20)\d{2}$", field):
            return True
        if field in {"periodosMensuales", "produccionTotal"}:
            return True
    return bool((parsed.get("aggregate") or {}).get("metric"))


def _aggregate_label(metric: str, year: int | None) -> str:
    if metric.startswith("cierre:"):
        if metric == "cierre:count":
            return "cierres"
        field = metric.split(":", 1)[1]
        return next((label for label, value in CLOSURE_FIELD_HINTS.items() if value == field), field)
    if metric in {"licitaciones", "obras"}:
        return metric
    if metric == "importecontratado":
        return f"importe contratado {year}" if year else "importe contratado"
    if metric == "importecontratadoprevio":
        return "importe contratado previo"
    if metric == "pipeline":
        return f"pipeline {year}" if year else "pipeline"
    if metric == "backlog":
        return f"backlog {year}" if year else "backlog"
    if metric == "produccion":
        return f"produccion {year}" if year else "produccion"
    if metric == "producciontotal":
        return "produccion total"
    if metric == "cartera2026":
        return "cartera 2026"
    if metric == "pendiente2026":
        return "pendiente 2026"
    if metric == "diferencia":
        return "diferencia"
    if metric == "rentabilidadprevista2026":
        return "rentabilidad prevista 2026"
    if metric == "produccionorigen2025":
        return "produccion origen 2025"
    if metric == "produccionorigenanosanteriores":
        return "produccion origen anos anteriores"
    if metric == "ventamaster2025":
        return "venta master 2025"
    if metric == "porcentajemateriales":
        return "porcentaje de materiales"
    if metric == "porcentajemanoobra":
        return "porcentaje de mano de obra"
    if metric == "produccionprimercuatrimestre":
        return "produccion primer cuatrimestre"
    if metric == "produccionsegundocuatrimestre":
        return "produccion segundo cuatrimestre"
    if metric == "produccionprimersegundocuatrimestre":
        return "produccion acumulada primer y segundo cuatrimestre"
    if metric == "produccionestimadapendiente":
        return "produccion estimada pendiente"
    if metric == "produccionestimadatercercuatrimestre":
        return "produccion tercer cuatrimestre"
    return metric


def _contains_cierre_hint(text: str) -> bool:
    return any(label in text for label in CLOSURE_FIELD_HINTS)


def _detect_reference_module(reference: str | None) -> str | None:
    if not reference:
        return None
    normalized = _normalize(reference).replace(" ", "-")
    if normalized.startswith("est-") or re.fullmatch(r"[a-z]{2,6}-\d{1,5}-20\d{2}", normalized):
        return "estudios"
    if re.fullmatch(r"\d{5}", normalized):
        return "produccion"
    return None


def _alias_in_text(text: str, alias: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) is not None


def _format_value(value: Any) -> str:
    if value is None:
        return "sin dato"
    if isinstance(value, bool):
        return "si" if value else "no"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    numeric = parse_decimal(value)
    if numeric is not None and not isinstance(value, bool):
        return f"{numeric:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
        year, month, day = value[:10].split("-")
        return f"{day}/{month}/{year}"
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        year, month, day = value[:10].split("-")
        return f"{day}/{month}/{year} {value[11:16]}"
    if isinstance(value, list):
        return f"{len(value)} elementos"
    return str(value)
