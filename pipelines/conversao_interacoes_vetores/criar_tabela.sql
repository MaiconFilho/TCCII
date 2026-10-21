-- Estrutura dos vetores dos trechos de interações medicamentosas.
-- A dimensão 384 corresponde ao modelo intfloat/multilingual-e5-small.
-- Troque 384 caso EMBEDDING_MODEL_ID aponte para um modelo de outra dimensão.
-- A aplicação cria tudo isto automaticamente; este arquivo serve de referência.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS bulas_interacoes_embeddings (
    id BIGSERIAL PRIMARY KEY,
    nome_normalizado TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    texto_chunk TEXT NOT NULL,
    texto_hash TEXT NOT NULL,
    dimensao INTEGER NOT NULL,
    quantidade_tokens INTEGER,
    embedding VECTOR(384) NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_embeddings_bula_interacao
        FOREIGN KEY (nome_normalizado)
        REFERENCES bulas_interacoes (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT uq_embedding_chunk
        UNIQUE (nome_normalizado, chunk_index)
);

-- Índice de busca aproximada por cosseno (criado por --criar-indice).
CREATE INDEX IF NOT EXISTS idx_bulas_interacoes_embeddings_hnsw
ON bulas_interacoes_embeddings
USING hnsw (embedding vector_cosine_ops);


-- ===========================================================================
-- Banco criado na versão anterior desta tabela
-- ===========================================================================
-- A primeira versão tinha as colunas "modelo" e "criado_em". Se o seu banco
-- ainda as tiver, a aplicação avisa e não grava nada até que sejam removidas.
-- Rode o bloco abaixo UMA VEZ. Nenhum vetor é apagado: saem só as duas
-- colunas, e a restrição única passa a ser (nome_normalizado, chunk_index).
-- Em bancos novos, nada disto é necessário.

-- Antes: confira se há conflito. Sem a coluna "modelo", só pode existir uma
-- linha por (medicamento, chunk). Esta consulta deve voltar VAZIA.
--
--     SELECT nome_normalizado, chunk_index, COUNT(*) AS linhas
--     FROM bulas_interacoes_embeddings
--     GROUP BY nome_normalizado, chunk_index
--     HAVING COUNT(*) > 1;

-- Depois: a alteração. Tudo em uma transação — se algo falhar, nada muda.
--
--     BEGIN;
--
--     ALTER TABLE bulas_interacoes_embeddings
--         DROP CONSTRAINT IF EXISTS uq_embedding_chunk_modelo;
--
--     ALTER TABLE bulas_interacoes_embeddings
--         DROP COLUMN IF EXISTS modelo,
--         DROP COLUMN IF EXISTS criado_em;
--
--     ALTER TABLE bulas_interacoes_embeddings
--         ADD CONSTRAINT uq_embedding_chunk
--         UNIQUE (nome_normalizado, chunk_index);
--
--     COMMIT;
