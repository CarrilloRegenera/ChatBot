import json
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Dict, Iterable, List

import pyodbc

from config import APPREGENERA_SQL_CONNECTION_STRING
from database import _normalize_sql_driver


def _prepare_appregenera_connection_string(connection_string: str) -> str:
    normalized = (connection_string or "").strip()
    if not normalized:
        return ""
    parts = {}
    for chunk in normalized.split(";"):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        parts[key.strip().lower()] = value.strip()

    odbc_parts = {
        "server": parts.get("server", ""),
        "database": parts.get("database") or parts.get("initial catalog", ""),
        "uid": parts.get("uid") or parts.get("user id", ""),
        "pwd": parts.get("pwd") or parts.get("password", ""),
        "encrypt": _normalize_bool(parts.get("encrypt", "yes")),
        "trustservercertificate": _normalize_bool(parts.get("trustservercertificate", "no")),
        "connection timeout": parts.get("connection timeout", "30"),
    }

    if "driver" in parts:
        odbc_prefix = f"Driver={{{parts['driver']}}};"
    else:
        odbc_prefix = "Driver={ODBC Driver 17 for SQL Server};"

    rebuilt = (
        f"{odbc_prefix}"
        f"Server={odbc_parts['server']};"
        f"Database={odbc_parts['database']};"
        f"UID={odbc_parts['uid']};"
        f"PWD={odbc_parts['pwd']};"
        f"Encrypt={odbc_parts['encrypt']};"
        f"TrustServerCertificate={odbc_parts['trustservercertificate']};"
        f"Connection Timeout={odbc_parts['connection timeout']};"
    )
    return _normalize_sql_driver(rebuilt)


