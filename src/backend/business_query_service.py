import logging
import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
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
    search_produccion as sql_search_produccion,
)
from config import APPREGENERA_DEV_BYPASS_KEY


logger = logging.getLogger(__name__)


def _load_business_schema() -> Dict[str, Any]:
    schema_path = Path(__file__).with_name("business_schema.json")
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("No se pudo cargar business_schema.json: %s", exc)
        return {}


RAW_BUSINESS_SCHEMA = _load_business_schema()


def _schema_module_hints(module: str) -> tuple[str, ...]:
    return tuple(
        _normalize_static(hint)
        for hint in ((BUSINESS_SCHEMA.get("modules") or {}).get(module) or {}).get("route_hints", [])
        if hint
    )


def _schema_scope_aliases(scope: str) -> tuple[str, ...]:
    return tuple(
        _normalize_static(alias)
        for alias in ((BUSINESS_SCHEMA.get("scopes") or {}).get(scope) or {}).get("aliases", [])
        if alias
    )


def _schema_field_entry(module: str, metric: str) -> Dict[str, Any]:
    fields = BUSINESS_SCHEMA.get("fields") or {}
    candidates = (
        f"{module}.{metric}",
        f"{module}.{(metric or '').lower()}",
    )
    for candidate in candidates:
        field = fields.get(candidate)
        if field:
            return field
    target_module = (module or "").strip().lower()
    target_metric = (metric or "").strip().lower()
    for key, value in fields.items():
        try:
            key_module, key_metric = key.split(".", 1)
        except ValueError:
            continue
        if key_module.strip().lower() == target_module and key_metric.strip().lower() == target_metric:
            return value
    return {}


def _schema_field_synonyms(module: str, metric: str) -> tuple[str, ...]:
    field = _schema_field_entry(module, metric)
    return tuple(_normalize_static(alias) for alias in field.get("synonyms", []) if alias)


def _schema_aggregation_aliases(kind: str) -> tuple[str, ...]:
    aggregate = (BUSINESS_SCHEMA.get("aggregations") or {}).get(kind) or {}
    return tuple(_normalize_static(alias) for alias in aggregate.get("aliases", []) if alias)


