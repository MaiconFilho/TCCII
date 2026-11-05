-- Métricas mantidas pelo modelo aprovado; sem tempos de etapas removidas.
SELECT count(*) AS total_concluido,round(avg(tempo_total_segundos),3) AS media_total_segundos,
    min(tempo_total_segundos) AS menor_total_segundos,max(tempo_total_segundos) AS maior_total_segundos
FROM bulas WHERE status='CONCLUIDO';
SELECT nome_normalizado,numero_registro,expediente,tempo_total_segundos FROM bulas
WHERE status='CONCLUIDO' ORDER BY tempo_total_segundos DESC NULLS LAST;
SELECT status,count(*) AS quantidade FROM bulas GROUP BY status ORDER BY quantidade DESC;