def _normalize_bool(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return "yes"
    if normalized in {"false", "0", "no", "n"}:
        return "no"
    return value or ""


_APPREGENERA_SQL_CONNECTION_STRING = _prepare_appregenera_connection_string(APPREGENERA_SQL_CONNECTION_STRING)

LICITACION_IMPORTE_YEAR_SQL = {
    2026: "COALESCE(ImporteContratado2026, ImporteContratado, 0)",
    2027: "COALESCE(ImporteContratado2027, 0)",
    2028: "COALESCE(ImporteContratado2028, 0)",
    2029: "COALESCE(ImporteContratado2029, 0)",
}
LICITACION_PLAN_YEAR_SQL = {
    2026: "COALESCE(Plan2026, 0)",
    2027: "COALESCE(Plan2027, 0)",
    2028: "COALESCE(Plan2028, 0)",
    2029: "COALESCE(Plan2029, 0)",
}
LICITACION_PRODUCCION_YEAR_SQL = {
    2026: "COALESCE(Produccion2026, Produccion, 0)",
    2027: "COALESCE(Produccion2027, 0)",
    2028: "COALESCE(Produccion2028, 0)",
    2029: "COALESCE(Produccion2029, 0)",
}


def is_available() -> bool:
    return bool(_APPREGENERA_SQL_CONNECTION_STRING)


@contextmanager
def appregenera_conn():
    if not _APPREGENERA_SQL_CONNECTION_STRING:
        raise RuntimeError("APPREGENERA_SQL_CONNECTION_STRING no configurada")
    conn = pyodbc.connect(_APPREGENERA_SQL_CONNECTION_STRING)
    try:
        yield conn
    finally:
        conn.close()


def _rows_to_dicts(cursor) -> List[Dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    items: List[Dict[str, Any]] = []
    for row in cursor.fetchall():
        item = {}
        for index, column in enumerate(columns):
            value = row[index]
            if isinstance(value, Decimal):
                value = float(value)
            item[column] = value
        items.append(item)
    return items


def search_licitaciones(reference: str, take: int = 10) -> List[Dict[str, Any]]:
    like = f"%{reference.strip()}%"
    with appregenera_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP (?) Id, NumeroProyecto, NumeroOferta, Obra, Cliente, Estado, TipoObra,
                   ImporteContratado, Produccion, Plan2026, Plan2027, Plan2028, Plan2029,
                   Produccion2026, Produccion2027, Produccion2028, Produccion2029, ProduccionPrevio
            FROM dbo.Licitaciones
            WHERE NumeroProyecto LIKE ?
               OR NumeroOferta LIKE ?
               OR Obra LIKE ?
               OR Cliente LIKE ?
            ORDER BY
                CASE WHEN NumeroProyecto = ? THEN 0
                     WHEN NumeroOferta = ? THEN 1
                     WHEN NumeroProyecto LIKE ? THEN 2
                     WHEN NumeroOferta LIKE ? THEN 3
                     ELSE 4 END,
                NumeroProyecto,
                NumeroOferta
            """,
            take,
            like,
            like,
            like,
            like,
            reference,
            reference,
            like,
            like,
        )
        return _rows_to_dicts(cursor)


def get_licitacion_detail(entity_id: str) -> Dict[str, Any] | None:
    with appregenera_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP 1 *
            FROM dbo.Licitaciones
            WHERE Id = ?
            """,
            entity_id,
        )
        rows = _rows_to_dicts(cursor)
        if not rows:
            return None
        lic = rows[0]

        cursor.execute(
            """
            SELECT Anio, Cuatrimestre, ImporteContratado, Produccion, ImporteContratadoPrevisto, ProduccionPrevista
            FROM dbo.LicitacionesDistribuciones
            WHERE LicitacionId = ?
            ORDER BY Anio, Cuatrimestre
            """,
            entity_id,
        )
        lic["Distribuciones"] = _rows_to_dicts(cursor)

        cursor.execute(
            """
            SELECT Anio, Mes, Importe
            FROM dbo.LicitacionesImportesContratadosMensuales
            WHERE LicitacionId = ?
            ORDER BY Anio, Mes
            """,
            entity_id,
        )
        lic["ImportesMensuales"] = _rows_to_dicts(cursor)
        return lic


def query_licitaciones_aggregate(
    *,
    select_field: str,
    agg: str,
    top: int = 1,
    year: int | None = None,
    scope: str | None = None,
    free_text: str | None = None,
    order: str | None = None,
) -> List[Dict[str, Any]]:
    agg_sql = "AVG" if agg == "avg" else ("SUM" if agg == "sum" else "MAX")
    filters = []
    params: List[Any] = []

    if free_text:
        like = f"%{free_text}%"
        filters.append(
            "("
            "NumeroProyecto LIKE ? OR NumeroOferta LIKE ? OR Obra LIKE ? OR Cliente LIKE ? "
            "OR TipoObra LIKE ? OR Estado LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like])

    if scope == "pipeline":
        filters.append(
            "("
            "Estado LIKE 'Pendiente de Presentar%' OR Estado LIKE 'Pde. Presentar%' "
            "OR Estado LIKE 'Pendiente de Adjudicar%' OR Estado LIKE 'Pde. Adjudicar%' "
            "OR Estado LIKE 'Potencial%'"
            ")"
        )
    elif scope == "backlog":
        filters.append("(Estado LIKE 'Adjudicada%' OR Estado LIKE 'Completada%')")

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    if agg == "count":
        with appregenera_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT COUNT(*) AS Valor
                FROM dbo.Licitaciones
                {where_clause}
                """,
                *params,
            )
            return _rows_to_dicts(cursor)

    field_sql = _resolve_licitacion_field_sql(select_field, year=year, scope=scope)
    order_sql = (
        "COALESCE(FechaAdjudicacion, UpdatedDate, CreatedDate) DESC, NumeroProyecto DESC, NumeroOferta DESC"
        if order == "latest"
        else f"{field_sql} DESC, NumeroProyecto, NumeroOferta"
    )

    with appregenera_conn() as conn:
        cursor = conn.cursor()
        if agg == "top":
            cursor.execute(
                f"""
                SELECT TOP (?) NumeroProyecto, NumeroOferta, Obra, Cliente, Estado, TipoObra, {field_sql} AS Valor
                FROM dbo.Licitaciones
                {where_clause}
                ORDER BY {order_sql}
                """,
                top,
                *params,
            )
        elif agg == "avg" and top > 1:
            avg_filters = [*filters, f"{field_sql} <> 0"]
            avg_where_clause = f"WHERE {' AND '.join(avg_filters)}" if avg_filters else ""
            cursor.execute(
                f"""
                SELECT AVG(Valor) AS Valor
                FROM (
                    SELECT TOP (?) {field_sql} AS Valor
                    FROM dbo.Licitaciones
                    {avg_where_clause}
                    ORDER BY {order_sql}
                ) ranked
                """,
                top,
                *params,
            )
        else:
            cursor.execute(
                f"""
                SELECT {agg_sql}({field_sql}) AS Valor
                FROM dbo.Licitaciones
                {where_clause}
                """,
                *params,
            )
        return _rows_to_dicts(cursor)


def _resolve_licitacion_field_sql(select_field: str, *, year: int | None, scope: str | None) -> str:
    field = (select_field or "").strip().lower()
    if field == "importecontratado":
        if year is not None:
            return LICITACION_IMPORTE_YEAR_SQL.get(year, "0")
        return "COALESCE(ImporteContratado, 0)"
    if field == "pipeline":
        if year is not None:
            return LICITACION_PLAN_YEAR_SQL.get(year, "0")
        return " + ".join(LICITACION_PLAN_YEAR_SQL[plan_year] for plan_year in sorted(LICITACION_PLAN_YEAR_SQL))
    if field == "backlog":
        if year is not None:
            return LICITACION_PRODUCCION_YEAR_SQL.get(year, "0")
        return "COALESCE(Produccion, 0) + " + " + ".join(
            LICITACION_PRODUCCION_YEAR_SQL[prod_year]
            for prod_year in sorted(year_key for year_key in LICITACION_PRODUCCION_YEAR_SQL if year_key != 2026)
        )
    if field == "produccion":
        if year is not None:
            return LICITACION_PRODUCCION_YEAR_SQL.get(year, "0")
        return "COALESCE(Produccion, 0)"
    if field == "importecontratadoprevio":
        return "COALESCE(ImporteContratadoPrevio, 0)"
    return "COALESCE(ImporteContratado, 0)"


def search_produccion(reference: str, take: int = 10) -> List[Dict[str, Any]]:
    like = f"%{reference.strip()}%"
    with appregenera_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP (?) p.Id, p.CodigoObra, p.NombreObra, p.TipoObra, p.ImporteContratado, p.Cartera2026,
                   p.ProduccionMarzo, p.ProduccionAbril, p.ProduccionMayo, p.ProduccionJunio,
                   p.ProduccionJulio, p.ProduccionSeptiembre, p.Pendiente2026, p.Diferencia, p.Comentarios,
                   l.NumeroProyecto, l.NumeroOferta, l.Cliente, l.Estado
            FROM dbo.ProyectosProduccionSync p
            LEFT JOIN dbo.Licitaciones l ON l.Id = p.LicitacionId
            WHERE p.CodigoObra LIKE ?
               OR p.NombreObra LIKE ?
               OR COALESCE(l.NumeroProyecto, '') LIKE ?
               OR COALESCE(l.NumeroOferta, '') LIKE ?
               OR COALESCE(l.Cliente, '') LIKE ?
            ORDER BY
                CASE WHEN p.CodigoObra = ? THEN 0
                     WHEN COALESCE(l.NumeroProyecto, '') = ? THEN 1
                     WHEN COALESCE(l.NumeroOferta, '') = ? THEN 2
                     ELSE 3 END,
                p.CodigoObra
            """,
            take,
            like,
            like,
            like,
            like,
            like,
            reference,
            reference,
            reference,
        )
        return _rows_to_dicts(cursor)


def get_produccion_detail(entity_id: str, year: int | None = None) -> Dict[str, Any] | None:
    with appregenera_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP 1 p.*, l.NumeroProyecto AS LicitacionNumeroProyecto, l.NumeroOferta AS LicitacionNumeroOferta,
                   l.Cliente AS LicitacionCliente, l.Estado AS LicitacionEstado,
                   l.Produccion AS LicitacionProduccion, l.Produccion2026 AS LicitacionProduccion2026,
                   l.Produccion2027 AS LicitacionProduccion2027, l.Produccion2028 AS LicitacionProduccion2028,
                   l.Produccion2029 AS LicitacionProduccion2029,
                   e.Nombre AS ResponsableNombre, e.Apellidos AS ResponsableApellidos
            FROM dbo.ProyectosProduccionSync p
            LEFT JOIN dbo.Licitaciones l ON l.Id = p.LicitacionId
            LEFT JOIN dbo.Empleados e ON e.Id = p.ResponsableId
            WHERE p.Id = ?
            """,
            entity_id,
        )
        rows = _rows_to_dicts(cursor)
        if not rows:
            return None
        item = rows[0]

        cursor.execute(
            """
            SELECT Anio, Mes, Importe
            FROM dbo.ProyectosProduccionSyncPeriodos
            WHERE ProyectoProduccionSyncId = ?
              AND (? IS NULL OR Anio = ?)
            ORDER BY Anio, Mes
            """,
            entity_id,
            year,
            year,
        )
        item["PeriodosMensuales"] = _rows_to_dicts(cursor)
        item["ResponsableNombreCompleto"] = " ".join(
            part for part in [item.get("ResponsableNombre"), item.get("ResponsableApellidos")] if part
        ).strip() or None
        return item


def get_cierre_detail(reference: str, periodo: str | None = None, area: str | None = None) -> Dict[str, Any] | None:
    like = f"%{reference.strip()}%"
    with appregenera_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP 1 f.Id, f.Area, f.Periodo, f.ProyectoBc, f.Numero, f.Tipo, f.Nombre, f.ValoresJson, f.LicitacionId
            FROM dbo.CierresProduccionFilasImportadas f
            WHERE (f.Numero LIKE ? OR f.ProyectoBc LIKE ? OR f.Nombre LIKE ?)
              AND (? IS NULL OR f.Periodo = ?)
              AND (? IS NULL OR f.Area = ?)
            ORDER BY f.UpdatedDate DESC
            """,
            like,
            like,
            like,
            periodo,
            periodo,
            area,
            area,
        )
        rows = _rows_to_dicts(cursor)
        if not rows:
            return None
        row = rows[0]
        valores = json.loads(row.get("ValoresJson") or "{}")
        row["Valores"] = valores
        effective_periodo = periodo or row.get("Periodo")
        if row.get("LicitacionId"):
            cursor.execute(
                """
                SELECT Campo, Valor, Periodo
                FROM dbo.CierresProduccionValores
                WHERE LicitacionId = ?
                  AND (? IS NULL OR Periodo = ?)
                ORDER BY UpdatedDate DESC
                """,
                row["LicitacionId"],
                effective_periodo,
                effective_periodo,
            )
            row["ValoresNormalizados"] = _rows_to_dicts(cursor)
        else:
            row["ValoresNormalizados"] = []
        return row


