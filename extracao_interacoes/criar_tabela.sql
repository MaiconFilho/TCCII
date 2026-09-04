CREATE TABLE IF NOT EXISTS bulas_interacoes (
    nome_normalizado TEXT PRIMARY KEY,
    numero_registro TEXT NOT NULL,
    expediente TEXT,
    trecho_interacoes TEXT,

    CONSTRAINT fk_bulas_interacoes_bula
        FOREIGN KEY (nome_normalizado)
        REFERENCES bulas (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);