def _normalize_static(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^\w\s/-]", " ", normalized)
    return re.sub(r"\s+", " ", normalized)


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
    ("tipo", "tipo", ("tipo",)),
    ("tipoRegistro", "tipo de registro", ("tipo de registro", "tipo registro")),
    ("numeroOferta", "numero de oferta", ("numero de estudio", "n estudio", "estudio", "numero de oferta", "numero oferta", "n oferta")),
    ("numeroProyecto", "numero de proyecto", ("numero de proyecto", "numero proyecto", "n proyecto")),
    ("obra", "obra", ("obra", "nombre de la obra", "nombre obra")),
    ("cliente", "cliente", ("cliente",)),
    ("tipoObra", "tipo de obra", ("tipo de obra", "tipo obra")),
    ("tipologiaObra", "tipologia de obra", ("tipologia de obra", "tipologia obra", "tipologia")),
    ("estado", "estado", ("estado",)),
    ("situacionOferta", "situacion de la oferta", ("situacion de la oferta", "situacion oferta")),
    ("probabilidadAdjudicacion", "probabilidad de adjudicacion", ("probabilidad de adjudicacion", "probabilidad adjudicacion")),
    ("fechaPresentacion", "fecha de presentacion", ("fecha de presentacion", "fecha presentacion")),
    ("fechaApertura", "fecha de apertura", ("fecha de apertura", "fecha apertura")),
    ("fechaAdjudicacion", "fecha de adjudicacion", ("fecha de adjudicacion", "fecha adjudicacion")),
    ("periodoEjecucion", "periodo de ejecucion", ("periodo de ejecucion", "periodo ejecucion")),
    ("observaciones", "observaciones", ("observaciones", "observacion")),
    ("enlaceLicitacion", "enlace de licitacion", ("enlace de licitacion", "enlace licitacion", "url licitacion")),
    ("importeContratado", "importe contratado", ("importe contratado", "importe adjudicado", "presupuesto", "presupuesto adjudicado", "valor contratado")),
    ("importeContratadoPrevio", "importe contratado previo", ("importe previo", "importe contratado previo")),
    ("importeContratado2026C1", "importe contratado C1 2026", ("importe contratado c1 2026", "importe contratado primer cuatrimestre 2026")),
    ("importeContratado2026C2", "importe contratado C2 2026", ("importe contratado c2 2026", "importe contratado segundo cuatrimestre 2026")),
    ("importeContratado2026C3", "importe contratado C3 2026", ("importe contratado c3 2026", "importe contratado tercer cuatrimestre 2026")),
    ("produccion", "backlog", ("backlog", "produccion", "backlog total")),
    ("produccionPrevio", "backlog previo", ("backlog previo", "produccion previa", "produccion previo")),
    ("plan2026", "pipeline 2026", ("pipeline 2026", "plan 2026")),
    ("plan2027", "pipeline 2027", ("pipeline 2027", "plan 2027")),
    ("plan2028", "pipeline 2028", ("pipeline 2028", "plan 2028")),
    ("plan2029", "pipeline 2029", ("pipeline 2029", "plan 2029")),
    ("produccion2026", "backlog 2026", ("backlog 2026", "produccion 2026")),
    ("produccion2026C1", "backlog C1 2026", ("backlog c1 2026", "produccion c1 2026", "backlog primer cuatrimestre 2026")),
    ("produccion2026C2", "backlog C2 2026", ("backlog c2 2026", "produccion c2 2026", "backlog segundo cuatrimestre 2026")),
    ("produccion2026C3", "backlog C3 2026", ("backlog c3 2026", "produccion c3 2026", "backlog tercer cuatrimestre 2026")),
    ("produccion2027", "backlog 2027", ("backlog 2027", "produccion 2027")),
    ("produccion2028", "backlog 2028", ("backlog 2028", "produccion 2028")),
    ("produccion2029", "backlog 2029", ("backlog 2029", "produccion 2029")),
    ("concurso", "concurso", ("concurso", "esta en concurso", "esta en concurso?", "esta en concurso o no")),
    ("pendiente", "pendiente", ("pendiente",)),
]

PRODUCCION_FIELD_SPECS = [
    ("codigoObra", "codigo de obra", ("codigo de obra", "codigo obra")),
    ("nombreObra", "nombre de la obra", ("nombre de la obra", "nombre obra")),
    ("tipoCliente", "tipo de cliente", ("tipo de cliente", "tipo cliente")),
    ("finalizada", "finalizada", ("finalizada", "finalizado", "esta finalizada", "esta finalizado")),
    ("oculta", "oculta", ("oculta", "oculto", "esta oculta", "esta oculto")),
    ("responsableNombreCompleto", "responsable", ("responsable", "quien es el responsable", "responsable de la obra")),
    ("tipoObra", "tipo de obra", ("tipo de obra", "tipo obra")),
    ("importeContratado", "importe contratado", ("importe contratado", "importe adjudicado", "presupuesto", "presupuesto contratado", "valor contratado")),
    ("rentabilidadPrevista2026", "rentabilidad prevista 2026", ("rentabilidad prevista 2026", "rentabilidad 2026", "rentabilidad prevista", "rentabilidad", "margen previsto", "margen", "beneficio previsto")),
    ("produccionOrigen2025", "produccion origen 2025", ("produccion origen 2025",)),
    ("produccionOrigenAnosAnteriores", "produccion origen anos anteriores", ("produccion origen anos anteriores",)),
    ("ventaMaster2025", "venta master 2025", ("venta master 2025",)),
    ("porcentajeMateriales", "porcentaje de materiales", ("porcentaje de materiales", "porcentaje materiales")),
    ("porcentajeManoObra", "porcentaje de mano de obra", ("porcentaje de mano de obra", "porcentaje mano de obra")),
    ("cartera2026", "cartera 2026", ("cartera 2026", "cartera", "cartera pendiente", "pendiente por ejecutar")),
    ("pendiente2026", "pendiente 2026", ("pendiente 2026",)),
    ("diferencia", "diferencia", ("diferencia",)),
    ("comentarios", "comentarios", ("comentarios",)),
    ("responsableCodigo", "codigo del responsable", ("codigo del responsable", "responsable codigo")),
    ("licitacionNumeroProyecto", "numero de proyecto", ("numero de proyecto", "numero proyecto", "n proyecto")),
    ("licitacionNumeroOferta", "numero de estudio", ("numero de estudio", "estudio", "numero de oferta", "numero oferta", "n oferta")),
    ("licitacionCliente", "cliente", ("cliente",)),
    ("licitacionEstado", "estado", ("estado",)),
    ("produccionPrimerCuatrimestre", "produccion primer cuatrimestre", ("primer cuatrimestre", "c1")),
    ("produccionSegundoCuatrimestre", "produccion segundo cuatrimestre", ("segundo cuatrimestre", "c2")),
    ("produccionPrimerSegundoCuatrimestre", "produccion acumulada primer y segundo cuatrimestre", ("primer y segundo cuatrimestre", "primer segundo cuatrimestre", "c1 c2")),
    ("produccionEstimadaPendiente", "produccion estimada pendiente", ("produccion estimada pendiente", "estimada pendiente")),
    ("produccionEstimadaTercerCuatrimestre", "produccion tercer cuatrimestre", ("tercer cuatrimestre", "c3")),
    ("ultimaSincronizacionExcelUtc", "ultima sincronizacion excel", ("ultima sincronizacion excel", "ultima sincronizacion")),
]

