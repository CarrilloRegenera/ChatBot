import json
from pathlib import Path
from typing import List, Tuple


_DOMAINS_CONFIG_PATH = Path(__file__).parent / "domains.json"


def normalize_prefix(prefix: str) -> str:
    cleaned = (prefix or "").strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def _load_blob_scopes_from_domains() -> List[Tuple[str, str]]:
    """Derive Azure Blob scopes from domains.json."""
    try:
        with open(_DOMAINS_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return []

    scopes: List[Tuple[str, str]] = []
    for domain_name, domain_cfg in cfg.get("domains", {}).items():
        if not domain_cfg.get("blob_sync", True):
            continue
        prefixes = domain_cfg.get("folder_prefixes", [])
        if not prefixes:
            continue
        primary = normalize_prefix(str(prefixes[0]))
        if primary:
            scopes.append((domain_name, primary))
    return scopes


def configured_blob_scopes(default_prefix: str = "", *scoped_prefixes: str) -> List[Tuple[str, str]]:
    """Return active Blob scopes for document sync.

    Explicit scoped prefixes keep backward compatibility with the old env-var
    flow. When no explicit scoped prefixes are provided, scopes are derived from
    domains.json so adding a new domain can be done from configuration.
    """
    legacy_names = (
        "alta_tension",
        "baja_tension",
        "fotovoltaica_om",
        "grupos_electrogenos",
        "guias_tecnicas",
        "rite",
    )
    if scoped_prefixes:
        padded = list(scoped_prefixes[: len(legacy_names)])
        padded.extend([""] * (len(legacy_names) - len(padded)))
        explicit = [
            (category, normalize_prefix(prefix))
            for category, prefix in zip(legacy_names, padded)
        ]
        active_explicit = [(category, prefix) for category, prefix in explicit if prefix]
        if active_explicit:
            return active_explicit
        return [("", normalize_prefix(default_prefix))]

    domain_scopes = _load_blob_scopes_from_domains()
    if domain_scopes:
        return domain_scopes

    return [("", normalize_prefix(default_prefix))]
