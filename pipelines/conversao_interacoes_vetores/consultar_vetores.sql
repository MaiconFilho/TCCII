-- Consulta o vetor gravado para um medicamento.
SELECT
    e.nome_normalizado,
    e.chunk_index,
    e.dimensao,
    e.quantidade_tokens,
    LEFT(e.texto_chunk, 120) AS inicio_do_chunk,
    e.texto_hash,
    LEFT(e.embedding::text, 80) || '...' AS inicio_do_vetor,
    e.atualizado_em
FROM bulas_interacoes_embeddings AS e
WHERE e.nome_normalizado = 'a saude da mulher'
ORDER BY e.chunk_index;

-- O texto oficial continua em bulas_interacoes; numero_registro e expediente
-- vêm por junção, sem duplicação de dados.
SELECT
    e.nome_normalizado,
    e.chunk_index,
    bi.numero_registro,
    bi.expediente,
    LENGTH(bi.trecho_interacoes) AS caracteres_do_texto_oficial
FROM bulas_interacoes_embeddings AS e
JOIN bulas_interacoes AS bi USING (nome_normalizado)
WHERE e.nome_normalizado = 'a saude da mulher'
ORDER BY e.chunk_index;

-- Confere a norma: com EMBEDDING_NORMALIZE=true deve ficar próxima de 1.
SELECT
    nome_normalizado,
    chunk_index,
    ROUND((embedding <-> ARRAY_FILL(0::real, ARRAY[dimensao])::vector)::numeric, 6) AS norma
FROM bulas_interacoes_embeddings
WHERE nome_normalizado = 'a saude da mulher'
ORDER BY chunk_index;

-- Progresso da vetorização.
SELECT
    (SELECT COUNT(*) FROM bulas_interacoes
      WHERE trecho_interacoes IS NOT NULL
        AND BTRIM(trecho_interacoes) <> '') AS medicamentos_com_texto,
    COUNT(DISTINCT nome_normalizado) AS ja_vetorizados,
    COUNT(*) AS chunks
FROM bulas_interacoes_embeddings;
