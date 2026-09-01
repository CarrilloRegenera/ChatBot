"""Inventario persistente de las fuentes que alimentan cada backend RAG."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Callable, Dict

from database import db_conn


def _normalize_source_path(source_path: str) -> str:
    return str(source_path or "").replace("\\", "/").strip("/")


def _source_key(backend: str, source_path: str) -> str:
    value = f"{backend.strip().lower()}\0{source_path}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def build_source_record(
    source_path: str,
    file_hash: str,
    *,
    backend: str,
    profile_resolver: Callable[[str], Dict[str, str]],
) -> Dict[str, str]:
    """Construye el registro común que comparten Chroma y Azure AI Search."""
    normalized_path = _normalize_source_path(source_path)
    profile = profile_resolver(normalized_path)
    return {
        "backend": backend.strip().lower(),
        "source_key": _source_key(backend, normalized_path),
        "source_path": normalized_path,
        "document_name": PurePosixPath(normalized_path).name,
        "file_hash": str(file_hash or ""),
        "department": str(profile.get("department", "")),
        "domain": str(profile.get("domain", "")),
        "document_type": str(profile.get("document_type", "")),
        "authority_layer": str(profile.get("document_layer", "")),
    }


def sync_document_source_registry(
    indexed_sources: Dict[str, str],
    *,
    backend: str,
    profile_resolver: Callable[[str], Dict[str, str]],
) -> int:
    """Refleja el índice actual sin alterar los campos de gobierno manuales.

    La tabla conserva documentos retirados para que su baja sea auditable. Las
    columnas Propietario, VersionDocumento, FechaRevision y EstadoVigencia no
    se actualizan aquí: se completarán por el responsable documental.
    """
    normalized_backend = backend.strip().lower()
    records = [
        build_source_record(
            source_path,
            file_hash,
            backend=normalized_backend,
            profile_resolver=profile_resolver,
        )
        for source_path, file_hash in indexed_sources.items()
        if _normalize_source_path(source_path)
    ]

    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE dbo.DocumentosFuente
            SET EstadoIndexacion = 'retirado', ActualizadoEn = SYSUTCDATETIME()
            WHERE Backend = ? AND EstadoIndexacion = 'indexado';
            """,
            normalized_backend,
        )
        for record in records:
            cursor.execute(
                """
                MERGE dbo.DocumentosFuente AS target
                USING (SELECT ? AS SourceKey) AS source
                ON target.SourceKey = source.SourceKey
                WHEN MATCHED THEN UPDATE SET
                    Backend = ?,
                    RutaFuente = ?,
                    NombreDocumento = ?,
                    HashContenido = ?,
                    Departamento = ?,
                    Dominio = ?,
                    TipoDocumento = ?,
                    CapaAutoridad = ?,
                    EstadoIndexacion = 'indexado',
                    UltimaIndexacion = SYSUTCDATETIME(),
                    UltimaDeteccion = SYSUTCDATETIME(),
                    ActualizadoEn = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT (
                    Backend, SourceKey, RutaFuente, NombreDocumento, HashContenido,
                    Departamento, Dominio, TipoDocumento, CapaAutoridad,
                    EstadoIndexacion, EstadoVigencia, UltimaIndexacion, UltimaDeteccion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexado', 'pendiente_revision',
                    SYSUTCDATETIME(), SYSUTCDATETIME());
                """,
                record["source_key"],
                record["backend"],
                record["source_path"],
                record["document_name"],
                record["file_hash"],
                record["department"],
                record["domain"],
                record["document_type"],
                record["authority_layer"],
                record["backend"],
                record["source_key"],
                record["source_path"],
                record["document_name"],
                record["file_hash"],
                record["department"],
                record["domain"],
                record["document_type"],
                record["authority_layer"],
            )
    return len(records)
