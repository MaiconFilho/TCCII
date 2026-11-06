# Backup do banco InteraMed

[`interamed_atual.dump`](interamed_atual.dump) foi extraído diretamente do
Supabase após a readequação ao modelo de cinco tabelas e 60 campos.

- Origem: projeto `vuowgvmmqbwfhccncxvg`, banco `postgres`, schema `public`.
- Instante do snapshot: **05/11/2026, 21:04:33 UTC (18:04:33 em São Paulo)**.
- Ferramenta: `pg_dump` 17.5, formato custom.
- Tamanho: **20.237.485 bytes**.
- SHA-256: `d13570d737b3ce8424273015c5a37d236e63baab4d17b3569d44bb5dd271e6bb`.

| Tabela | Registros |
| --- | ---: |
| `bulas` | 5895 |
| `bulas_interacoes` | 5867 |
| `processamentos_interacoes` | 5783 |
| `interacoes` | 67351 |
| `interacoes_medicamentos` | 555908 |

O dump foi restaurado em banco isolado e conferido por PK, schema, API e
sequências. Os 4.512 cards simples e 286 resumos gerais simples Smol foram
preservados, com seus originais, evidências e controles.

O arquivo contém somente as tabelas da aplicação e seus objetos de apoio.
Não inclui `auth`, `storage`, PDFs, modelos ou arquivos de ambiente.
Backups de segurança, manifestos e relatórios da operação permanecem locais.

## Restaurar em outro banco

Use PostgreSQL 17 ou superior e um destino local novo. O dump recria o schema
`public`; remova somente esse schema vazio antes de restaurar:

```powershell
createdb -h localhost -U postgres interamed_test_restaurado
psql -h localhost -U postgres -d interamed_test_restaurado -c "DROP SCHEMA public RESTRICT"
pg_restore -h localhost -U postgres -d interamed_test_restaurado --no-owner --no-privileges --exit-on-error --single-transaction backups_banco/interamed_atual.dump
```

Não execute esses comandos sobre produção ou sobre um banco com dados.
Configure os papéis e permissões do backend no novo destino; não desative
RLS para liberar consultas. Para uma instalação sem dados, use o SQL completo
[`banco/modelo.sql`](../banco/modelo.sql).