PRODUCCION_ONLY_FALLBACK_BLOCK_FIELDS = {
    "rentabilidadPrevista2026",
    "cartera2026",
    "pendiente2026",
    "produccionTotal",
    "periodosMensuales",
    "produccionEnero",
    "produccionFebrero",
    "produccionMarzo",
    "produccionAbril",
    "produccionMayo",
    "produccionJunio",
    "produccionJulio",
    "produccionAgosto",
    "produccionSeptiembre",
    "produccionOctubre",
    "produccionNoviembre",
    "produccionEstimadaDiciembre",
    "produccionPrimerCuatrimestre",
    "produccionSegundoCuatrimestre",
    "produccionEstimadaTercerCuatrimestre",
    "produccionPrimerSegundoCuatrimestre",
    "produccionEstimadaPendiente",
}

FIELD_LABELS = {key: label for key, label, _ in LICITACION_FIELD_SPECS + PRODUCCION_FIELD_SPECS}
FIELD_LABELS.update(
    {
        "importeContratado2025": "importe contratado 2025",
        "importeContratado2026": "importe contratado 2026",
        "importeContratado2027": "importe contratado 2027",
        "importeContratado2028": "importe contratado 2028",
        "importeContratado2029": "importe contratado 2029",
        "produccion": "produccion total",
        "produccionPrevio": "produccion previa",
        "produccion2025": "produccion 2025",
        "produccion2026": "produccion 2026",
        "produccion2027": "produccion 2027",
        "produccion2028": "produccion 2028",
        "produccion2029": "produccion 2029",
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
        "periodosMensuales": "periodos mensuales",
        "produccionTotal": "produccion total",
        "licitacionProduccion2026": "produccion 2026",
        "licitacionProduccion2027": "produccion 2027",
        "licitacionProduccion2028": "produccion 2028",
        "licitacionProduccion2029": "produccion 2029",
        "cierre": "cierre",
    }
)

