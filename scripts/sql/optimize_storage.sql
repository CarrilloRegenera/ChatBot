-- =============================================================================
-- optimize_storage.sql
-- =============================================================================
-- Aplica PAGE compression a las tablas que crecen con texto largo
-- (NVARCHAR(MAX)) y a los índices secundarios. Ejecutar UNA SOLA VEZ tras
-- desplegar el esquema; o re-ejecutar cuando se añadan nuevos índices.
--
-- Requisitos:
--   - SQL Server 2016 SP1+ o Azure SQL Database (compresión incluida en todos
--     los tiers desde GP).
--   - Sin tráfico crítico mientras corre: REBUILD bloquea brevemente.
--     En Azure SQL puedes añadir WITH (ONLINE = ON) para evitar bloqueos.
--
-- Cómo lanzarlo:
--   sqlcmd -S <server> -d ChatBot -E -i scripts/sql/optimize_storage.sql
--   o desde SSMS abriendo este archivo y F5.
-- =============================================================================

SET NOCOUNT ON;
PRINT 'Aplicando DATA_COMPRESSION = PAGE a tablas e índices...';

-- InteraccionesRAG: la tabla más pesada (Pregunta/Respuesta/Contexto/Fuentes NVARCHAR(MAX))
IF OBJECT_ID('dbo.InteraccionesRAG', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.InteraccionesRAG REBUILD WITH (DATA_COMPRESSION = PAGE);
    PRINT '  ✓ dbo.InteraccionesRAG comprimida';
END

-- ConocimientoValidado: misma forma (texto largo)
IF OBJECT_ID('dbo.ConocimientoValidado', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.ConocimientoValidado REBUILD WITH (DATA_COMPRESSION = PAGE);
    PRINT '  ✓ dbo.ConocimientoValidado comprimida';
END

-- Mensajes: histórico de chat (Pregunta/Respuesta)
IF OBJECT_ID('dbo.Mensajes', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.Mensajes REBUILD WITH (DATA_COMPRESSION = PAGE);
    PRINT '  ✓ dbo.Mensajes comprimida';
END

-- ModelErrorEvents: log de errores 4xx/5xx (no es de texto largo pero crece rápido)
IF OBJECT_ID('dbo.ModelErrorEvents', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.ModelErrorEvents REBUILD WITH (DATA_COMPRESSION = PAGE);
    PRINT '  ✓ dbo.ModelErrorEvents comprimida';
END

-- Índices secundarios: comprimirlos también
DECLARE @sql NVARCHAR(MAX) = N'';
SELECT @sql = @sql + N'ALTER INDEX ' + QUOTENAME(i.name) +
                     N' ON ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) +
                     N' REBUILD WITH (DATA_COMPRESSION = PAGE);' + CHAR(10)
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE i.type_desc IN ('NONCLUSTERED')
  AND i.is_primary_key = 0
  AND t.name IN ('InteraccionesRAG', 'ConocimientoValidado', 'Mensajes', 'ModelErrorEvents');

IF LEN(@sql) > 0
BEGIN
    EXEC sp_executesql @sql;
    PRINT '  ✓ Índices secundarios comprimidos';
END

PRINT 'Listo.';

-- =============================================================================
-- POLÍTICA DE ARCHIVADO (opcional, descomentar cuando se decida la retención):
-- Mover interacciones >90 días a una tabla histórica fría para mantener
-- los índices calientes pequeños. Ajusta el umbral según necesidad.
-- =============================================================================
-- IF OBJECT_ID('dbo.InteraccionesRAG_Archivo', 'U') IS NULL
-- BEGIN
--     SELECT TOP 0 * INTO dbo.InteraccionesRAG_Archivo FROM dbo.InteraccionesRAG;
--     ALTER TABLE dbo.InteraccionesRAG_Archivo REBUILD WITH (DATA_COMPRESSION = PAGE);
-- END
--
-- INSERT INTO dbo.InteraccionesRAG_Archivo
-- SELECT * FROM dbo.InteraccionesRAG
-- WHERE FechaCreacion < DATEADD(day, -90, SYSUTCDATETIME());
--
-- DELETE FROM dbo.InteraccionesRAG
-- WHERE FechaCreacion < DATEADD(day, -90, SYSUTCDATETIME());
