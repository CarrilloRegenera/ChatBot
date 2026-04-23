import pyodbc

def get_connection():
    conn = pyodbc.connect(
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=localhost\\SQLEXPRESS;"
        "Database=ChatBot;"
        "Trusted_Connection=yes;"
    )
    return conn


def ensure_app_schema():
    conn = get_connection()
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
        IF COL_LENGTH('dbo.InteraccionesRAG', 'PromptTokens') IS NULL
            ALTER TABLE dbo.InteraccionesRAG ADD PromptTokens INT NULL;
        IF COL_LENGTH('dbo.InteraccionesRAG', 'CompletionTokens') IS NULL
            ALTER TABLE dbo.InteraccionesRAG ADD CompletionTokens INT NULL;
        IF COL_LENGTH('dbo.InteraccionesRAG', 'TotalTokens') IS NULL
            ALTER TABLE dbo.InteraccionesRAG ADD TotalTokens INT NULL;
        IF COL_LENGTH('dbo.InteraccionesRAG', 'Modelo') IS NULL
            ALTER TABLE dbo.InteraccionesRAG ADD Modelo NVARCHAR(120) NULL;
        IF COL_LENGTH('dbo.InteraccionesRAG', 'Ruta') IS NULL
            ALTER TABLE dbo.InteraccionesRAG ADD Ruta NVARCHAR(40) NULL;
        IF COL_LENGTH('dbo.InteraccionesRAG', 'DesdeMemoria') IS NULL
            ALTER TABLE dbo.InteraccionesRAG ADD DesdeMemoria BIT NOT NULL CONSTRAINT DF_InteraccionesRAG_DesdeMemoria DEFAULT 0;
        IF COL_LENGTH('dbo.InteraccionesRAG', 'TiempoRespuestaMs') IS NULL
            ALTER TABLE dbo.InteraccionesRAG ADD TiempoRespuestaMs INT NULL;
        """
    )

    conn.commit()
    conn.close()
