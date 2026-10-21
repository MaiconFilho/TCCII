CREATE TABLE IF NOT EXISTS bulas_interacoes (
    nome_normalizado TEXT PRIMARY KEY,
    numero_registro TEXT NOT NULL,
    expediente TEXT,
    trecho_interacoes TEXT,
    status_extracao TEXT NOT NULL DEFAULT 'CONCLUIDO',
    detalhe_revisao TEXT,
    tempo_leitura_segundos NUMERIC(12,3),
    tempo_inferencia_segundos NUMERIC(12,3),
    tempo_total_segundos NUMERIC(12,3),

    CONSTRAINT fk_bulas_interacoes_bula
        FOREIGN KEY (nome_normalizado)
        REFERENCES bulas (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);
