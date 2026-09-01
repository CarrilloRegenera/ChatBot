import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

import pyodbc
from dotenv import load_dotenv


logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)


def _normalize_connection_timeout(connection_string: str) -> str:
    timeout = os.getenv("SQL_CONNECTION_TIMEOUT", "15").strip() or "15"
    if re.search(r"Connection Timeout\s*=", connection_string, flags=re.IGNORECASE):
        return re.sub(
            r"Connection Timeout\s*=\s*[^;]+",
            f"Connection Timeout={timeout}",
            connection_string,
            count=1,
            flags=re.IGNORECASE,
        )
    return connection_string.rstrip(";") + f";Connection Timeout={timeout};"


# En Azure preferimos no reutilizar handles ODBC antiguos entre peticiones; con
# Azure SQL eso puede derivar en sockets estancados y primeros accesos colgados.
pyodbc.pooling = False


def _available_sql_drivers() -> list[str]:
    return [driver for driver in pyodbc.drivers() if "SQL Server" in driver]


def _normalize_sql_driver(connection_string: str) -> str:
    """Reescribe el Driver si el declarado no existe en la maquina actual.

    En Azure la imagen ya incluye el driver esperado, pero en local puede
    existir solo ODBC Driver 17. Queremos aceptar una connection string
    productiva y degradar de forma segura a un driver SQL Server instalado.
    """
    match = re.search(r"Driver=\{([^}]+)\}", connection_string, flags=re.IGNORECASE)
    if not match:
        return connection_string

    configured_driver = match.group(1).strip()
    available_drivers = _available_sql_drivers()
    if not available_drivers or configured_driver in available_drivers:
        return connection_string

    preferred_order = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server",
    ]
    fallback_driver = next((driver for driver in preferred_order if driver in available_drivers), available_drivers[-1])

    logger.warning(
        "Driver ODBC configurado no disponible en esta maquina. Usando '%s' en lugar de '%s'.",
        fallback_driver,
        configured_driver,
    )
    return re.sub(
        r"Driver=\{[^}]+\}",
        f"Driver={{{fallback_driver}}}",
        connection_string,
        count=1,
        flags=re.IGNORECASE,
    )


def _build_connection_string() -> str:
    """Connection string desde env (Azure-ready) sin dependencias locales hardcodeadas."""
    explicit = os.getenv("SQL_CONNECTION_STRING")
    if explicit:
        return _normalize_connection_timeout(_normalize_sql_driver(explicit))
    env_name = (os.getenv("ENVIRONMENT", "") or os.getenv("APP_ENV", "")).strip().lower()
    is_production = env_name in {"prod", "production"}
    allow_local_fallback = os.getenv("ALLOW_LOCAL_SQL_FALLBACK", "1").strip().lower() in {"1", "true", "yes", "on"}
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    server = os.getenv("SQL_SERVER", "").strip()
    database = os.getenv("SQL_DATABASE", "").strip()
    user = os.getenv("SQL_USER", "")
    password = os.getenv("SQL_PASSWORD", "")
    timeout = os.getenv("SQL_CONNECTION_TIMEOUT", "15")
    encrypt = os.getenv("SQL_ENCRYPT", "yes")
    trust_cert = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "no")

    if (not server or not database) and (not is_production) and allow_local_fallback:
        server = "localhost\\SQLEXPRESS"
        database = "ChatBot"

    if not server or not database:
        raise RuntimeError(
            "Config SQL incompleta. Define SQL_CONNECTION_STRING o, en su defecto, "
            "SQL_SERVER y SQL_DATABASE."
        )

    parts = [
        f"Driver={{{driver}}}",
        f"Server={server}",
        f"Database={database}",
        f"Encrypt={encrypt}",
        f"TrustServerCertificate={trust_cert}",
        f"Connection Timeout={timeout}",
    ]
    if user and password:
        parts.append(f"UID={user}")
        parts.append(f"PWD={password}")
    else:
        parts.append("Trusted_Connection=yes")
    return _normalize_connection_timeout(_normalize_sql_driver(";".join(parts) + ";"))


_CONNECTION_STRING = _build_connection_string()


