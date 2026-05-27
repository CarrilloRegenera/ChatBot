import html
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib import error, parse, request

from config import (
    ADMIN_PANEL_ALLOWED_EMAILS,
    CHATBOT_FRONTEND_URL,
    DEPLOYMENTS_HISTORY_LIMIT,
    EMAIL_ENABLED,
    EMAIL_PROVIDER,
    GITHUB_DEPLOY_BRANCH,
    GITHUB_DEPLOY_TOKEN,
    GITHUB_DEPLOY_WORKFLOW,
    GITHUB_REPOSITORY_NAME,
    GITHUB_REPOSITORY_OWNER,
    GITHUB_SERVER_URL,
    GRAPH_EMAIL_CLIENT_ID,
    GRAPH_EMAIL_CLIENT_SECRET,
    GRAPH_EMAIL_FROM_USER,
    GRAPH_EMAIL_SAVE_TO_SENT_ITEMS,
    GRAPH_EMAIL_TENANT_ID,
)
from database import db_conn


logger = logging.getLogger(__name__)
GITHUB_API_VERSION = "2022-11-28"


class DeploymentConfigurationError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_github_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def _iso_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_recipients(recipients: list[str] | str | None) -> list[str]:
    if recipients is None:
        return []
    raw = recipients if isinstance(recipients, str) else ",".join(recipients)
    values = []
    seen = set()
    for item in raw.replace(";", ",").split(","):
        email = item.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        values.append(email)
    return values


def _require_github_token() -> str:
    if not GITHUB_DEPLOY_TOKEN:
        raise DeploymentConfigurationError("Falta GITHUB_DEPLOY_TOKEN en la configuracion del chatbot")
    return GITHUB_DEPLOY_TOKEN


def _github_headers() -> dict[str, str]:
    token = _require_github_token()
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "ChatbotDeployManager/1.0",
    }


def _github_request(method: str, path: str, payload: dict[str, Any] | None = None, accept: str | None = None) -> Any:
    headers = _github_headers()
    if accept:
        headers["Accept"] = accept

    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    url = f"https://api.github.com{path}"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(body.decode("utf-8"))
            return body
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        logger.error("GitHub API error %s %s: %s", method, path, detail)
        raise RuntimeError(f"GitHub API devolvio {exc.code}") from exc


def trigger_full_deploy(*, requested_by_email: str | None, requested_by_name: str | None, branch: str | None = None) -> dict[str, Any]:
    target_branch = (branch or GITHUB_DEPLOY_BRANCH or "sandbox").strip()
    payload = {
        "ref": target_branch,
        "inputs": {
            "branch": target_branch,
            "action": "full",
            "requested_by_email": (requested_by_email or "").strip(),
            "requested_by_name": (requested_by_name or "").strip(),
            "trigger_source": "chatbot_ui",
        },
    }
    _github_request(
        "POST",
        f"/repos/{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}/actions/workflows/{GITHUB_DEPLOY_WORKFLOW}/dispatches",
        payload=payload,
    )
    return {
        "message": "Despliegue full lanzado correctamente",
        "branch": target_branch,
        "action": "full",
    }


def get_notification_settings() -> dict[str, Any]:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TOP 1 Recipients, UpdatedBy, UpdatedAt FROM DeploymentNotificationSettings WHERE Id = 1"
        )
        row = cursor.fetchone()
    if not row:
        return {"recipients": sorted(ADMIN_PANEL_ALLOWED_EMAILS), "updated_by": "system", "updated_at": None}
    return {
        "recipients": _normalize_recipients(row[0]),
        "updated_by": row[1] or "system",
        "updated_at": _iso_datetime(row[2]),
    }


