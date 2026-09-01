-- Inventario de gobierno documental para los índices RAG.
-- Ejecutar una vez en Azure SQL antes de activar la sincronización del registro.
SET NOCOUNT ON;

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