def _connect_with_retry() -> pyodbc.Connection:
    """Abre SQL Azure con reintentos para fallos transitorios de red/login.

    No reintenta errores de consulta: estos se producen despues de abrir la
    conexion y deben propagarse inmediatamente. Mantener esta logica aqui hace
    que health, el webhook y el resto de accesos tengan el mismo comportamiento.
    """
    attempts = max(1, int(os.getenv("SQL_CONNECTION_RETRY_ATTEMPTS", "5")))
    base_delay = max(0.0, float(os.getenv("SQL_CONNECTION_RETRY_DELAY_SECS", "2")))
    last_error: pyodbc.Error | None = None

    for attempt in range(1, attempts + 1):
        try:
            return pyodbc.connect(_CONNECTION_STRING)
        except pyodbc.Error as exc:
            last_error = exc
            if attempt == attempts:
                break

            # Retroceso acotado: 2, 4, 8, 16 s por defecto. Evita que un
            # reinicio/cold start puntual de Azure SQL pierda la notificacion.
            delay = min(base_delay * (2 ** (attempt - 1)), 20.0)
            logger.warning(
                "Conexion SQL fallida (intento %s/%s); se reintentara en %.1f s.",
                attempt,
                attempts,
                delay,
                exc_info=True,
            )
            time.sleep(delay)

    raise last_error or RuntimeError("No se pudo abrir la conexion SQL")


def get_connection() -> pyodbc.Connection:
    """Conexión cruda. Mantiene compatibilidad. Prefiere `db_conn()` para
    transaccionalidad y cierre garantizado."""
    return _connect_with_retry()


def ping_database() -> None:
    """Verifica que la base de datos responde con una consulta trivial.

    La usamos en startup y health para no declarar la API lista antes de que
    SQL esté realmente disponible para el primer login.
    """
    with _connect_with_retry() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()


@contextmanager
def db_conn():
    """Context manager con transacción explícita y cierre garantizado.

    - Commit automático si el bloque termina sin excepción.
    - Rollback automático en cualquier excepción, re-elevada.
    - Cierre garantizado de la conexión (devuelve handle al pool).
    """
    conn = _connect_with_retry()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.exception("Rollback fallido")
        raise
    finally:
        try:
            conn.close()
        except Exception:
            logger.exception("Close fallido")


# En Azure las migraciones deben correr en pipeline CI/CD (sqlcmd / Alembic),
# no en cada arranque del contenedor. Para deshabilitarlo, exporta
# RUN_SCHEMA_MIGRATIONS=0 antes de levantar el proceso.
def _migrations_enabled() -> bool:
    return os.getenv("RUN_SCHEMA_MIGRATIONS", "1").strip().lower() in {"1", "true", "yes", "on"}