def update_notification_settings(recipients: list[str], *, updated_by: str) -> dict[str, Any]:
    normalized = _normalize_recipients(recipients)
    if not normalized:
        raise ValueError("Debes indicar al menos un correo destinatario")

    recipients_csv = ",".join(normalized)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            MERGE DeploymentNotificationSettings AS target
            USING (SELECT 1 AS Id) AS source
            ON target.Id = source.Id
            WHEN MATCHED THEN
                UPDATE SET Recipients = ?, UpdatedBy = ?, UpdatedAt = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (Id, Recipients, UpdatedBy) VALUES (1, ?, ?);
            """,
            recipients_csv,
            updated_by,
            recipients_csv,
            updated_by,
        )
    return get_notification_settings()


def _build_logs_url(run_id: int) -> str:
    return f"{GITHUB_SERVER_URL}/{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}/actions/runs/{run_id}"


def _github_logs_api_path(run_id: int) -> str:
    return f"/repos/{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}/actions/runs/{run_id}/logs"


def download_run_logs(run_id: int) -> tuple[bytes, str]:
    archive = _github_request("GET", _github_logs_api_path(run_id), accept="application/vnd.github+json")
    filename = f"deploy-chatbot-run-{run_id}.zip"
    return archive, filename


def _run_record_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    started_at = _parse_github_datetime(payload.get("run_started_at"))
    completed_at = _parse_github_datetime(payload.get("completed_at"))
    duration_seconds = None
    if started_at and completed_at:
        duration_seconds = max(0, int((completed_at - started_at).total_seconds()))

    return {
        "github_run_id": int(payload["run_id"]),
        "run_number": payload.get("run_number"),
        "run_attempt": payload.get("run_attempt"),
        "workflow_name": payload.get("workflow_name") or "Deploy Chatbot",
        "branch": payload.get("branch") or GITHUB_DEPLOY_BRANCH,
        "requested_action": payload.get("requested_action") or "full",
        "trigger_source": payload.get("trigger_source") or "github_manual",
        "triggered_by_email": (payload.get("requested_by_email") or "").strip().lower() or None,
        "triggered_by_name": (payload.get("requested_by_name") or "").strip() or None,
        "actor": payload.get("actor"),
        "status": payload.get("status") or "completed",
        "conclusion": payload.get("conclusion"),
        "html_url": payload.get("html_url") or _build_logs_url(int(payload["run_id"])),
        "logs_url": payload.get("logs_url") or _build_logs_url(int(payload["run_id"])),
        "backend_url": payload.get("backend_url"),
        "frontend_url": payload.get("frontend_url") or CHATBOT_FRONTEND_URL,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
    }


def _run_record_from_github(run: dict[str, Any]) -> dict[str, Any]:
    started_at = _parse_github_datetime(run.get("run_started_at") or run.get("created_at"))
    completed_at = _parse_github_datetime(run.get("updated_at")) if run.get("status") == "completed" else None
    duration_seconds = None
    if started_at and completed_at:
        duration_seconds = max(0, int((completed_at - started_at).total_seconds()))

    return {
        "github_run_id": int(run["id"]),
        "run_number": run.get("run_number"),
        "run_attempt": run.get("run_attempt"),
        "workflow_name": run.get("name") or "Deploy Chatbot",
        "branch": run.get("head_branch") or GITHUB_DEPLOY_BRANCH,
        "requested_action": None,
        "trigger_source": "github_manual",
        "triggered_by_email": None,
        "triggered_by_name": None,
        "actor": (run.get("actor") or {}).get("login"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "html_url": run.get("html_url") or _build_logs_url(int(run["id"])),
        "logs_url": run.get("html_url") or _build_logs_url(int(run["id"])),
        "backend_url": None,
        "frontend_url": CHATBOT_FRONTEND_URL,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
    }


def _get_existing_run(run_id: int) -> tuple | None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP 1 Id, RequestedAction, TriggerSource, TriggeredByEmail, TriggeredByName,
                   LastNotifiedConclusion, Status, Conclusion, HtmlUrl, LogsUrl, BackendUrl, FrontendUrl
            FROM DeploymentRuns
            WHERE GitHubRunId = ?
            """,
            run_id,
        )
        return cursor.fetchone()