_FOLLOW_UP_PREFIXES = ("y ", "y para", "y el", "y la", "y los", "y las", "y de", "y del", "tambien", "tambien ")
SOURCE_INFO_PATTERNS = ("de estudios o produccion", "de estudios o de produccion", "de produccion o estudios", "de donde sale", "que modulo", "de que modulo")
CLOSURE_FIELD_HINTS = {
    "presupuesto total": "presupuestoTotal",
    "presupuesto vigente": "presupuestoVigente",
    "coste previsto final": "costePrevistoFinalObra",
    "produccion origen anio": "produccionOrigenAnio",
    "produccion origen ano": "produccionOrigenAnio",
    "produccion origen": "produccionOrigen",
    "produccion mes": "produccionMes",
    "coste origen anio": "costeOrigenAnio",
    "coste origen ano": "costeOrigenAnio",
    "coste origen": "costeOrigen",
    "produccion ejercicio mes anterior": "produccionEjercicioMesAnterior",
    "cartera anio": "carteraAnio",
    "cartera ano": "carteraAnio",
    "cartera pendiente anio": "carteraPendienteAnio",
    "cartera pendiente ano": "carteraPendienteAnio",
    "cartera 2027": "cartera2027",
    "cartera 2028": "cartera2028",
    "certificacion mes": "certificacionMes",
    "certificacion origen anio": "certificacionOrigenAnio",
    "certificacion origen ano": "certificacionOrigenAnio",
    "certificacion origen": "certificacionOrigen",
    "certificacion acumulada mes anterior": "certificacionAcumuladaMesAnterior",
    "costes mes": "costesMes",
    "costes mes n": "costesMesN",
    "total costes mes n": "totalCostesMesN",
    "coste previsto ejercicio": "costePrevistoEjercicio",
    "coste ejercicio": "costeEjercicio",
    "resultado actual ejercicio": "resultadoActualEjercicio",
    "resultado actual mes": "resultadoActualMes",
    "resultado origen": "resultadoOrigen",
    "resultado previsto ejercicio": "resultadoPrevistoEjercicio",
    "resultado previsto fin obra": "resultadoPrevistoFinObra",
    "ppc origen": "ppcOrigen",
    "ppc ejercicio": "ppcEjercicio",
    "ppc mes": "ppcMes",
    "publico privado": "publicoPrivado",
    "situacion": "situacion",
}


def _schema_field_key(module: str, field_name: str) -> str:
    return f"{module}.{(field_name or '').strip().lower()}"


def _default_sql_field_name(field_name: str) -> str:
    if not field_name:
        return ""
    return field_name[0].upper() + field_name[1:]


def _default_field_description(label: str, module: str) -> str:
    module_copy = {
        "estudios": "una licitacion o estudio",
        "produccion": "una obra en produccion",
        "cierre": "un cierre de produccion",
    }.get(module, "la entidad consultada")
    return f"Dato de {label} asociado a {module_copy}."


