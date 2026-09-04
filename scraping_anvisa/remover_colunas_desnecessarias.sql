-- Migração para bancos criados por versões anteriores do scraper.
-- Execute conectado ao banco "TCC". O comando não usa CASCADE.
BEGIN;

ALTER TABLE IF EXISTS bulas
    DROP COLUMN IF EXISTS data_publicacao_original,
    DROP COLUMN IF EXISTS data_atualizacao_base_anvisa,
    DROP COLUMN IF EXISTS data_atualizacao_base_original,
    DROP COLUMN IF EXISTS nome_pesquisado;

COMMIT;
