from fastapi import APIRouter, HTTPException, Request

from database import db_conn
from models import InteractionReviewRequest
from routes.auth_helpers import assert_admin, resolve_request_user_id
from routes import chat as chat_routes


router = APIRouter()


@router.post("/admin/sync")
def admin_sync(request: Request, background: bool = False):
    assert_admin(request)
    if background:
        return chat_routes._start_document_sync_background()
    return chat_routes._rag_service().sync_documents()


@router.get("/admin/sync/status")
def admin_sync_status(request: Request):
    assert_admin(request)
    with chat_routes._document_sync_lock:
        return dict(chat_routes._document_sync_status)


@router.get("/knowledge/pending")
def get_pending_knowledge(limit: int = 50):
    return {"pending": chat_routes._memory_service().list_pending_interactions(limit=limit)}


@router.get("/knowledge/my-pending")
def get_my_pending_knowledge(request: Request, limit: int = 50, chat_mode: str | None = None):
    request_user_id = resolve_request_user_id(request)
    return {
        "pending": chat_routes._memory_service().list_pending_interactions(
            limit=limit,
            user_id=request_user_id,
            chat_mode=chat_mode,
        )
    }


@router.get("/knowledge/my-validated")
def get_my_validated_knowledge(request: Request, limit: int = 50):
    request_user_id = resolve_request_user_id(request)
    return {
        "validated": chat_routes._memory_service().list_validated_interactions(
            limit=limit,
            user_id=request_user_id,
        )
    }


@router.get("/admin/metrics")
def admin_metrics(request: Request, days: int = 30):
    assert_admin(request)
    return chat_routes._memory_service().get_admin_metrics(days=days)


@router.get("/admin/metrics/errors-503")
def admin_503_metrics(request: Request, hours: int = 24):
    assert_admin(request)
    return chat_routes._memory_service().get_admin_503_metrics(hours=hours)


@router.get("/admin/knowledge/pending")
def admin_pending(request: Request, limit: int = 50, user_id: int | None = None):
    assert_admin(request)
    return {"pending": chat_routes._memory_service().list_pending_interactions(limit=limit, user_id=user_id)}


@router.get("/admin/knowledge/users")
def admin_pending_users(request: Request):
    assert_admin(request)
    return {"users": chat_routes._memory_service().list_pending_users()}


@router.get("/admin/knowledge/validated")
def admin_validated(request: Request, limit: int = 50, user_id: int | None = None):
    assert_admin(request)
    return {"validated": chat_routes._memory_service().list_validated_interactions(limit=limit, user_id=user_id)}


@router.get("/admin/knowledge/validated/users")
def admin_validated_users(request: Request):
    assert_admin(request)
    return {"users": chat_routes._memory_service().list_validated_users()}


