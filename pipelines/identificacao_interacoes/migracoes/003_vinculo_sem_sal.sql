-- Migração 003: critério de vínculo pela forma sem sal e estratégia por
-- nomes do banco.
--
-- Única alteração de regra: a CHECK de interacoes_medicamentos.criterio_vinculo
-- passa a aceitar PRINCIPIO_ATIVO_SEM_SAL ("varfarina" no texto e "varfarina
-- sódica" no cadastro). A regra antiga é trocada pela nova na mesma instrução,
-- com o mesmo nome; nenhuma linha é alterada ou apagada, e todos os valores
-- já gravados continuam válidos. Idempotente.

ALTER TABLE interacoes_medicamentos
    DROP CONSTRAINT IF EXISTS ck_interacoes_medicamentos_criterio,
    ADD CONSTRAINT ck_interacoes_medicamentos_criterio
        CHECK (criterio_vinculo IN (
            'PRINCIPIO_ATIVO_EXATO',
            'NOME_MEDICAMENTO_EXATO',
            'COMPOSICAO_EQUIVALENCIA_DECLARADA',
            'PRINCIPIO_ATIVO_SEM_SAL'
        ));

COMMENT ON COLUMN interacoes_medicamentos.criterio_vinculo IS
    'Regra determinística da correspondência: princípio ativo exato, princípio ativo sem sal ou hidrato, nome exato ou equivalência declarada na composição.';
COMMENT ON COLUMN interacoes.resumo IS
    'Texto curto sobre o efeito. Na estratégia com LLM, gerado pelo modelo e conferido contra a evidência; na estratégia por nomes do banco (dicionario), recorte literal da evidência. Não define gravidade.';
COMMENT ON COLUMN processamentos_interacoes.modelo IS
    'Modelo usado na identificação, ou "nenhum (busca por nomes do banco)" na estratégia dicionario.';
COMMENT ON COLUMN processamentos_interacoes.versao_processamento IS
    'Versão da estratégia (dicionario/<n> com hash do código, da lista de termos ignorados e dos nomes do banco; ou o prompt com hash, modelo e parâmetros da estratégia com LLM).';