def _upsert_run(record: dict[str, Any]) -> dict[str, Any]:
    existing = _get_existing_run(record["github_run_id"])
    requested_action = record["requested_action"] or (existing[1] if existing else None) or "full"
    trigger_source = record["trigger_source"] or (existing[2] if existing else None) or "github_manual"
    triggered_by_email = record["triggered_by_email"] or (existing[3] if existing else None)
    triggered_by_name = record["triggered_by_name"] or (existing[4] if existing else None)
    last_notified = existing[5] if existing else None
    html_url = record["html_url"] or (existing[8] if existing else None)
    logs_url = record["logs_url"] or (existing[9] if existing else None) or html_url
    backend_url = record["backend_url"] or (existing[10] if existing else None)
    frontend_url = record["frontend_url"] or (existing[11] if existing else None) or CHATBOT_FRONTEND_URL

    with db_conn() as conn:
        cursor = conn.cursor()
        if existing:
            cursor.execute(
                """
                UPDATE DeploymentRuns
                SET RunNumber = ?, RunAttempt = ?, WorkflowName = ?, Branch = ?, RequestedAction = ?,
                    TriggerSource = ?, TriggeredByEmail = ?, TriggeredByName = ?, Actor = ?, Status = ?,
                    Conclusion = ?, HtmlUrl = ?, LogsUrl = ?, BackendUrl = ?, FrontendUrl = ?,
                    StartedAt = ?, CompletedAt = ?, DurationSeconds = ?, UpdatedAt = SYSUTCDATETIME()
                WHERE GitHubRunId = ?
                """,
                record["run_number"],
                record["run_attempt"],
                record["workflow_name"],
                record["branch"],
                requested_action,
                trigger_source,
                triggered_by_email,
                triggered_by_name,
                record["actor"],
                record["status"],
                record["conclusion"],
                html_url,
                logs_url,
                backend_url,
                frontend_url,
                record["started_at"],
                record["completed_at"],
                record["duration_seconds"],
                record["github_run_id"],
            )
        else:
            cursor.execute(
                """
                INSERT INTO DeploymentRuns (
                    GitHubRunId, RunNumber, RunAttempt, WorkflowName, Branch, RequestedAction,
                    TriggerSource, TriggeredByEmail, TriggeredByName, Actor, Status, Conclusion,
                    HtmlUrl, LogsUrl, BackendUrl, FrontendUrl, StartedAt, CompletedAt, DurationSeconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                record["github_run_id"],
                record["run_number"],
                record["run_attempt"],
                record["workflow_name"],
                record["branch"],
                requested_action,
                trigger_source,
                triggered_by_email,
                triggered_by_name,
                record["actor"],
                record["status"],
                record["conclusion"],
                html_url,
                logs_url,
                backend_url,
                frontend_url,
                record["started_at"],
                record["completed_at"],
                record["duration_seconds"],
            )

    record["requested_action"] = requested_action
    record["trigger_source"] = trigger_source
    record["triggered_by_email"] = triggered_by_email
    record["triggered_by_name"] = triggered_by_name
    record["html_url"] = html_url
    record["logs_url"] = logs_url
    record["backend_url"] = backend_url
    record["frontend_url"] = frontend_url
    record["last_notified_conclusion"] = last_notified
    return record


def _mark_notification_sent(run_id: int, conclusion: str) -> None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE DeploymentRuns
            SET LastNotifiedConclusion = ?, NotificationSentAt = SYSUTCDATETIME(), UpdatedAt = SYSUTCDATETIME()
            WHERE GitHubRunId = ?
            """,
            conclusion,
            run_id,
        )


