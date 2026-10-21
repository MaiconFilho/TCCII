-- Pode ser executado diretamente no pgAdmin. Apenas acrescenta campos.
-- Se as tabelas da proposta anterior já existirem, seus dados ficam preservados.
ALTER TABLE interacoes ADD COLUMN IF NOT EXISTS resumo_card TEXT;
ALTER TABLE processamentos_interacoes
    ADD COLUMN IF NOT EXISTS resumo_geral TEXT,
    ADD COLUMN IF NOT EXISTS resumo_status TEXT,
    ADD COLUMN IF NOT EXISTS resumo_gerado_em TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resumo_modelo TEXT,
    ADD COLUMN IF NOT EXISTS resumo_erro TEXT,
    ADD COLUMN IF NOT EXISTS resumo_tempo_inferencia_segundos DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS resumo_tempo_total_segundos DOUBLE PRECISION;
