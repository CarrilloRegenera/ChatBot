import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


def _load_business_schema() -> Dict[str, Any]:
    schema_path = Path(__file__).with_name("business_schema.json")
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("No se pudo cargar business_schema.json: %s", exc)
        return {}


RAW_BUSINESS_SCHEMA = _load_business_schema()


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