def _build_generated_business_fields() -> Dict[str, Dict[str, Any]]:
    generated: Dict[str, Dict[str, Any]] = {}

    for canonical, label, aliases in LICITACION_FIELD_SPECS:
        generated[_schema_field_key("estudios", canonical)] = {
            "table": "dbo.Licitaciones",
            "sql_field": _default_sql_field_name(canonical),
            "label": label,
            "human_description": _default_field_description(label, "estudios"),
            "synonyms": list(dict.fromkeys((label, *aliases))),
            "aggregations": [],
        }

    for canonical, label, aliases in PRODUCCION_FIELD_SPECS:
        table_name = "dbo.ProyectosProduccionSyncPeriodos" if canonical == "periodosMensuales" else "dbo.ProyectosProduccionSync"
        generated[_schema_field_key("produccion", canonical)] = {
            "table": table_name,
            "sql_field": _default_sql_field_name(canonical),
            "label": label,
            "human_description": _default_field_description(label, "produccion"),
            "synonyms": list(dict.fromkeys((label, *aliases))),
            "aggregations": [],
        }

    numeric_fields = {
        "estudios": {
            "importeContratado",
            "importeContratadoPrevio",
            "importeContratado2026C1",
            "importeContratado2026C2",
            "importeContratado2026C3",
            "produccion",
            "produccionPrevio",
            "plan2026",
            "plan2027",
            "plan2028",
            "plan2029",
            "produccion2026",
            "produccion2026C1",
            "produccion2026C2",
            "produccion2026C3",
            "produccion2027",
            "produccion2028",
            "produccion2029",
        },
        "produccion": {
            "importeContratado",
            "rentabilidadPrevista2026",
            "produccionOrigen2025",
            "produccionOrigenAnosAnteriores",
            "ventaMaster2025",
            "porcentajeMateriales",
            "porcentajeManoObra",
            "cartera2026",
            "pendiente2026",
            "diferencia",
            "produccionPrimerCuatrimestre",
            "produccionSegundoCuatrimestre",
            "produccionPrimerSegundoCuatrimestre",
            "produccionEstimadaPendiente",
            "produccionEstimadaTercerCuatrimestre",
            *PRODUCTION_MONTH_FIELDS.values(),
            "produccionTotal",
        },
    }
    aggregate_kinds = ["sum", "avg", "top"]
    for module_name, field_names in numeric_fields.items():
        for field_name in field_names:
            key = _schema_field_key(module_name, field_name)
            if key in generated:
                generated[key]["aggregations"] = aggregate_kinds

    for label, field_name in CLOSURE_FIELD_HINTS.items():
        generated[_schema_field_key("cierre", field_name)] = {
            "table": "dbo.CierresProduccionValores",
            "sql_field": field_name,
            "label": label,
            "human_description": _default_field_description(label, "cierre"),
            "synonyms": [label],
            "aggregations": aggregate_kinds,
        }

    generated[_schema_field_key("estudios", "pipeline")] = {
        "table": "dbo.Licitaciones",
        "sql_field": "Plan2026, Plan2027, Plan2028, Plan2029",
        "label": "pipeline",
        "human_description": "Plan o pipeline previsto de licitaciones pendientes o potenciales.",
        "synonyms": ["pipeline", "plan", "prevision", "potencial"],
        "aggregations": aggregate_kinds,
    }
    generated[_schema_field_key("estudios", "backlog")] = {
        "table": "dbo.Licitaciones",
        "sql_field": "Produccion, Produccion2026, Produccion2027, Produccion2028, Produccion2029",
        "label": "backlog",
        "human_description": "Produccion prevista de licitaciones adjudicadas o completadas.",
        "synonyms": ["backlog", "produccion adjudicada", "produccion prevista", "trabajo adjudicado"],
        "aggregations": aggregate_kinds,
    }
    generated[_schema_field_key("produccion", "producciontotal")] = {
        "table": "dbo.ProyectosProduccionSync",
        "sql_field": "ProduccionTotal",
        "label": "produccion total",
        "human_description": "Produccion total acumulada de la obra en produccion.",
        "synonyms": ["produccion total", "produccion acumulada", "total producido"],
        "aggregations": aggregate_kinds,
    }
    generated[_schema_field_key("produccion", "cartera2027")] = {
        "table": "dbo.CierresProduccionValores",
        "sql_field": "cartera2027",
        "label": "cartera 2027",
        "human_description": "Cartera para 2027 reflejada en cierres.",
        "synonyms": ["cartera 2027"],
        "aggregations": aggregate_kinds,
    }
    generated[_schema_field_key("produccion", "cartera2028")] = {
        "table": "dbo.CierresProduccionValores",
        "sql_field": "cartera2028",
        "label": "cartera 2028",
        "human_description": "Cartera para 2028 reflejada en cierres.",
        "synonyms": ["cartera 2028"],
        "aggregations": aggregate_kinds,
    }
    return generated


def _augment_business_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    merged = json.loads(json.dumps(schema or {}))
    merged.setdefault("fields", {})
    for key, generated_field in _build_generated_business_fields().items():
        existing = merged["fields"].get(key) or {}
        merged["fields"][key] = {
            **generated_field,
            **existing,
            "synonyms": list(dict.fromkeys([*(generated_field.get("synonyms") or []), *(existing.get("synonyms") or [])])),
            "aggregations": list(dict.fromkeys([*(generated_field.get("aggregations") or []), *(existing.get("aggregations") or [])])),
        }
    return merged


BUSINESS_SCHEMA = _augment_business_schema(RAW_BUSINESS_SCHEMA)


