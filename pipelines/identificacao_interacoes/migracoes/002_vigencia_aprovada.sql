-- Retirada explícita de uma versão, preservando integralmente seu histórico.
ALTER TABLE processamentos_interacoes
    ADD COLUMN IF NOT EXISTS invalidado_em TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS motivo_invalidacao TEXT;

COMMENT ON COLUMN processamentos_interacoes.invalidado_em IS
    'Retirada explícita por erro reconhecido. Falha ou revisão de nova tentativa não preenche este campo.';

CREATE OR REPLACE VIEW processamentos_interacoes_vigentes AS
SELECT DISTINCT ON (p.medicamento_origem) p.*
FROM processamentos_interacoes AS p
JOIN bulas AS b ON b.nome_normalizado = p.medicamento_origem
JOIN bulas_interacoes AS bi ON bi.nome_normalizado = p.medicamento_origem
WHERE p.status IN ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES')
  AND p.invalidado_em IS NULL
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
    'Última conclusão operacional sem revisão pendente, não retirada e compatível com a fonte. Não constitui aprovação clínica.';

-- Último resultado registrado (concluído ou em revisão) para a fonte atual.
-- Serve apenas para a retomada em lote não repetir, com a mesma versão, um
-- item que já terminou em revisão; nunca é lido pela API.
CREATE OR REPLACE VIEW processamentos_interacoes_registrados AS
SELECT DISTINCT ON (p.medicamento_origem) p.*
FROM processamentos_interacoes AS p
JOIN bulas AS b ON b.nome_normalizado = p.medicamento_origem
JOIN bulas_interacoes AS bi ON bi.nome_normalizado = p.medicamento_origem
WHERE p.status IN ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES', 'REVISAO_NECESSARIA')
  AND p.invalidado_em IS NULL
  AND bi.status_extracao = 'CONCLUIDO'
  AND bi.trecho_interacoes IS NOT NULL
  AND p.hash_trecho = ENCODE(SHA256(CONVERT_TO(bi.trecho_interacoes, 'UTF8')), 'hex')
  AND bi.numero_registro IS NOT DISTINCT FROM b.numero_registro
  AND bi.expediente IS NOT DISTINCT FROM b.expediente
  AND p.numero_registro IS NOT DISTINCT FROM b.numero_registro
  AND p.expediente IS NOT DISTINCT FROM b.expediente
  AND p.sha256_pdf IS NOT DISTINCT FROM b.sha256_pdf
ORDER BY p.medicamento_origem, p.id DESC;

COMMENT ON VIEW processamentos_interacoes_registrados IS
    'Último resultado não retirado (concluído ou em revisão) compatível com a fonte. Controle de retomada; não é publicação.';
