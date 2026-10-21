-- Migração 001: identificação estruturada das interações.
--
-- Aditiva e idempotente: apenas cria tabelas, índices, comentários e uma view.
-- Não altera bulas, bulas_interacoes nem bulas_interacoes_embeddings e nunca
-- executa DROP, DELETE ou TRUNCATE. Aplique conectado ao banco TCC com:
--     python -m pipelines.identificacao_interacoes.aplicar_migracoes
-- ou no psql/pgAdmin executando este arquivo inteiro.

-- ------------------------------------------------------------------ controle

CREATE TABLE IF NOT EXISTS processamentos_interacoes (
    id BIGSERIAL PRIMARY KEY,
    medicamento_origem TEXT NOT NULL,
    status TEXT NOT NULL,
    versao_processamento TEXT NOT NULL,
    modelo TEXT NOT NULL,
    hash_trecho TEXT NOT NULL,
    caracteres_trecho INTEGER NOT NULL,
    numero_registro TEXT,
    expediente TEXT,
    sha256_pdf TEXT,
    id_produto TEXT,
    data_publicacao_anvisa TIMESTAMPTZ,
    quantidade_janelas INTEGER NOT NULL DEFAULT 0,
    janelas_concluidas INTEGER NOT NULL DEFAULT 0,
    quantidade_relacoes INTEGER NOT NULL DEFAULT 0,
    quantidade_rejeicoes INTEGER NOT NULL DEFAULT 0,
    quantidade_vinculos INTEGER NOT NULL DEFAULT 0,
    chamadas_llm INTEGER NOT NULL DEFAULT 0,
    tempo_inferencia_segundos NUMERIC(12, 3),
    tempo_total_segundos NUMERIC(12, 3),
    pico_memoria_mb NUMERIC(10, 1),
    detalhe TEXT,
    iniciado_em TIMESTAMPTZ NOT NULL,
    concluido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_processamentos_interacoes_bula
        FOREIGN KEY (medicamento_origem)
        REFERENCES bulas (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT ck_processamentos_interacoes_status
        CHECK (status IN (
            'CONCLUIDO_COM_RELACOES',
            'CONCLUIDO_SEM_RELACOES',
            'REVISAO_NECESSARIA',
            'FALHA'
        )),

    CONSTRAINT ck_processamentos_interacoes_janelas
        CHECK (
            janelas_concluidas BETWEEN 0 AND quantidade_janelas
            AND (status = 'FALHA' OR janelas_concluidas = quantidade_janelas)
        ),

    CONSTRAINT ck_processamentos_interacoes_relacoes
        CHECK (
            (status <> 'CONCLUIDO_SEM_RELACOES' OR quantidade_relacoes = 0)
            AND (status <> 'CONCLUIDO_COM_RELACOES' OR quantidade_relacoes > 0)
            AND (status <> 'FALHA' OR quantidade_relacoes = 0)
        )
);

CREATE INDEX IF NOT EXISTS idx_processamentos_interacoes_origem
    ON processamentos_interacoes (medicamento_origem, id DESC);

COMMENT ON TABLE processamentos_interacoes IS
    'Controle e histórico de cada execução da identificação por medicamento. Ausência de linha significa não processado. Linhas nunca são apagadas.';
COMMENT ON COLUMN processamentos_interacoes.medicamento_origem IS
    'Medicamento cuja bula foi analisada; vem do registro selecionado em bulas, nunca do modelo.';
COMMENT ON COLUMN processamentos_interacoes.status IS
    'CONCLUIDO_COM_RELACOES, CONCLUIDO_SEM_RELACOES e REVISAO_NECESSARIA percorreram todas as janelas; FALHA não grava relações. Conclusão operacional não garante que o modelo encontrou todas as relações.';
COMMENT ON COLUMN processamentos_interacoes.versao_processamento IS
    'Versão do prompt (com hash das instruções e do esquema), modelo e parâmetros de janela e saída.';
COMMENT ON COLUMN processamentos_interacoes.hash_trecho IS
    'SHA-256 de bulas_interacoes.trecho_interacoes em UTF-8 no momento da análise; identifica a versão do texto.';
COMMENT ON COLUMN processamentos_interacoes.numero_registro IS
    'Cópia do registro da bula analisada, para manter a evidência ligada à bula usada mesmo após atualização de bulas.';
COMMENT ON COLUMN processamentos_interacoes.expediente IS
    'Cópia do expediente da bula analisada.';
COMMENT ON COLUMN processamentos_interacoes.sha256_pdf IS
    'Cópia do hash do PDF de origem da bula analisada.';
COMMENT ON COLUMN processamentos_interacoes.data_publicacao_anvisa IS
    'Data de publicação da bula analisada no Bulário; difere de concluido_em, que é a data do processamento.';
COMMENT ON COLUMN processamentos_interacoes.quantidade_rejeicoes IS
    'Relações propostas pelo modelo e rejeitadas pela validação; o detalhe fica no relatório da execução.';
COMMENT ON COLUMN processamentos_interacoes.detalhe IS
    'Motivo da falha ou da revisão necessária, sem dados sensíveis.';
COMMENT ON COLUMN processamentos_interacoes.concluido_em IS
    'Data do processamento (não é a data de publicação da bula).';

-- ----------------------------------------------------------------- domínio

CREATE TABLE IF NOT EXISTS interacoes (
    id BIGSERIAL PRIMARY KEY,
    medicamento_origem TEXT NOT NULL,
    alvo TEXT NOT NULL,
    tipo_alvo TEXT NOT NULL,
    resumo TEXT NOT NULL,
    evidencia TEXT NOT NULL,
    processamento_id BIGINT NOT NULL,

    CONSTRAINT fk_interacoes_bula
        FOREIGN KEY (medicamento_origem)
        REFERENCES bulas (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_interacoes_processamento
        FOREIGN KEY (processamento_id)
        REFERENCES processamentos_interacoes (id)
        ON DELETE RESTRICT,

    CONSTRAINT ck_interacoes_tipo_alvo
        CHECK (tipo_alvo IN (
            'PRINCIPIO_ATIVO',
            'MEDICAMENTO',
            'CLASSE_FARMACOLOGICA',
            'ALIMENTO',
            'GRUPO_ALIMENTAR',
            'OUTRA_SUBSTANCIA',
            'EXAME_LABORATORIAL',
            'OUTRO'
        )),

    CONSTRAINT ck_interacoes_textos
        CHECK (BTRIM(alvo) <> '' AND BTRIM(resumo) <> '' AND BTRIM(evidencia) <> '')
);

CREATE INDEX IF NOT EXISTS idx_interacoes_processamento
    ON interacoes (processamento_id, id);
CREATE INDEX IF NOT EXISTS idx_interacoes_origem
    ON interacoes (medicamento_origem);

COMMENT ON TABLE interacoes IS
    'Todas as relações identificadas no tópico de interações, inclusive com classes, alimentos e alvos sem medicamento no cadastro.';
COMMENT ON COLUMN interacoes.id IS 'Identificador da interação.';
COMMENT ON COLUMN interacoes.medicamento_origem IS
    'Bula em que a relação está descrita (bulas.nome_normalizado).';
COMMENT ON COLUMN interacoes.alvo IS
    'Alvo textual citado na bula (substância, medicamento, classe, alimento etc.); não precisa existir em outra tabela.';
COMMENT ON COLUMN interacoes.tipo_alvo IS
    'PRINCIPIO_ATIVO, MEDICAMENTO, CLASSE_FARMACOLOGICA, ALIMENTO, GRUPO_ALIMENTAR, OUTRA_SUBSTANCIA, EXAME_LABORATORIAL ou OUTRO.';
COMMENT ON COLUMN interacoes.resumo IS
    'Texto curto gerado pelo modelo e conferido contra a evidência; não define gravidade.';
COMMENT ON COLUMN interacoes.evidencia IS
    'Passagem literal recortada de bulas_interacoes.trecho_interacoes, com a frase completa.';
COMMENT ON COLUMN interacoes.processamento_id IS
    'Vínculo técnico com a versão do processamento que produziu a relação.';

CREATE TABLE IF NOT EXISTS interacoes_medicamentos (
    interacao_id BIGINT NOT NULL,
    medicamento_relacionado TEXT NOT NULL,
    criterio_vinculo TEXT NOT NULL,
    fundamento_vinculo TEXT NOT NULL,
    vinculado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    invalidado_em TIMESTAMPTZ,

    CONSTRAINT pk_interacoes_medicamentos
        PRIMARY KEY (interacao_id, medicamento_relacionado),

    CONSTRAINT fk_interacoes_medicamentos_interacao
        FOREIGN KEY (interacao_id)
        REFERENCES interacoes (id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_interacoes_medicamentos_bula
        FOREIGN KEY (medicamento_relacionado)
        REFERENCES bulas (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT ck_interacoes_medicamentos_criterio
        CHECK (criterio_vinculo IN (
            'PRINCIPIO_ATIVO_EXATO',
            'NOME_MEDICAMENTO_EXATO',
            'COMPOSICAO_EQUIVALENCIA_DECLARADA'
        ))
);

CREATE INDEX IF NOT EXISTS idx_interacoes_medicamentos_relacionado
    ON interacoes_medicamentos (medicamento_relacionado, interacao_id)
    WHERE invalidado_em IS NULL;

COMMENT ON TABLE interacoes_medicamentos IS
    'Medicamentos do cadastro ligados a uma interação (zero, um ou vários). Resumo e evidência não são duplicados aqui.';
COMMENT ON COLUMN interacoes_medicamentos.criterio_vinculo IS
    'Regra determinística da correspondência: princípio ativo exato, nome exato ou equivalência declarada na composição.';
COMMENT ON COLUMN interacoes_medicamentos.fundamento_vinculo IS
    'Texto do cadastro que fundamenta o vínculo (princípio ativo, nome ou passagem da composição).';
COMMENT ON COLUMN interacoes_medicamentos.invalidado_em IS
    'Preenchido quando o cadastro deixou de sustentar o vínculo; o vínculo é preservado como histórico em vez de apagado.';

-- ---------------------------------------------------------------- vigência

-- Último processamento concluído e compatível com a fonte atual de cada
-- medicamento: mesmo texto (hash), mesma bula (registro, expediente e PDF) e
-- extração CONCLUIDO coerente com bulas. FALHA nunca é vigente, então uma
-- falha posterior preserva o resultado anterior compatível.
CREATE OR REPLACE VIEW processamentos_interacoes_vigentes AS
SELECT DISTINCT ON (p.medicamento_origem)
    p.*
FROM processamentos_interacoes AS p
JOIN bulas AS b
    ON b.nome_normalizado = p.medicamento_origem
JOIN bulas_interacoes AS bi
    ON bi.nome_normalizado = p.medicamento_origem
WHERE p.status IN ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES', 'REVISAO_NECESSARIA')
  AND bi.status_extracao = 'CONCLUIDO'
  AND bi.trecho_interacoes IS NOT NULL
  AND p.hash_trecho = ENCODE(SHA256(CONVERT_TO(bi.trecho_interacoes, 'UTF8')), 'hex')
  AND bi.numero_registro IS NOT DISTINCT FROM b.numero_registro
  AND bi.expediente IS NOT DISTINCT FROM b.expediente
  AND p.numero_registro IS NOT DISTINCT FROM b.numero_registro
  AND p.expediente IS NOT DISTINCT FROM b.expediente
  AND p.sha256_pdf IS NOT DISTINCT FROM b.sha256_pdf
ORDER BY p.medicamento_origem, p.id DESC;

COMMENT ON VIEW processamentos_interacoes_vigentes IS
    'Processamento concluído mais recente e compatível com a versão atual do texto e da bula. Resultados de versões antigas não aparecem aqui.';
