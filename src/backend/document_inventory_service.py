from typing import Callable, Dict, List


_DOMAIN_DISPLAY_NAMES = {
    "alta_tension": "Alta tension",
    "baja_tension": "Baja tension",
    "fotovoltaica_om": "Fotovoltaica O&M",
    "grupos_electrogenos": "Grupos electrogenos",
    "guias_tecnicas": "Guias tecnicas",
    "rite": "RITE",
    "ops": "OPS",
}


def group_indexed_sources(indexed_sources: Dict[str, str]) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for source in sorted(indexed_sources):
        normalized = str(source or "").replace("\\", "/").strip("/")
        if not normalized:
            continue
        parts = normalized.split("/")
        domain = parts[0]
        grouped.setdefault(domain, []).append(normalized)
    return grouped


def inventory_focus_domains(
    question: str,
    grouped_sources: Dict[str, List[str]],
    *,
    detect_hint_domains: Callable[[str], List[str]],
) -> List[str]:
    return [
        domain
        for domain in detect_hint_domains(question or "")
        if domain in grouped_sources
    ]


def source_display_name(source: str) -> str:
    normalized = str(source or "").replace("\\", "/").strip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def format_document_inventory_response(
    indexed_sources: Dict[str, str],
    question: str = "",
    *,
    detect_hint_domains: Callable[[str], List[str]],
) -> str:
    grouped = group_indexed_sources(indexed_sources)
    if not grouped:
        return (
            "Ahora mismo no veo documentos tecnicos indexados. "
            "Cuando termine la sincronizacion documental podre listar los documentos disponibles."
        )

    focus_domains = inventory_focus_domains(
        question,
        grouped,
        detect_hint_domains=detect_hint_domains,
    )
    if focus_domains:
        domains_to_render = focus_domains
        if len(focus_domains) == 1:
            title = f"Los documentos que tenemos en {_DOMAIN_DISPLAY_NAMES.get(focus_domains[0], focus_domains[0])} son:"
        else:
            title = "Los documentos que tenemos en esos bloques son:"
    else:
        domains_to_render = sorted(grouped)
        title = "Los documentos tecnicos disponibles son:"

    lines = [title]
    seen_names: set[str] = set()
    for domain in domains_to_render:
        for source in grouped.get(domain, []):
            name = source_display_name(source)
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            lines.append(f"- {name}")
    return "\n".join(lines)
