import logging
import os
from contextlib import contextmanager

import pyodbc


logger = logging.getLogger(__name__)


# pyodbc connection pool — comparte handles entre llamadas y evita el handshake
# por petición. Hay que activarlo ANTES del primer pyodbc.connect().
pyodbc.pooling = True


def _build_connection_string() -> str:
    """Connection string desde env (Azure-ready) sin dependencias locales hardcodeadas."""
    explicit = os.getenv("SQL_CONNECTION_STRING")
    if explicit:
        return explicit
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    server = os.getenv("SQL_SERVER", "").strip()
    database = os.getenv("SQL_DATABASE", "").strip()
    user = os.getenv("SQL_USER", "")
    password = os.getenv("SQL_PASSWORD", "")
    timeout = os.getenv("SQL_CONNECTION_TIMEOUT", "5")
    encrypt = os.getenv("SQL_ENCRYPT", "yes")
    trust_cert = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "no")

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
    return ";".join(parts) + ";"


_CONNECTION_STRING = _build_connection_string()


def get_connection() -> pyodbc.Connection:
    """Conexión cruda. Mantiene compatibilidad. Prefiere `db_conn()` para
    transaccionalidad y cierre garantizado."""
    return pyodbc.connect(_CONNECTION_STRING)


@contextmanager
def db_conn():
    """Context manager con transacción explícita y cierre garantizado.

    - Commit automático si el bloque termina sin excepción.
    - Rollback automático en cualquier excepción, re-elevada.
    - Cierre garantizado de la conexión (devuelve handle al pool).
    """
    conn = pyodbc.connect(_CONNECTION_STRING)
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