def _extract_year_suffixes(field_names: List[str], prefix: str) -> List[int]:
    years: List[int] = []
    for field_name in field_names:
        match = re.fullmatch(rf"{re.escape(prefix)}(20\d{{2}})", field_name)
        if match:
            years.append(int(match.group(1)))
    return sorted(dict.fromkeys(years))


_ALL_FIELD_NAMES = list(FIELD_LABELS.keys())
LICITACION_IMPORTE_YEARS = _extract_year_suffixes(_ALL_FIELD_NAMES, "importeContratado")
LICITACION_PLAN_YEARS = _extract_year_suffixes(_ALL_FIELD_NAMES, "plan")
LICITACION_PRODUCCION_YEARS = _extract_year_suffixes(_ALL_FIELD_NAMES, "produccion")
PRODUCCION_LINKED_YEARS = _extract_year_suffixes(_ALL_FIELD_NAMES, "licitacionProduccion")

STUDIES_AGGREGATE_KEYWORDS = {
    "pipeline": ("pipeline", "plan "),
    "backlog": ("backlog",),
    "importecontratado": ("importe contratado", "importe adjudicado"),
    "importecontratadoprevio": ("importe previo", "importe contratado previo"),
    "produccion": ("produccion",),
}

PRODUCCION_AGGREGATE_KEYWORDS = {
    "cartera2026": ("cartera",),
    "pendiente2026": ("pendiente 2026",),
    "diferencia": ("diferencia",),
    "importecontratado": ("importe contratado", "importe adjudicado"),
    "rentabilidadprevista2026": ("rentabilidad prevista", "rentabilidad 2026", "rentabilidad"),
    "produccionorigen2025": ("produccion origen 2025",),
    "produccionorigenanosanteriores": ("produccion origen anos anteriores",),
    "ventamaster2025": ("venta master 2025",),
    "porcentajemateriales": ("porcentaje de materiales", "porcentaje materiales"),
    "porcentajemanoobra": ("porcentaje de mano de obra", "porcentaje mano de obra"),
    "produccionprimercuatrimestre": ("primer cuatrimestre", "c1"),
    "produccionsegundocuatrimestre": ("segundo cuatrimestre", "c2"),
    "produccionprimersegundocuatrimestre": ("primer y segundo cuatrimestre", "primer segundo cuatrimestre", "c1 c2"),
    "produccionestimadapendiente": ("produccion estimada pendiente", "estimada pendiente"),
    "produccionestimadatercercuatrimestre": ("tercer cuatrimestre", "c3"),
    "producciontotal": ("produccion total",),
}


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
            "filter_text": aggregate.get("filter_text"),
            "group_by": group_by,
        }
    )
    return parsed


def detect_business_route(question: str) -> str | None:
    text = _normalize(question)
    explicit_scope = _detect_explicit_scope(text)
    if explicit_scope == "estudios":
        return "business_licitaciones"
    if explicit_scope == "produccion":
        return "business_produccion"
    reference = _extract_reference(question, text)
    if reference:
        reference_module = _detect_reference_module(reference)
        if reference_module == "estudios":
            return "business_licitaciones"
        if reference_module == "produccion":
            return "business_produccion"
        if re.fullmatch(r"\d{5}", reference):
            return "business_produccion"

    if any(hint in text for hint in ("control de produccion", "cierre", "cartera", "rentabilidad", "produccion estimada", "produccion marzo", "produccion abril")):
        return "business_produccion"
    if any(hint in text for hint in _schema_module_hints("cierre")):
        return "business_produccion"
    if any(hint in text for hint in _schema_module_hints("produccion")):
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
            detail_result = _answer_estudios_detail_sql(parsed, route=route)
        else:
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
        return None

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
    reference = _resolve_reference(question, normalized, history)
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
    aggregate = _detect_aggregate(question, normalized, module=module, fields=fields, year=year, reference=reference)
    expected_client = _extract_expected_client(question, normalized)
    return {
        "question": question,
        "reference": reference,
        "fields": fields,
        "year": year,
        "years": years,
        "cuatrimestre": cuatrimestre,
        "month": month,
        "per_month": per_month,
        "per_year": per_year,
        "aggregate": aggregate,
        "expected_client": expected_client,
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