def ensure_app_schema() -> None:
    """Crea tablas, columnas e índices si no existen.

    Idempotente. En entornos gestionados (Azure) ejecutar via pipeline y poner
    RUN_SCHEMA_MIGRATIONS=0 para que el arranque no toque DDL.
    """
    if not _migrations_enabled():
        logger.info("ensure_app_schema: omitido (RUN_SCHEMA_MIGRATIONS=0)")
        return

    with db_conn() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            IF OBJECT_ID('dbo.Usuarios', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.Usuarios (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    Nombre NVARCHAR(200) NOT NULL,
                    Email NVARCHAR(255) NOT NULL,
                    Password NVARCHAR(255) NOT NULL,
                    Rol NVARCHAR(50) NOT NULL DEFAULT 'Usuario',
                    AuthProvider NVARCHAR(50) NOT NULL DEFAULT 'local',
                    FechaCreacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END
            """
        )

        cursor.execute(
            """
            IF COL_LENGTH('dbo.Usuarios', 'Rol') IS NULL
                ALTER TABLE dbo.Usuarios ADD Rol NVARCHAR(50) NOT NULL CONSTRAINT DF_Usuarios_Rol DEFAULT 'Usuario';
            IF COL_LENGTH('dbo.Usuarios', 'AuthProvider') IS NULL
                ALTER TABLE dbo.Usuarios ADD AuthProvider NVARCHAR(50) NOT NULL CONSTRAINT DF_Usuarios_AuthProvider DEFAULT 'local';
            IF COL_LENGTH('dbo.Usuarios', 'FechaCreacion') IS NULL
                ALTER TABLE dbo.Usuarios ADD FechaCreacion DATETIME2 NOT NULL CONSTRAINT DF_Usuarios_FechaCreacion DEFAULT SYSUTCDATETIME();
            """
        )

        cursor.execute(
            """
            IF NOT EXISTS (
                SELECT 1 FROM sys.indexes WHERE name = 'UX_Usuarios_Email'
                AND object_id = OBJECT_ID('dbo.Usuarios')
            )
                CREATE UNIQUE INDEX UX_Usuarios_Email ON dbo.Usuarios (Email);
            """
        )

        cursor.execute(
            """
            IF OBJECT_ID('dbo.Conversaciones', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.Conversaciones (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    UsuarioId INT NOT NULL,
                    Titulo NVARCHAR(255) NOT NULL,
                    Estado NVARCHAR(50) NOT NULL DEFAULT 'Activa',
                    ChatMode NVARCHAR(20) NOT NULL DEFAULT 'technical',
                    FechaCreacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END
            """
        )

        cursor.execute(
            """
            IF COL_LENGTH('dbo.Conversaciones', 'Estado') IS NULL
                ALTER TABLE dbo.Conversaciones ADD Estado NVARCHAR(50) NOT NULL CONSTRAINT DF_Conversaciones_Estado DEFAULT 'Activa';
            IF COL_LENGTH('dbo.Conversaciones', 'ChatMode') IS NULL
                ALTER TABLE dbo.Conversaciones ADD ChatMode NVARCHAR(20) NOT NULL CONSTRAINT DF_Conversaciones_ChatMode DEFAULT 'technical';
            IF COL_LENGTH('dbo.Conversaciones', 'FechaCreacion') IS NULL
                ALTER TABLE dbo.Conversaciones ADD FechaCreacion DATETIME2 NOT NULL CONSTRAINT DF_Conversaciones_FechaCreacion DEFAULT SYSUTCDATETIME();
            """
        )

        cursor.execute(
            """
            IF OBJECT_ID('dbo.Mensajes', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.Mensajes (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    ConversacionId INT NOT NULL,
                    Pregunta NVARCHAR(MAX) NOT NULL,
                    Respuesta NVARCHAR(MAX) NOT NULL,
                    TiempoRespuestaMs INT NULL,
                    FechaCreacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END
            """
        )

        cursor.execute(
            """
            IF COL_LENGTH('dbo.Mensajes', 'TiempoRespuestaMs') IS NULL
                ALTER TABLE dbo.Mensajes ADD TiempoRespuestaMs INT NULL;
            IF COL_LENGTH('dbo.Mensajes', 'FechaCreacion') IS NULL
                ALTER TABLE dbo.Mensajes ADD FechaCreacion DATETIME2 NOT NULL CONSTRAINT DF_Mensajes_FechaCreacion DEFAULT SYSUTCDATETIME();
            """
        )

        cursor.execute(
            """
            IF OBJECT_ID('dbo.InteraccionesRAG', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.InteraccionesRAG (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    ConversacionId INT NULL,
                    Pregunta NVARCHAR(MAX) NOT NULL,
                    Respuesta NVARCHAR(MAX) NOT NULL,
                    Fuentes NVARCHAR(MAX) NULL,
                    Contexto NVARCHAR(MAX) NULL,
                    Estado NVARCHAR(20) NOT NULL DEFAULT 'pendiente',
                    Confianza FLOAT NULL,
                    FechaCreacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    FechaRevision DATETIME2 NULL,
                    RevisadoPor NVARCHAR(120) NULL
                );
            END
            """
        )

        cursor.execute(
            """
            IF OBJECT_ID('dbo.ConocimientoValidado', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.ConocimientoValidado (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    InteraccionId INT NOT NULL,
                    Pregunta NVARCHAR(MAX) NOT NULL,
                    Respuesta NVARCHAR(MAX) NOT NULL,
                    Fuentes NVARCHAR(MAX) NULL,
                    Contexto NVARCHAR(MAX) NULL,
                    FechaValidacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    ValidadoPor NVARCHAR(120) NULL
                );
            END
            """
        )

        cursor.execute(
            """
            IF OBJECT_ID('dbo.ModelErrorEvents', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.ModelErrorEvents (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    Modelo NVARCHAR(120) NOT NULL,
                    StatusCode INT NOT NULL,
                    ErrorKind NVARCHAR(40) NULL,
                    Origen NVARCHAR(40) NULL,
                    FechaCreacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END
            """
        )

        cursor.execute(
            """
            IF COL_LENGTH('dbo.InteraccionesRAG', 'PromptTokens') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD PromptTokens INT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'CompletionTokens') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD CompletionTokens INT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'TotalTokens') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD TotalTokens INT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'Modelo') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD Modelo NVARCHAR(120) NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'ModeloBase') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD ModeloBase NVARCHAR(120) NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'ModeloFinal') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD ModeloFinal NVARCHAR(120) NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'ConfianzaBase') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD ConfianzaBase FLOAT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'ConfianzaFinal') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD ConfianzaFinal FLOAT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'Escalado') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD Escalado BIT NOT NULL CONSTRAINT DF_InteraccionesRAG_Escalado DEFAULT 0;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'MotivoEscalado') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD MotivoEscalado NVARCHAR(80) NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'Ruta') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD Ruta NVARCHAR(40) NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'DesdeMemoria') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD DesdeMemoria BIT NOT NULL CONSTRAINT DF_InteraccionesRAG_DesdeMemoria DEFAULT 0;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'TiempoRespuestaMs') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD TiempoRespuestaMs INT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'RouterMs') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD RouterMs INT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'RagMs') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD RagMs INT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'LlmMs') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD LlmMs INT NULL;
            IF COL_LENGTH('dbo.InteraccionesRAG', 'DbMs') IS NULL
                ALTER TABLE dbo.InteraccionesRAG ADD DbMs INT NULL;
            """
        )

        cursor.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InteraccionesRAG_Estado'
                           AND object_id = OBJECT_ID('dbo.InteraccionesRAG'))
                CREATE INDEX IX_InteraccionesRAG_Estado
                    ON dbo.InteraccionesRAG (Estado);

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InteraccionesRAG_FechaCreacion'
                           AND object_id = OBJECT_ID('dbo.InteraccionesRAG'))
                CREATE INDEX IX_InteraccionesRAG_FechaCreacion
                    ON dbo.InteraccionesRAG (FechaCreacion);

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InteraccionesRAG_Modelo'
                           AND object_id = OBJECT_ID('dbo.InteraccionesRAG'))
                CREATE INDEX IX_InteraccionesRAG_Modelo
                    ON dbo.InteraccionesRAG (Modelo)
                    INCLUDE (Estado, TotalTokens, PromptTokens, CompletionTokens, TiempoRespuestaMs);

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InteraccionesRAG_ConversacionId'
                           AND object_id = OBJECT_ID('dbo.InteraccionesRAG'))
                CREATE INDEX IX_InteraccionesRAG_ConversacionId
                    ON dbo.InteraccionesRAG (ConversacionId);

            IF OBJECT_ID('dbo.Conversaciones', 'U') IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Conversaciones_UsuarioId'
                               AND object_id = OBJECT_ID('dbo.Conversaciones'))
                CREATE INDEX IX_Conversaciones_UsuarioId
                    ON dbo.Conversaciones (UsuarioId)
                    INCLUDE (FechaCreacion, Estado);

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ModelErrorEvents_Modelo_Fecha'
                           AND object_id = OBJECT_ID('dbo.ModelErrorEvents'))
                CREATE INDEX IX_ModelErrorEvents_Modelo_Fecha
                    ON dbo.ModelErrorEvents (Modelo, FechaCreacion)
                    INCLUDE (StatusCode);

            IF OBJECT_ID('dbo.Mensajes', 'U') IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Mensajes_ConversacionId'
                               AND object_id = OBJECT_ID('dbo.Mensajes'))
                CREATE INDEX IX_Mensajes_ConversacionId
                    ON dbo.Mensajes (ConversacionId)
                    INCLUDE (FechaCreacion);
            """
        )

        cursor.execute(
            """
            IF OBJECT_ID('dbo.DeploymentNotificationSettings', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.DeploymentNotificationSettings (
                    Id INT NOT NULL PRIMARY KEY,
                    Recipients NVARCHAR(MAX) NOT NULL,
                    UpdatedBy NVARCHAR(255) NULL,
                    UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END

            IF OBJECT_ID('dbo.DeploymentRuns', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.DeploymentRuns (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    GitHubRunId BIGINT NOT NULL,
                    RunNumber INT NULL,
                    RunAttempt INT NULL,
                    WorkflowName NVARCHAR(120) NULL,
                    Branch NVARCHAR(120) NULL,
                    RequestedAction NVARCHAR(40) NULL,
                    TriggerSource NVARCHAR(40) NULL,
                    TriggeredByEmail NVARCHAR(255) NULL,
                    TriggeredByName NVARCHAR(255) NULL,
                    Actor NVARCHAR(255) NULL,
                    Status NVARCHAR(40) NULL,
                    Conclusion NVARCHAR(40) NULL,
                    HtmlUrl NVARCHAR(500) NULL,
                    LogsUrl NVARCHAR(500) NULL,
                    BackendUrl NVARCHAR(500) NULL,
                    FrontendUrl NVARCHAR(500) NULL,
                    StartedAt DATETIME2 NULL,
                    CompletedAt DATETIME2 NULL,
                    DurationSeconds INT NULL,
                    LastNotifiedConclusion NVARCHAR(40) NULL,
                    NotificationSentAt DATETIME2 NULL,
                    CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END

            IF COL_LENGTH('dbo.DeploymentRuns', 'RunNumber') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD RunNumber INT NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'RunAttempt') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD RunAttempt INT NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'WorkflowName') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD WorkflowName NVARCHAR(120) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'Branch') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD Branch NVARCHAR(120) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'RequestedAction') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD RequestedAction NVARCHAR(40) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'TriggerSource') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD TriggerSource NVARCHAR(40) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'TriggeredByEmail') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD TriggeredByEmail NVARCHAR(255) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'TriggeredByName') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD TriggeredByName NVARCHAR(255) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'Actor') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD Actor NVARCHAR(255) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'Status') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD Status NVARCHAR(40) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'Conclusion') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD Conclusion NVARCHAR(40) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'HtmlUrl') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD HtmlUrl NVARCHAR(500) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'LogsUrl') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD LogsUrl NVARCHAR(500) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'BackendUrl') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD BackendUrl NVARCHAR(500) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'FrontendUrl') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD FrontendUrl NVARCHAR(500) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'StartedAt') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD StartedAt DATETIME2 NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'CompletedAt') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD CompletedAt DATETIME2 NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'DurationSeconds') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD DurationSeconds INT NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'LastNotifiedConclusion') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD LastNotifiedConclusion NVARCHAR(40) NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'NotificationSentAt') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD NotificationSentAt DATETIME2 NULL;
            IF COL_LENGTH('dbo.DeploymentRuns', 'CreatedAt') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_DeploymentRuns_CreatedAt DEFAULT SYSUTCDATETIME();
            IF COL_LENGTH('dbo.DeploymentRuns', 'UpdatedAt') IS NULL
                ALTER TABLE dbo.DeploymentRuns ADD UpdatedAt DATETIME2 NOT NULL CONSTRAINT DF_DeploymentRuns_UpdatedAt DEFAULT SYSUTCDATETIME();

            IF NOT EXISTS (
                SELECT 1 FROM dbo.DeploymentNotificationSettings WHERE Id = 1
            )
            BEGIN
                INSERT INTO dbo.DeploymentNotificationSettings (Id, Recipients, UpdatedBy)
                VALUES (1, 'jcanete@regeneraenergy.es,acarrillo@regeneraenergy.es', 'system');
            END

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_DeploymentRuns_GitHubRunId'
                           AND object_id = OBJECT_ID('dbo.DeploymentRuns'))
                CREATE UNIQUE INDEX UX_DeploymentRuns_GitHubRunId
                    ON dbo.DeploymentRuns (GitHubRunId);

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DeploymentRuns_UpdatedAt'
                           AND object_id = OBJECT_ID('dbo.DeploymentRuns'))
                CREATE INDEX IX_DeploymentRuns_UpdatedAt
                    ON dbo.DeploymentRuns (UpdatedAt DESC)
                    INCLUDE (Branch, Status, Conclusion, RequestedAction, HtmlUrl, LogsUrl);
            """
        )

        cursor.execute(
            """
            IF OBJECT_ID('dbo.DocumentosFuente', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.DocumentosFuente (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    Backend NVARCHAR(40) NOT NULL,
                    SourceKey CHAR(64) NOT NULL,
                    RutaFuente NVARCHAR(2048) NOT NULL,
                    NombreDocumento NVARCHAR(512) NOT NULL,
                    HashContenido NVARCHAR(128) NOT NULL DEFAULT '',
                    Departamento NVARCHAR(120) NOT NULL DEFAULT '',
                    Dominio NVARCHAR(120) NOT NULL DEFAULT '',
                    TipoDocumento NVARCHAR(120) NOT NULL DEFAULT '',
                    CapaAutoridad NVARCHAR(120) NOT NULL DEFAULT '',
                    Propietario NVARCHAR(255) NULL,
                    VersionDocumento NVARCHAR(120) NULL,
                    FechaRevision DATE NULL,
                    FechaCaducidad DATE NULL,
                    EstadoVigencia NVARCHAR(40) NOT NULL DEFAULT 'pendiente_revision',
                    EstadoIndexacion NVARCHAR(20) NOT NULL DEFAULT 'indexado',
                    UltimaIndexacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    UltimaDeteccion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    CreadoEn DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    ActualizadoEn DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END;

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_DocumentosFuente_SourceKey'
                           AND object_id = OBJECT_ID('dbo.DocumentosFuente'))
                CREATE UNIQUE INDEX UX_DocumentosFuente_SourceKey ON dbo.DocumentosFuente (SourceKey);

            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DocumentosFuente_Backend_Estado'
                           AND object_id = OBJECT_ID('dbo.DocumentosFuente'))
                CREATE INDEX IX_DocumentosFuente_Backend_Estado
                    ON dbo.DocumentosFuente (Backend, EstadoIndexacion)
                    INCLUDE (Dominio, Departamento, EstadoVigencia, UltimaIndexacion);
            """
        )
