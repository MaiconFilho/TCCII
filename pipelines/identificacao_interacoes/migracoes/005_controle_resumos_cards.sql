-- Controle de geração por interação, sem modificar evidências ou vínculos.
ALTER TABLE interacoes
    ADD COLUMN IF NOT EXISTS resumo_status TEXT,
    ADD COLUMN IF NOT EXISTS resumo_modelo TEXT,
    ADD COLUMN IF NOT EXISTS resumo_tempo_segundos DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS resumo_erro TEXT,
    ADD COLUMN IF NOT EXISTS resumo_atualizado_em TIMESTAMPTZ;
