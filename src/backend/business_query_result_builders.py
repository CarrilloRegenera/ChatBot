from typing import Any, Callable, Dict, List


def build_source_info_response(
    parsed: Dict[str, Any],
    *,
    module: str,
    route: str,
    field_label: Callable[[str], str],
) -> Dict[str, Any]:
    source_name = "Estudios" if module == "estudios" else "Produccion"
    reference = parsed.get("reference")
    fields = parsed.get("fields") or []
    fields_text = ", ".join(field_label(field) for field in fields[:4]) or "el dato consultado"
    ref_text = f" para {reference}" if reference else ""
    return {
        "response": f"Este dato lo estoy consultando desde {source_name}{ref_text}. La pregunta se ha interpretado sobre {fields_text}.",
        "route": route,
        "confidence": 1.0,
        "sources": [{"source": f"AppRegenera SQL {source_name}", "module": module}],
    }


def build_estudios_result(
    detail: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    extract_estudios_value: Callable[[Dict[str, Any], str, Dict[str, Any]], Any],
) -> Dict[str, Any]:
    fields = []
    for key in parsed.get("fields") or []:
        value = extract_estudios_value(detail, key, parsed)
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


def build_produccion_result(
    detail: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    extract_produccion_value: Callable[[Dict[str, Any], str], Any],
    extract_periodo_month_value: Callable[[Dict[str, Any], Dict[str, Any]], Any],
    production_month_field_values: List[str],
) -> Dict[str, Any]:
    fields = []
    for key in parsed.get("fields") or []:
        value = extract_produccion_value(detail, key)
        if key == "periodosMensuales" and parsed.get("per_month"):
            value = detail.get("PeriodosMensuales") or []
        elif key in production_month_field_values and value is None:
            value = extract_periodo_month_value(detail, parsed)
        fields.append({"key": key, "value": value})

    return {
        "status": "ok",
        "entity": {
            "primaryCode": detail.get("CodigoObra") or detail.get("LicitacionNumeroProyecto") or detail.get("LicitacionNumeroOferta") or parsed.get("reference"),
            "displayName": detail.get("NombreObra") or parsed.get("reference"),
        },
        "fields": fields,
    }


def summarize_cierre(
    cierre: Dict[str, Any],
    *,
    match_cierre_fields: Callable[[Dict[str, Any], Dict[str, Any]], List[Dict[str, Any]]],
    format_value: Callable[[Any], str],
) -> str | None:
    values = match_cierre_fields(cierre, {"fields": []})
    if not values:
        return None
    titulo = cierre.get("Nombre") or cierre.get("Numero") or "la referencia solicitada"
    periodo = cierre.get("Periodo")
    period_copy = f" ({periodo})" if periodo else ""
    resumen = "; ".join(f"{item['label']} = {format_value(item['value'])}" for item in values[:5])
    return f"Cierre de {titulo}{period_copy}: {resumen}."


def build_no_data_message(
    match: Dict[str, Any],
    *,
    module: str,
    parsed: Dict[str, Any],
    build_period_text: Callable[[Dict[str, Any]], str],
    field_label: Callable[[str], str],
) -> str:
    prefix = "Licitacion" if module == "estudios" else "Obra"
    name = match.get("displayName") or parsed["reference"]
    code = match.get("primaryCode") or parsed["reference"]
    period_text = build_period_text(parsed)

    if module == "produccion" and parsed.get("per_month"):
        return f"{prefix} {code} {name}{period_text}: no hay produccion mensual cargada en AppRegenera para ese periodo."

    if parsed.get("month") and module == "produccion":
        return f"{prefix} {code} {name}{period_text}: no hay dato cargado para ese mes en AppRegenera."

    requested_labels = [field_label(field) for field in parsed.get("fields") or []]
    labels_text = ", ".join(requested_labels[:4]) if requested_labels else "el dato solicitado"
    return f"{prefix} {code} {name}{period_text}: no hay datos disponibles para {labels_text}."


def build_period_text(
    parsed: Dict[str, Any],
    *,
    month_labels: Dict[int, str],
    should_include_period_context: Callable[[Dict[str, Any]], bool],
) -> str:
    if not should_include_period_context(parsed):
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
        parts.append(month_labels.get(parsed["month"], f"mes {parsed['month']}"))
    return f" ({', '.join(parts)})" if parts else ""
