-- Desglose de latencia para interacciones RAG. Es aditivo e idempotente.
SET NOCOUNT ON;

IF COL_LENGTH('dbo.InteraccionesRAG', 'RouterMs') IS NULL
    ALTER TABLE dbo.InteraccionesRAG ADD RouterMs INT NULL;
IF COL_LENGTH('dbo.InteraccionesRAG', 'RagMs') IS NULL
    ALTER TABLE dbo.InteraccionesRAG ADD RagMs INT NULL;
IF COL_LENGTH('dbo.InteraccionesRAG', 'LlmMs') IS NULL
    ALTER TABLE dbo.InteraccionesRAG ADD LlmMs INT NULL;
IF COL_LENGTH('dbo.InteraccionesRAG', 'DbMs') IS NULL
    ALTER TABLE dbo.InteraccionesRAG ADD DbMs INT NULL;
