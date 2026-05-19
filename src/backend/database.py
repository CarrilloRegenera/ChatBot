import pyodbc

def get_connection():
    conn = pyodbc.connect(
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=localhost\\SQLEXPRESS;"
        "Database=ChatBot;"
        "Trusted_Connection=yes;"
        "Encrypt=no;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=5;"
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

        IF OBJECT_ID('dbo.Mensajes', 'U') IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Mensajes_ConversacionId'
                           AND object_id = OBJECT_ID('dbo.Mensajes'))
            CREATE INDEX IX_Mensajes_ConversacionId
                ON dbo.Mensajes (ConversacionId);
        """
    )

    conn.commit()
    conn.close()
