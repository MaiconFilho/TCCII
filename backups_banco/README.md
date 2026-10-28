# Backup atualizado do banco InteraMed

O arquivo [`interamed_atual.dump`](interamed_atual.dump) contém o esquema e os
dados do banco `TCC` usados pelos módulos publicados nesta branch.

- **Snapshot:** 27/10/2026, às 16h56min28s (America/Sao_Paulo), conforme
  `Archive created at` de `pg_restore -l`.
- **Formato:** arquivo custom do PostgreSQL, com compressão gzip nível 9.
- **Ferramenta:** `pg_dump` 18.6.
- **Tamanho:** 21.450.506 bytes, aproximadamente 20,46 MiB.
- **Restauração:** conferida com `pg_restore` 18.6 em banco PostgreSQL local,
  temporário e isolado, sem erros na criação do esquema, índices e restrições.
  As contagens abaixo foram consultadas nesse banco, e a consulta pela API
  retornou o resumo geral simplificado de Abevmy.

O snapshot é consistente mesmo que o processamento de resumos continue após a
geração do arquivo. Ele não inclui alterações feitas depois do horário informado.

## Conteúdo

| Tabela | Registros |
| --- | ---: |
| `bulas` | 5.895 |
| `bulas_interacoes` | 5.867 |
| `processamentos_interacoes` | 5.783 |
| `interacoes` | 67.351 |
| `interacoes_medicamentos` | 555.908 |

No snapshot, 66.145 interações têm `resumo_card` preenchido e 4.432 processamentos
têm `resumo_geral` preenchido. Os campos em linguagem simples estão preenchidos
em 613 registros de `resumo_card_simples` e 30 de `resumo_geral_simples`.
Essas contagens consideram textos não vazios e descrevem o conteúdo salvo, sem
afirmar que a geração terminou nem que os textos foram validados clinicamente.

A tabela `bulas_interacoes_embeddings` e a extensão pgvector não fazem parte
deste backup, porque os vetores não participam da consulta atual. A tabela
experimental `solicitacoes_medicamentos` e seus objetos associados continuam
fora do arquivo; a inclusão permanece desativada na publicação.

Os dados das cinco tabelas acima foram preservados, incluindo histórico,
evidências, vínculos e resumos. O arquivo não contém os PDFs, modelos,
ambientes Python ou arquivos `.env`. Os caminhos de arquivos presentes nos
registros são mantidos como metadados. Propriedades de dono e permissões de
usuários não são exportadas.

## Integridade

SHA-256:

```text
fc8296e8a7dc260822c61fad9ff723359b63567a8c71832c72caac1b996db49b
```

Na raiz do projeto, confira no PowerShell:

```powershell
Get-FileHash .\backups_banco\interamed_atual.dump -Algorithm SHA256
```

## Restaurar

Use PostgreSQL 18. A extensão pgvector não é necessária para restaurar este
backup nem para as consultas atuais da API. O exemplo cria outro banco e não
substitui um banco existente. Os comandos abaixo supõem que os executáveis do
PostgreSQL estejam no `PATH`.

```powershell
createdb -h localhost -U postgres interamed_restaurado
pg_restore -h localhost -U postgres -d interamed_restaurado --no-owner --no-privileges --exit-on-error --single-transaction .\backups_banco\interamed_atual.dump
```

O mesmo arquivo pode ser restaurado pela opção **Restore** do pgAdmin, em um
banco novo. Depois, configure o `DATABASE_URL` local para apontar para ele.

Somente este backup de distribuição e este README são versionados nesta pasta.
Backups de testes, diagnósticos e arquivos temporários permanecem locais.