def _send_graph_mail(*, recipients: list[str], subject: str, html_body: str) -> None:
    if not EMAIL_ENABLED or EMAIL_PROVIDER.strip().lower() != "graph":
        logger.info("Envio de correo omitido: Email__Enabled o Email__Provider no permiten Graph.")
        return
    missing = []
    if not GRAPH_EMAIL_TENANT_ID:
        missing.append("GraphEmail__TenantId")
    if not GRAPH_EMAIL_CLIENT_ID:
        missing.append("GraphEmail__ClientId")
    if not GRAPH_EMAIL_CLIENT_SECRET:
        missing.append("GraphEmail__ClientSecret")
    if not GRAPH_EMAIL_FROM_USER:
        missing.append("GraphEmail__FromUser")
    if missing:
        raise DeploymentConfigurationError(f"Faltan ajustes de correo Graph: {', '.join(missing)}")

    token_payload = parse.urlencode(
        {
            "client_id": GRAPH_EMAIL_CLIENT_ID,
            "client_secret": GRAPH_EMAIL_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    token_request = request.Request(
        f"https://login.microsoftonline.com/{GRAPH_EMAIL_TENANT_ID}/oauth2/v2.0/token",
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with request.urlopen(token_request, timeout=30) as response:
        token_data = json.loads(response.read().decode("utf-8"))
    access_token = token_data["access_token"]

    graph_payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [{"emailAddress": {"address": email}} for email in recipients],
        },
        "saveToSentItems": GRAPH_EMAIL_SAVE_TO_SENT_ITEMS,
    }
    graph_request = request.Request(
        f"https://graph.microsoft.com/v1.0/users/{parse.quote(GRAPH_EMAIL_FROM_USER)}/sendMail",
        data=json.dumps(graph_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(graph_request, timeout=30):
        logger.info("Correo de despliegue enviado a %s", ", ".join(recipients))


def _build_email_subject(run: dict[str, Any]) -> str:
    if (run.get("conclusion") or "").lower() == "success":
        return f"[ChatBot] Despliegue correcto - {run.get('branch') or GITHUB_DEPLOY_BRANCH}"
    return f"[ChatBot] Despliegue incorrecto - {run.get('branch') or GITHUB_DEPLOY_BRANCH}"


def _build_email_body(run: dict[str, Any]) -> str:
    status_ok = (run.get("conclusion") or "").lower() == "success"
    badge_text = "CORRECTO" if status_ok else "INCORRECTO"
    badge_color = "#2da44e" if status_ok else "#cf222e"
    html_url = html.escape(run.get("html_url") or "")
    requested_action = html.escape((run.get("requested_action") or "full").upper())
    actor = html.escape(run.get("triggered_by_email") or run.get("actor") or "desconocido")
    started_at = html.escape(run.get("started_at") or "-")
    completed_at = html.escape(run.get("completed_at") or "-")
    duration = html.escape(str(run.get("duration_seconds") or 0))
    frontend_url = html.escape(run.get("frontend_url") or CHATBOT_FRONTEND_URL)
    backend_url = html.escape(run.get("backend_url") or "")
    rows = [
        ("Estado", badge_text),
        ("Rama", html.escape(run.get("branch") or GITHUB_DEPLOY_BRANCH)),
        ("Accion", requested_action),
        ("Lanzado por", actor),
        ("GitHub actor", html.escape(run.get("actor") or "-")),
        ("Inicio", started_at),
        ("Fin", completed_at),
        ("Duracion (s)", duration),
        ("Frontend", frontend_url),
    ]
    if backend_url:
        rows.append(("Backend", backend_url))

    row_html = "".join(
        f"<tr><td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#6b7280;font-weight:600'>{label}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#111827'>{value}</td></tr>"
        for label, value in rows
    )

    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;background:#f6f8fa;padding:24px;color:#111827">
      <div style="max-width:720px;margin:0 auto;background:#ffffff;border-radius:16px;padding:24px;border:1px solid #e5e7eb">
        <div style="display:inline-block;background:{badge_color};color:#ffffff;border-radius:999px;padding:6px 12px;font-weight:700;font-size:12px;letter-spacing:.04em">
          {badge_text}
        </div>
        <h2 style="margin:16px 0 8px 0">Despliegue de ChatBot PRO</h2>
        <p style="margin:0 0 18px 0;color:#4b5563">Se ha completado un despliegue del chatbot y ya queda registrado en el historial de despliegues.</p>
        <table style="width:100%;border-collapse:collapse;margin-bottom:20px">{row_html}</table>
        <a href="{html_url}" style="display:inline-block;background:#0f172a;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:600">
          Ver ejecucion en GitHub
        </a>
      </div>
    </div>
    """.strip()


def _maybe_notify(run: dict[str, Any]) -> None:
    if (run.get("status") or "").lower() != "completed":
        return
    conclusion = (run.get("conclusion") or "").strip().lower()
    if not conclusion:
        return
    if conclusion == (run.get("last_notified_conclusion") or "").strip().lower():
        return

    settings = get_notification_settings()
    recipients = settings.get("recipients") or []
    if not recipients:
        logger.info("No hay destinatarios configurados para avisos de despliegue.")
        return

    subject = _build_email_subject(run)
    body = _build_email_body(run)
    _send_graph_mail(recipients=recipients, subject=subject, html_body=body)
    _mark_notification_sent(int(run["github_run_id"]), conclusion)


def register_webhook_run(payload: dict[str, Any]) -> dict[str, Any]:
    record = _run_record_from_payload(payload)
    saved = _upsert_run(record)
    _maybe_notify(saved)
    return saved


def store_webhook_run(payload: dict[str, Any]) -> dict[str, Any]:
    record = _run_record_from_payload(payload)
    return _upsert_run(record)


def notify_run_if_needed(run_id: int) -> None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP 1
                GitHubRunId, RunNumber, RunAttempt, WorkflowName, Branch, RequestedAction,
                TriggerSource, TriggeredByEmail, TriggeredByName, Actor, Status, Conclusion,
                HtmlUrl, LogsUrl, BackendUrl, FrontendUrl, StartedAt, CompletedAt, DurationSeconds,
                LastNotifiedConclusion
            FROM DeploymentRuns
            WHERE GitHubRunId = ?
            """,
            run_id,
        )
        row = cursor.fetchone()
    if not row:
        raise ValueError(f"No existe DeploymentRun para GitHubRunId={run_id}")

    run = {
        "github_run_id": int(row[0]),
        "run_number": row[1],
        "run_attempt": row[2],
        "workflow_name": row[3],
        "branch": row[4],
        "requested_action": row[5] or "full",
        "trigger_source": row[6] or "github_manual",
        "triggered_by_email": row[7],
        "triggered_by_name": row[8],
        "actor": row[9],
        "status": row[10] or "unknown",
        "conclusion": row[11],
        "html_url": row[12],
        "logs_url": row[13],
        "backend_url": row[14],
        "frontend_url": row[15] or CHATBOT_FRONTEND_URL,
        "started_at": _iso_datetime(row[16]),
        "completed_at": _iso_datetime(row[17]),
        "duration_seconds": row[18],
        "last_notified_conclusion": row[19],
    }
    _maybe_notify(run)


def sync_recent_deployments(limit: int | None = None) -> None:
    per_page = max(10, min(limit or DEPLOYMENTS_HISTORY_LIMIT, 100))
    try:
        response = _github_request(
            "GET",
            f"/repos/{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}/actions/workflows/{GITHUB_DEPLOY_WORKFLOW}/runs?per_page={per_page}",
        )
    except DeploymentConfigurationError:
        return
    except Exception:
        logger.exception("No se pudieron sincronizar ejecuciones de despliegue desde GitHub")
        return

    for run in response.get("workflow_runs", []):
        _upsert_run(_run_record_from_github(run))


def list_deployments(limit: int | None = None) -> list[dict[str, Any]]:
    max_rows = limit or DEPLOYMENTS_HISTORY_LIMIT
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT TOP ({max_rows})
                GitHubRunId, RunNumber, RunAttempt, WorkflowName, Branch, RequestedAction,
                TriggerSource, TriggeredByEmail, TriggeredByName, Actor, Status, Conclusion,
                HtmlUrl, LogsUrl, BackendUrl, FrontendUrl, StartedAt, CompletedAt, DurationSeconds, UpdatedAt
            FROM DeploymentRuns
            ORDER BY COALESCE(CompletedAt, StartedAt, UpdatedAt, CreatedAt) DESC, GitHubRunId DESC
            """
        )
        rows = cursor.fetchall()

    deployments = []
    for row in rows:
        deployments.append(
            {
                "github_run_id": int(row[0]),
                "run_number": row[1],
                "run_attempt": row[2],
                "workflow_name": row[3],
                "branch": row[4],
                "requested_action": row[5] or "full",
                "trigger_source": row[6] or "github_manual",
                "triggered_by_email": row[7],
                "triggered_by_name": row[8],
                "actor": row[9],
                "status": row[10] or "unknown",
                "conclusion": row[11],
                "html_url": row[12],
                "logs_url": row[13],
                "backend_url": row[14],
                "frontend_url": row[15] or CHATBOT_FRONTEND_URL,
                "started_at": _iso_datetime(row[16]),
                "completed_at": _iso_datetime(row[17]),
                "duration_seconds": row[18],
                "updated_at": _iso_datetime(row[19]),
            }
        )
    return deployments
