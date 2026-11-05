-- Consultas de conferência (somente leitura) das interações identificadas.

-- 1. Situação dos processamentos: histórico completo e o vigente de cada bula.
SELECT p.id, p.medicamento_origem, p.status, p.quantidade_janelas, p.janelas_concluidas,
       p.quantidade_relacoes, p.quantidade_rejeicoes,
       p.tempo_total_segundos, p.pico_memoria_mb, p.concluido_em,
       (v.id IS NOT NULL) AS vigente
FROM processamentos_interacoes AS p
LEFT JOIN processamentos_interacoes_vigentes AS v ON v.id = p.id
ORDER BY p.medicamento_origem, p.id DESC;

-- 2. Relações de uma bula, inclusive classes e alimentos sem vínculo
--    (LEFT JOIN: alvos sem medicamento relacionado não desaparecem).
SELECT i.id, i.alvo, i.tipo_alvo, i.resumo, LEFT(i.evidencia, 160) AS evidencia,
       COUNT(im.medicamento_relacionado) FILTER (WHERE im.invalidado_em IS NULL) AS vinculos_ativos,
       STRING_AGG(im.medicamento_relacionado, ', ' ORDER BY im.medicamento_relacionado)
           FILTER (WHERE im.invalidado_em IS NULL) AS medicamentos_relacionados
FROM processamentos_interacoes_vigentes AS v
JOIN interacoes AS i ON i.processamento_id = v.id
LEFT JOIN interacoes_medicamentos AS im ON im.interacao_id = i.id
WHERE v.medicamento_origem = 'untral'
GROUP BY i.id
ORDER BY i.id;

-- 3. Outras bulas que citam explicitamente um medicamento.
SELECT i.medicamento_origem, i.alvo, i.tipo_alvo, im.criterio_vinculo, i.resumo
FROM interacoes_medicamentos AS im
JOIN interacoes AS i ON i.id = im.interacao_id
JOIN processamentos_interacoes_vigentes AS v ON v.id = i.processamento_id
WHERE im.medicamento_relacionado = 'carbamazepina'
  AND im.invalidado_em IS NULL
ORDER BY i.medicamento_origem, i.id;

-- 4. Evidência gravada é recorte literal do texto analisado?
--    (só para o processamento vigente, cujo texto não mudou).
SELECT i.id, POSITION(i.evidencia IN bi.trecho_interacoes) > 0 AS literal
FROM processamentos_interacoes_vigentes AS v
JOIN interacoes AS i ON i.processamento_id = v.id
JOIN bulas_interacoes AS bi ON bi.nome_normalizado = v.medicamento_origem
ORDER BY i.id;

-- 5. Cobertura do corpus: revisão só conta na ausência de conclusão utilizável.
SELECT (SELECT count(*) FROM bulas_interacoes WHERE status_extracao='CONCLUIDO'
    AND NULLIF(BTRIM(trecho_interacoes),'') IS NOT NULL) AS elegiveis,
    (SELECT count(*) FROM processamentos_interacoes_vigentes) AS concluidas,
    (SELECT count(*) FROM processamentos_interacoes_registrados r WHERE r.status='REVISAO_NECESSARIA'
        AND NOT EXISTS (SELECT 1 FROM processamentos_interacoes_vigentes v WHERE v.medicamento_origem=r.medicamento_origem)) AS em_revisao;

-- 6. Vínculos invalidados pela atualização do cadastro (histórico preservado).
SELECT im.interacao_id, im.medicamento_relacionado, im.criterio_vinculo,
       im.invalidado_em
FROM interacoes_medicamentos AS im
WHERE im.invalidado_em IS NOT NULL
ORDER BY im.invalidado_em DESC;
