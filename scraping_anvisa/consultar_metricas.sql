-- Resumo geral das coletas concluídas.
SELECT
    COUNT(*) AS total_concluido,
    ROUND(AVG(tempo_consulta_segundos), 3) AS media_consulta_segundos,
    MIN(tempo_consulta_segundos) AS menor_consulta_segundos,
    MAX(tempo_consulta_segundos) AS maior_consulta_segundos,
    ROUND(AVG(tempo_download_segundos), 3) AS media_download_segundos,
    ROUND(AVG(tempo_total_segundos), 3) AS media_total_segundos
FROM bulas
WHERE status = 'CONCLUIDO';

-- Tempos por medicamento, do mais demorado para o mais rápido.
SELECT
    nome_normalizado,
    numero_registro,
    data_publicacao_anvisa,
    tempo_consulta_segundos,
    tempo_download_segundos,
    tempo_total_segundos
FROM bulas
WHERE status = 'CONCLUIDO'
ORDER BY tempo_total_segundos DESC;

-- Quantidade de registros por status.
SELECT
    status,
    COUNT(*) AS quantidade
FROM bulas
GROUP BY status
ORDER BY quantidade DESC;