@router.get("/admin/knowledge/{interaction_id}")
def admin_interaction_detail(interaction_id: int, request: Request):
    assert_admin(request)
    try:
        return chat_routes._memory_service().get_interaction_detail(interaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/admin/retrieval-stats")
def admin_retrieval_stats(request: Request, days: int = 30):
    assert_admin(request)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN Estado = 'rechazada' THEN 1 ELSE 0 END) AS rechazadas,
                SUM(CASE WHEN Estado = 'validada' THEN 1 ELSE 0 END) AS validadas,
                SUM(CASE WHEN Estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
                AVG(ConfianzaFinal) AS avg_confidence,
                SUM(CASE WHEN ConfianzaFinal < 0.65 THEN 1 ELSE 0 END) AS low_confidence,
                SUM(CASE WHEN Respuesta LIKE '%no hay informaci_n suficiente%' THEN 1 ELSE 0 END) AS sin_info
            FROM dbo.InteraccionesRAG
            WHERE FechaCreacion >= DATEADD(DAY, -?, SYSUTCDATETIME())
            """,
            days,
        )
        row = cursor.fetchone()
        total = row[0] or 0
        rechazadas = row[1] or 0
        validadas = row[2] or 0
        pendientes = row[3] or 0

        cursor.execute(
            """
            SELECT
                ISNULL(Ruta, 'unknown') AS ruta,
                COUNT(*) AS total,
                SUM(CASE WHEN Estado = 'rechazada' THEN 1 ELSE 0 END) AS rechazadas,
                AVG(ConfianzaFinal) AS avg_confidence
            FROM dbo.InteraccionesRAG
            WHERE FechaCreacion >= DATEADD(DAY, -?, SYSUTCDATETIME())
            GROUP BY ISNULL(Ruta, 'unknown')
            """,
            days,
        )
        by_route = []
        for r in cursor.fetchall():
            route_total = r[1] or 0
            by_route.append({
                "route": r[0],
                "total": route_total,
                "rechazadas": r[2] or 0,
                "rejection_rate": round((r[2] or 0) / route_total, 4) if route_total else 0.0,
                "avg_confidence": round(float(r[3] or 0), 4),
            })

    return {
        "days": days,
        "total": total,
        "rechazadas": rechazadas,
        "validadas": validadas,
        "pendientes": pendientes,
        "rejection_rate": round(rechazadas / total, 4) if total else 0.0,
        "validation_rate": round(validadas / total, 4) if total else 0.0,
        "avg_confidence": round(float(row[4] or 0), 4),
        "low_confidence": row[5] or 0,
        "sin_info": row[6] or 0,
        "by_route": by_route,
    }


@router.get("/admin/retrieval-stats/timeline")
def admin_retrieval_timeline(request: Request, weeks: int = 12):
    assert_admin(request)
    weeks = max(1, min(weeks, 52))
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                DATEADD(WEEK, DATEDIFF(WEEK, 0, FechaCreacion), 0) AS week_start,
                COUNT(*) AS total,
                SUM(CASE WHEN Estado = 'rechazada' THEN 1 ELSE 0 END) AS rechazadas,
                SUM(CASE WHEN Estado = 'validada' THEN 1 ELSE 0 END) AS validadas,
                AVG(ConfianzaFinal) AS avg_confidence,
                SUM(CASE WHEN Respuesta LIKE '%no hay informaci_n suficiente%' THEN 1 ELSE 0 END) AS sin_info
            FROM dbo.InteraccionesRAG
            WHERE FechaCreacion >= DATEADD(WEEK, -?, SYSUTCDATETIME())
            GROUP BY DATEADD(WEEK, DATEDIFF(WEEK, 0, FechaCreacion), 0)
            ORDER BY week_start
            """,
            weeks,
        )
        timeline = []
        for r in cursor.fetchall():
            total = r[1] or 0
            timeline.append({
                "week": str(r[0])[:10],
                "total": total,
                "rechazadas": r[2] or 0,
                "validadas": r[3] or 0,
                "rejection_rate": round((r[2] or 0) / total, 4) if total else 0.0,
                "avg_confidence": round(float(r[4] or 0), 4),
                "sin_info": r[5] or 0,
            })

        cursor.execute(
            """
            SELECT
                Id, Branch, Conclusion, StartedAt, CompletedAt
            FROM dbo.DeploymentRuns
            WHERE StartedAt >= DATEADD(WEEK, -?, SYSUTCDATETIME())
              AND Conclusion IS NOT NULL
            ORDER BY StartedAt
            """,
            weeks,
        )
        deploys = []
        for r in cursor.fetchall():
            deploys.append({
                "id": r[0],
                "branch": r[1] or "",
                "conclusion": r[2] or "",
                "started_at": str(r[3] or ""),
                "completed_at": str(r[4] or ""),
            })

    return {"weeks": weeks, "timeline": timeline, "deployments": deploys}


