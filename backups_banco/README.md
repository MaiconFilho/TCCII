# Backup do banco InteraMed

[`interamed_atual.dump`](interamed_atual.dump) foi extraído diretamente do Supabase
de produção **depois** da migração e validação do modelo de cinco tabelas/60 campos.
Foi restaurado em banco local isolado e conferido por PK, schema, API, resumos Smol
e sequências. Está preparado para revisão; **não houve commit nem push**.

- Projeto: `vuowgvmmqbwfhccncxvg`, banco `postgres`, PostgreSQL 17.11.
- Instante: **05/11/2026 21:04:33 UTC**; **05/11/2026 18:04:33 (São Paulo)**.
- Ferramenta: `pg_dump` 17.5, formato custom com compressão padrão.
- Tamanho: **20237485 bytes**.
- Escopo: schema `public`, cinco tabelas, funções, triggers, views, índices e sequências.
  Não inclui serviços Supabase, `auth`, `storage`, PDFs, modelos ou arquivos `.env`.
- SHA-256: `d13570d737b3ce8424273015c5a37d236e63baab4d17b3569d44bb5dd271e6bb`.

| Tabela | Registros |
| --- | ---: |
| `bulas` | 5895 |
| `bulas_interacoes` | 5867 |
| `processamentos_interacoes` | 5783 |
| `interacoes` | 67351 |
| `interacoes_medicamentos` | 555908 |

Foram preservados 4.512 cards simples e 286 resumos gerais simples publicados com
Smol, seus originais/evidências e os controles completos. Todos os campos mantidos
foram comparados por PK antes/depois da migração e na restauração final, não apenas
os textos associados ao nome do modelo. Pendências/falhas não viraram conclusões.

## Restauração conferida no ambiente de trabalho

Use PostgreSQL 17 ou superior e um banco **local vazio**, com `test` no nome.
Configure sua conexão em arquivo `.env` privado. O manifesto completo adjacente
`backups_banco/interamed_atual.json` está disponível localmente e ignorado pelo Git.

```powershell
$env:PG_BIN = 'C:/Program Files/PostgreSQL/17/bin'
python -m banco.operacao restaurar --env caminho/banco-test.env --manifesto backups_banco/interamed_atual.json --saida tmp/restauracao-revisao
python -m banco.operacao validar --env caminho/banco-test.env --saida tmp/validacao-revisao
```

O helper verifica o SHA, recusa destino remoto/não vazio, restaura sem donos/grants
do ambiente de origem e confere cada registro por PK. O manifesto completo contém
digests de todos os registros e fica fora do commit. Evidências resumidas
versionáveis: [VALIDACAO_SUPABASE.json](../banco/VALIDACAO_SUPABASE.json).

Para restaurar somente o dump em outra máquina, crie um banco local novo e remova
apenas seu schema `public` **vazio** antes do restore, pois o dump o recria:

```powershell
createdb -h localhost -U postgres interamed_test_restaurado
psql -h localhost -U postgres -d interamed_test_restaurado -c "DROP SCHEMA public RESTRICT"
pg_restore -h localhost -U postgres -d interamed_test_restaurado --no-owner --no-privileges --exit-on-error --single-transaction backups_banco/interamed_atual.dump
```

Esses comandos usam nome literal de banco descartável; não restaure sobre produção
ou sobre `TCC`. Configure explicitamente o papel/permissões do backend no destino.
As views usam `security_invoker=true`; não desative RLS para liberar consultas.
O dump conserva as ACLs da origem; `--no-privileges` evita importá-las no ensaio.

## Segurança e backups anteriores

TOC, SQL e dados foram revisados: somente as tabelas da aplicação e seus apoios,
sem tabelas de pacientes/contas ou objetos gerenciados de outros schemas. A busca
não encontrou a senha configurada nem os padrões de tokens/chaves/URLs com senha
verificados. O arquivo está abaixo do limite de 100 MiB do GitHub. Revise novamente
antes do commit; não inclua `.env`, tokens, SQL temporário ou manifestos privados.

Backup de segurança anterior à migração, restaurado e ensaiado separadamente:
`tmp\operacao\supabase\seguranca-pausada\interamed_seguranca_20261105T205457Z.dump`, SHA-256 `a87f5d74ceb9128aa8fe696b5f904ac0551dc93014dcd9c6680049ea62c0770a`.
Não confunda esse arquivo de recuperação com o backup final acima.

O snapshot histórico de 21/10/2026 foi preservado localmente em
`tmp/operacao/supabase/historico/interamed_atual_20261021.dump`, SHA-256
`d83b15e06ef588f348d1e7505ac9346c82503889f1b6814cd02c3c1aef29e602`. Sua cópia anterior continua também no histórico Git; nenhum histórico
foi reescrito. O candidato atual substitui apenas o arquivo de distribuição.
O backup original do checkout de publicação, igual ao HEAD antes da substituição,
também ficou guardado em `tmp/operacao/supabase/historico/interamed_atual_checkout_original.dump`.

Relatório completo: [RELATORIO_READEQUACAO.md](../banco/RELATORIO_READEQUACAO.md).
