-- Fixture anterior, exclusivamente para banco local descartável de testes.
CREATE TABLE bulas (
    nome_normalizado TEXT PRIMARY KEY,nome_anvisa TEXT,numero_registro TEXT,expediente TEXT,
    principios_ativos TEXT,composicao TEXT,status TEXT,tentativas INTEGER,tempo_total_segundos NUMERIC,
    sha256_pdf TEXT,id_produto TEXT,data_publicacao_anvisa TIMESTAMPTZ,caminho_pdf TEXT
);
CREATE TABLE bulas_interacoes (
    nome_normalizado TEXT PRIMARY KEY REFERENCES bulas(nome_normalizado),numero_registro TEXT,expediente TEXT,
    trecho_interacoes TEXT,status_extracao TEXT,detalhe_revisao TEXT,
    tempo_leitura_segundos NUMERIC,tempo_inferencia_segundos NUMERIC,tempo_total_segundos NUMERIC
);