@router.get("/admin/retrieval-stats/compare")
def admin_retrieval_compare(request: Request, deploy_a: int = 0, deploy_b: int = 0):
    assert_admin(request)
    if not deploy_a or not deploy_b:
        raise HTTPException(status_code=400, detail="Se requieren deploy_a y deploy_b")
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Id, CompletedAt FROM dbo.DeploymentRuns WHERE Id IN (?, ?) ORDER BY CompletedAt",
            deploy_a, deploy_b,
        )
        rows = cursor.fetchall()
        if len(rows) < 2:
            raise HTTPException(status_code=404, detail="No se encontraron ambos despliegues")

        period_a_start = rows[0][1]
        period_a_end = rows[1][1]

        cursor.execute(
            "SELECT TOP 1 CompletedAt FROM dbo.DeploymentRuns WHERE CompletedAt > ? ORDER BY CompletedAt",
            period_a_end,
        )
        next_deploy = cursor.fetchone()
        period_b_end = next_deploy[0] if next_deploy else None

        def _stats_for_period(start, end):
            if end:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN Estado = 'rechazada' THEN 1 ELSE 0 END) AS rechazadas,
                        SUM(CASE WHEN Estado = 'validada' THEN 1 ELSE 0 END) AS validadas,
                        AVG(ConfianzaFinal) AS avg_confidence,
                        SUM(CASE WHEN Respuesta LIKE '%no hay informaci_n suficiente%' THEN 1 ELSE 0 END) AS sin_info
                    FROM dbo.InteraccionesRAG
                    WHERE FechaCreacion >= ? AND FechaCreacion < ?
                    """,
                    start, end,
                )
            else:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN Estado = 'rechazada' THEN 1 ELSE 0 END) AS rechazadas,
                        SUM(CASE WHEN Estado = 'validada' THEN 1 ELSE 0 END) AS validadas,
                        AVG(ConfianzaFinal) AS avg_confidence,
                        SUM(CASE WHEN Respuesta LIKE '%no hay informaci_n suficiente%' THEN 1 ELSE 0 END) AS sin_info
                    FROM dbo.InteraccionesRAG
                    WHERE FechaCreacion >= ?
                    """,
                    start,
                )
            row = cursor.fetchone()
            total = row[0] or 0
            return {
                "total": total,
                "rechazadas": row[1] or 0,
                "validadas": row[2] or 0,
                "rejection_rate": round((row[1] or 0) / total, 4) if total else 0.0,
                "avg_confidence": round(float(row[3] or 0), 4),
                "sin_info": row[4] or 0,
            }

        stats_a = _stats_for_period(period_a_start, period_a_end)
        stats_b = _stats_for_period(period_a_end, period_b_end)

    deltas = {}
    for key in ("rejection_rate", "avg_confidence"):
        deltas[key] = round(stats_b[key] - stats_a[key], 4)

    return {
        "deploy_a": {"id": rows[0][0], "completed_at": str(rows[0][1])},
        "deploy_b": {"id": rows[1][0], "completed_at": str(rows[1][1])},
        "period_a": stats_a,
        "period_b": stats_b,
        "deltas": deltas,
    }


@router.post("/admin/knowledge/{interaction_id}/retract")
def admin_retract_memory(interaction_id: int, data: InteractionReviewRequest, request: Request):
    assert_admin(request)
    return chat_routes._memory_service().reject_interaction(
        interaction_id=interaction_id,
        reviewer=data.reviewer,
    )


@router.post("/admin/memory/purge-inventory")
def admin_purge_inventory_memories(request: Request, dry_run: bool = True):
    assert_admin(request)
    return chat_routes._memory_service().purge_document_inventory_memories(dry_run=dry_run)


@router.post("/knowledge/{interaction_id}/validate")
def approve_interaction(interaction_id: int, data: InteractionReviewRequest, request: Request):
    chat_routes._assert_admin_or_interaction_owner(request, interaction_id)
    try:
        return chat_routes._memory_service().validate_interaction(
            interaction_id=interaction_id,
            reviewer=data.reviewer,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/knowledge/{interaction_id}/reject")
def reject_interaction_endpoint(interaction_id: int, data: InteractionReviewRequest, request: Request):
    chat_routes._assert_admin_or_interaction_owner(request, interaction_id)
    return chat_routes._memory_service().reject_interaction(
        interaction_id=interaction_id,
        reviewer=data.reviewer,
    )
