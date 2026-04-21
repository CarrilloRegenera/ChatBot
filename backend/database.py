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

    conn.commit()
    conn.close()