def query_produccion_aggregate(
    *,
    select_field: str,
    agg: str,
    top: int = 1,
    free_text: str | None = None,
    order: str | None = None,
) -> List[Dict[str, Any]]:
    field_sql = _resolve_produccion_field_sql(select_field) if agg != "count" else ""
    filters = [] if agg == "count" else [f"{field_sql} IS NOT NULL"]
    params: List[Any] = []

    if free_text:
        like = f"%{free_text}%"
        filters.append(
            "("
            "p.CodigoObra LIKE ? OR p.NombreObra LIKE ? OR COALESCE(l.NumeroProyecto, '') LIKE ? "
            "OR COALESCE(l.NumeroOferta, '') LIKE ? OR COALESCE(l.Cliente, '') LIKE ? "
            "OR COALESCE(p.TipoObra, '') LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like])

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    if agg == "count":
        with appregenera_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT COUNT(*) AS Valor
                FROM dbo.ProyectosProduccionSync p
                LEFT JOIN dbo.Licitaciones l ON l.Id = p.LicitacionId
                {where_clause}
                """,
                *params,
            )
            return _rows_to_dicts(cursor)

    order_sql = (
        "COALESCE(p.UpdatedDate, p.CreatedDate) DESC, p.CodigoObra DESC"
        if order == "latest"
        else f"{field_sql} DESC, p.CodigoObra"
    )
    with appregenera_conn() as conn:
        cursor = conn.cursor()
        if agg == "top":
            cursor.execute(
                f"""
                SELECT TOP (?) p.CodigoObra, p.NombreObra, p.TipoObra, p.ImporteContratado,
                       l.NumeroProyecto, l.NumeroOferta, l.Cliente, {field_sql} AS Valor
                FROM dbo.ProyectosProduccionSync p
                LEFT JOIN dbo.Licitaciones l ON l.Id = p.LicitacionId
                {where_clause}
                ORDER BY {order_sql}
                """,
                top,
                *params,
            )
        elif agg == "avg" and top > 1:
            avg_filters = [*filters, f"{field_sql} <> 0"]
            avg_where_clause = f"WHERE {' AND '.join(avg_filters)}" if avg_filters else ""
            cursor.execute(
                f"""
                SELECT AVG(Valor) AS Valor
                FROM (
                    SELECT TOP (?) {field_sql} AS Valor
                    FROM dbo.ProyectosProduccionSync p
                    LEFT JOIN dbo.Licitaciones l ON l.Id = p.LicitacionId
                    {avg_where_clause}
                    ORDER BY {order_sql}
                ) ranked
                """,
                top,
                *params,
            )
        else:
            agg_sql = "AVG" if agg == "avg" else "SUM"
            cursor.execute(
                f"""
                SELECT {agg_sql}({field_sql}) AS Valor
                FROM dbo.ProyectosProduccionSync p
                LEFT JOIN dbo.Licitaciones l ON l.Id = p.LicitacionId
                {where_clause}
                """,
                *params,
            )
        return _rows_to_dicts(cursor)


def _resolve_produccion_field_sql(select_field: str) -> str:
    field = (select_field or "").strip().lower()
    mapping = {
        "cartera2026": "COALESCE(p.Cartera2026, 0)",
        "pendiente2026": "COALESCE(p.Pendiente2026, 0)",
        "diferencia": "COALESCE(p.Diferencia, 0)",
        "importecontratado": "COALESCE(p.ImporteContratado, 0)",
        "rentabilidadprevista2026": "COALESCE(p.RentabilidadPrevista2026, 0)",
        "produccionorigen2025": "COALESCE(p.ProduccionOrigen2025, 0)",
        "produccionorigenanosanteriores": "COALESCE(p.ProduccionOrigenAnosAnteriores, 0)",
        "ventamaster2025": "COALESCE(p.VentaMaster2025, 0)",
        "porcentajemateriales": "COALESCE(p.PorcentajeMateriales, 0)",
        "porcentajemanoobra": "COALESCE(p.PorcentajeManoObra, 0)",
        "produccionenero": "COALESCE(p.ProduccionEnero, 0)",
        "produccionfebrero": "COALESCE(p.ProduccionFebrero, 0)",
        "produccionmarzo": "COALESCE(p.ProduccionMarzo, 0)",
        "produccionabril": "COALESCE(p.ProduccionAbril, 0)",
        "produccionmayo": "COALESCE(p.ProduccionMayo, 0)",
        "produccionjunio": "COALESCE(p.ProduccionJunio, 0)",
        "produccionjulio": "COALESCE(p.ProduccionJulio, 0)",
        "produccionjulioagosto": "COALESCE(p.ProduccionJulioAgosto, 0)",
        "produccionseptiembre": "COALESCE(p.ProduccionSeptiembre, 0)",
        "produccionestimadaoctubre": "COALESCE(p.ProduccionEstimadaOctubre, 0)",
        "produccionestimadanoviembre": "COALESCE(p.ProduccionEstimadaNoviembre, 0)",
        "produccionestimadadiciembre": "COALESCE(p.ProduccionEstimadaDiciembre, 0)",
        "produccionprimercuatrimestre": "COALESCE(p.ProduccionPrimerCuatrimestre, 0)",
        "produccionsegundocuatrimestre": "COALESCE(p.ProduccionSegundoCuatrimestre, 0)",
        "produccionprimersegundocuatrimestre": "COALESCE(p.ProduccionPrimerSegundoCuatrimestre, 0)",
        "produccionestimadapendiente": "COALESCE(p.ProduccionEstimadaPendiente, 0)",
        "produccionestimadatercercuatrimestre": "COALESCE(p.ProduccionEstimadaTercerCuatrimestre, 0)",
        "producciontotal": "("
        "COALESCE(p.ProduccionEnero, 0) + COALESCE(p.ProduccionFebrero, 0) + COALESCE(p.ProduccionMarzo, 0) + "
        "COALESCE(p.ProduccionAbril, 0) + COALESCE(p.ProduccionMayo, 0) + COALESCE(p.ProduccionJunio, 0) + "
        "COALESCE(p.ProduccionJulio, 0) + COALESCE(p.ProduccionJulioAgosto, 0) + COALESCE(p.ProduccionSeptiembre, 0) + "
        "COALESCE(p.ProduccionEstimadaOctubre, 0) + COALESCE(p.ProduccionEstimadaNoviembre, 0) + COALESCE(p.ProduccionEstimadaDiciembre, 0)"
        ")",
    }
    return mapping.get(field, "COALESCE(p.Cartera2026, 0)")


def query_cierre_aggregate(
    *,
    campo: str,
    agg: str,
    top: int = 1,
    periodo: str | None = None,
    area: str | None = None,
    free_text: str | None = None,
    order: str | None = None,
) -> List[Dict[str, Any]]:
    if agg == "count":
        filters = [
            "(? IS NULL OR f.Periodo = ?)",
            "(? IS NULL OR f.Area = ?)",
        ]
        params: List[Any] = [periodo, periodo, area, area]
        if free_text:
            like = f"%{free_text}%"
            filters.append(
                "("
                "COALESCE(l.NumeroProyecto, '') LIKE ? OR COALESCE(l.NumeroOferta, '') LIKE ? "
                "OR COALESCE(l.Obra, '') LIKE ? OR COALESCE(f.Nombre, '') LIKE ? OR COALESCE(f.ProyectoBc, '') LIKE ?"
                ")"
            )
            params.extend([like, like, like, like, like])
        where_clause = f"WHERE {' AND '.join(filters)}"
        with appregenera_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                WITH rows_latest AS (
                    SELECT
                        f.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(CONVERT(varchar(36), f.LicitacionId), f.Numero, f.Nombre), COALESCE(f.Area, '')
                            ORDER BY f.Periodo DESC, f.UpdatedDate DESC
                        ) AS frn
                    FROM dbo.CierresProduccionFilasImportadas f
                )
                SELECT COUNT(*) AS Valor
                FROM rows_latest f
                LEFT JOIN dbo.Licitaciones l ON l.Id = f.LicitacionId
                {where_clause}
                  AND f.frn = 1
                """,
                *params,
            )
            return _rows_to_dicts(cursor)

    campo_safe = "".join(ch for ch in (campo or "") if ch.isalnum() or ch == "_")
    if not campo_safe:
        return []
    json_value_sql = f"JSON_VALUE(f.ValoresJson, '$.{campo_safe}')"
    raw_value_sql = f"COALESCE(latest.Valor, {json_value_sql})"
    numeric_sql = (
        "CASE "
        f"WHEN {raw_value_sql} LIKE '%,%' AND {raw_value_sql} LIKE '%.%' "
        f"THEN TRY_PARSE({raw_value_sql} AS decimal(18,2) USING 'es-ES') "
        f"WHEN {raw_value_sql} LIKE '%,%' "
        f"THEN TRY_PARSE({raw_value_sql} AS decimal(18,2) USING 'es-ES') "
        f"ELSE TRY_PARSE({raw_value_sql} AS decimal(18,2) USING 'en-US') "
        "END"
    )
    filters = [
        "(latest.Campo = ? OR latest.Campo IS NULL)",
        f"{numeric_sql} IS NOT NULL",
        "(? IS NULL OR COALESCE(latest.Periodo, f.Periodo) = ?)",
        "(? IS NULL OR COALESCE(latest.Area, f.Area) = ?)",
    ]
    params: List[Any] = [campo, periodo, periodo, area, area]

    if free_text:
        like = f"%{free_text}%"
        filters.append(
            "("
            "COALESCE(l.NumeroProyecto, '') LIKE ? OR COALESCE(l.NumeroOferta, '') LIKE ? "
            "OR COALESCE(l.Obra, '') LIKE ? OR COALESCE(f.Nombre, '') LIKE ? OR COALESCE(f.ProyectoBc, '') LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like])

    where_clause = f"WHERE {' AND '.join(filters)}"
    order_sql = (
        "COALESCE(latest.Periodo, f.Periodo) DESC, f.UpdatedDate DESC, COALESCE(l.NumeroProyecto, f.Numero) DESC"
        if order == "latest"
        else f"{numeric_sql} DESC, COALESCE(l.NumeroProyecto, f.Numero), l.NumeroOferta"
    )
    with appregenera_conn() as conn:
        cursor = conn.cursor()
        if agg == "top":
            cursor.execute(
                f"""
                WITH latest AS (
                    SELECT
                        v.LicitacionId,
                        v.Area,
                        v.Campo,
                        v.Valor,
                        v.Periodo,
                        ROW_NUMBER() OVER (
                            PARTITION BY v.LicitacionId, v.Campo, COALESCE(v.Area, '')
                            ORDER BY v.Periodo DESC, v.UpdatedDate DESC
                        ) AS rn
                    FROM dbo.CierresProduccionValores v
                ),
                rows_latest AS (
                    SELECT
                        f.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(CONVERT(varchar(36), f.LicitacionId), f.Numero, f.Nombre), COALESCE(f.Area, '')
                            ORDER BY f.Periodo DESC, f.UpdatedDate DESC
                        ) AS frn
                    FROM dbo.CierresProduccionFilasImportadas f
                )
                SELECT TOP (?) COALESCE(l.NumeroProyecto, f.Numero) AS NumeroProyecto, l.NumeroOferta, COALESCE(l.Obra, f.Nombre) AS Obra, f.Nombre,
                       COALESCE(latest.Area, f.Area) AS Area, COALESCE(latest.Periodo, f.Periodo) AS Periodo,
                       {numeric_sql} AS Valor
                FROM rows_latest f
                LEFT JOIN latest
                    ON latest.LicitacionId = f.LicitacionId
                   AND latest.rn = 1
                   AND latest.Campo = ?
                   AND COALESCE(latest.Periodo, f.Periodo) = f.Periodo
                LEFT JOIN dbo.Licitaciones l ON l.Id = COALESCE(latest.LicitacionId, f.LicitacionId)
                {where_clause}
                  AND f.frn = 1
                ORDER BY {order_sql}
                """,
                top,
                campo,
                *params,
            )
        elif agg == "avg" and top > 1:
            cursor.execute(
                f"""
                WITH latest AS (
                    SELECT
                        v.LicitacionId,
                        v.Area,
                        v.Campo,
                        v.Valor,
                        v.Periodo,
                        ROW_NUMBER() OVER (
                            PARTITION BY v.LicitacionId, v.Campo, COALESCE(v.Area, '')
                            ORDER BY v.Periodo DESC, v.UpdatedDate DESC
                        ) AS rn
                    FROM dbo.CierresProduccionValores v
                ),
                rows_latest AS (
                    SELECT
                        f.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(CONVERT(varchar(36), f.LicitacionId), f.Numero, f.Nombre), COALESCE(f.Area, '')
                            ORDER BY f.Periodo DESC, f.UpdatedDate DESC
                        ) AS frn
                    FROM dbo.CierresProduccionFilasImportadas f
                )
                SELECT AVG(Valor) AS Valor
                FROM (
                    SELECT TOP (?) {numeric_sql} AS Valor
                    FROM rows_latest f
                    LEFT JOIN latest
                        ON latest.LicitacionId = f.LicitacionId
                       AND latest.rn = 1
                       AND latest.Campo = ?
                       AND COALESCE(latest.Periodo, f.Periodo) = f.Periodo
                    LEFT JOIN dbo.Licitaciones l ON l.Id = COALESCE(latest.LicitacionId, f.LicitacionId)
                    {where_clause}
                      AND f.frn = 1
                      AND {numeric_sql} <> 0
                    ORDER BY {order_sql}
                ) ranked
                """,
                top,
                campo,
                *params,
            )
        else:
            agg_sql = "AVG" if agg == "avg" else "SUM"
            cursor.execute(
                f"""
                WITH latest AS (
                    SELECT
                        v.LicitacionId,
                        v.Area,
                        v.Campo,
                        v.Valor,
                        v.Periodo,
                        ROW_NUMBER() OVER (
                            PARTITION BY v.LicitacionId, v.Campo, COALESCE(v.Area, '')
                            ORDER BY v.Periodo DESC, v.UpdatedDate DESC
                        ) AS rn
                    FROM dbo.CierresProduccionValores v
                ),
                rows_latest AS (
                    SELECT
                        f.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(CONVERT(varchar(36), f.LicitacionId), f.Numero, f.Nombre), COALESCE(f.Area, '')
                            ORDER BY f.Periodo DESC, f.UpdatedDate DESC
                        ) AS frn
                    FROM dbo.CierresProduccionFilasImportadas f
                )
                SELECT {agg_sql}({numeric_sql}) AS Valor
                FROM rows_latest f
                LEFT JOIN latest
                    ON latest.LicitacionId = f.LicitacionId
                   AND latest.rn = 1
                   AND latest.Campo = ?
                   AND COALESCE(latest.Periodo, f.Periodo) = f.Periodo
                LEFT JOIN dbo.Licitaciones l ON l.Id = COALESCE(latest.LicitacionId, f.LicitacionId)
                {where_clause}
                  AND f.frn = 1
                """,
                campo,
                *params,
            )
        return _rows_to_dicts(cursor)


def parse_decimal(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, str):
        clean = value.strip()
        if not clean:
            return None
        if "," in clean and "." in clean:
            clean = clean.replace(".", "").replace(",", ".")
        elif "," in clean:
            clean = clean.replace(",", ".")
        try:
            return float(clean)
        except ValueError:
            return None
    return None


def first_non_null(values: Iterable[Any]) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
