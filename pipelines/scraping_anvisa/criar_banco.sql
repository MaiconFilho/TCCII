-- ETAPA 1: execute somente este comando conectado ao banco postgres, fora de transação.
CREATE DATABASE "TCC";

-- ETAPA 2: abra uma nova conexão ao banco TCC antes de executar o restante.
-- No pgAdmin, abra o Query Tool do banco TCC e execute apenas esta etapa.
-- Esquema inicial; CREATE TABLE IF NOT EXISTS não migra tabelas existentes.

CREATE TABLE IF NOT EXISTS bulas (
    nome_normalizado TEXT PRIMARY KEY,
    quantidade_registros_planilha INTEGER NOT NULL DEFAULT 1,
    nome_anvisa TEXT,
    principios_ativos TEXT,
    composicao TEXT,
    numero_registro TEXT,
    expediente TEXT,
    id_produto TEXT,
    id_bula_profissional TEXT,
    data_publicacao_anvisa TIMESTAMPTZ,
    status TEXT NOT NULL,
    tentativas INTEGER NOT NULL DEFAULT 0,
    caminho_pdf TEXT,
    sha256_pdf TEXT,
    tamanho_pdf BIGINT,
    tempo_consulta_segundos NUMERIC(12, 3),
    tempo_download_segundos NUMERIC(12, 3),
    tempo_total_segundos NUMERIC(12, 3),
    mensagem TEXT,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bulas_status ON bulas (status);

COMMENT ON TABLE bulas IS 'Controle da coleta da bula profissional mais recente por nome de medicamento.';
COMMENT ON COLUMN bulas.nome_normalizado IS 'Chave normalizada usada para impedir mais de uma bula por nome.';
COMMENT ON COLUMN bulas.principios_ativos IS 'Nomes literais dos principios ativos confirmados na composicao da bula, separados por ponto e virgula.';
COMMENT ON COLUMN bulas.composicao IS 'Texto integral da secao de composicao extraido do PDF pela etapa de extracao.';
COMMENT ON COLUMN bulas.data_publicacao_anvisa IS 'Data de publicação da bula no Bulário Eletrônico, obtida do campo data da API.';
COMMENT ON COLUMN bulas.tempo_consulta_segundos IS 'Tempo em segundos da consulta paginada e seleção da bula mais recente, medido com relógio monotônico.';
COMMENT ON COLUMN bulas.tempo_download_segundos IS 'Tempo em segundos da transferência e recebimento do PDF.';
COMMENT ON COLUMN bulas.tempo_total_segundos IS 'Tempo em segundos desde o início da consulta até a validação e preparação do PDF ou registro do erro.';

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
