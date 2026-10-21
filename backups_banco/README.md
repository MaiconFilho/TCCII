# Backup atualizado do banco InteraMed

O arquivo [`interamed_atual.dump`](interamed_atual.dump) contém o esquema e os
dados do banco `TCC` usados pelos módulos publicados nesta branch.

- **Snapshot:** 21/10/2026, às 15h28min05s (America/Sao_Paulo).
- **Formato:** arquivo custom do PostgreSQL, com compressão nível 9.
- **Ferramenta:** `pg_dump` 18.6.
- **Tamanho:** 61.122.010 bytes, aproximadamente 58,3 MiB.
- **Restauração:** conferida com `pg_restore` 18.6 em um banco isolado, incluindo
  criação do esquema, índices e restrições. As contagens restauradas coincidiram
  com as contagens da origem no mesmo snapshot.

O snapshot é consistente mesmo que o processamento de resumos continue após a
geração do arquivo. Ele não inclui alterações feitas depois do horário informado.

## Conteúdo

| Tabela | Registros |
| --- | ---: |
| `bulas` | 5.895 |
| `bulas_interacoes` | 5.867 |
| `bulas_interacoes_embeddings` | 17.143 |
| `processamentos_interacoes` | 5.783 |
| `interacoes` | 67.351 |
| `interacoes_medicamentos` | 555.908 |

No snapshot, 59.338 interações têm `resumo_card` preenchido e 3.937 processamentos
têm `resumo_geral` preenchido. Isso descreve o conteúdo salvo, sem afirmar que a
geração terminou para todos os medicamentos nem que os textos foram validados
clinicamente.

A tabela experimental `solicitacoes_medicamentos` foi excluída do backup porque
a funcionalidade de inclusão permanece somente na máquina de desenvolvimento.
Os dados das seis tabelas acima foram preservados integralmente, incluindo
histórico, evidências, vínculos, vetores e resumos.

O arquivo não contém os PDFs, modelos, ambientes Python ou arquivos `.env`.
Os caminhos de arquivos presentes nos registros são mantidos como metadados.
Propriedades de dono e permissões de usuários não são exportadas.

## Integridade

SHA-256:

```text
d83b15e06ef588f348d1e7505ac9346c82503889f1b6814cd02c3c1aef29e602
```

Na raiz do projeto, confira no PowerShell:

```powershell
Get-FileHash .\backups_banco\interamed_atual.dump -Algorithm SHA256
```

## Restaurar

Use PostgreSQL 18 com a extensão `pgvector` disponível no servidor. O exemplo
cria outro banco e não substitui um banco existente. Os comandos abaixo supõem
que os executáveis do PostgreSQL estejam no `PATH`.

```powershell
createdb -h localhost -U postgres interamed_restaurado
pg_restore -h localhost -U postgres -d interamed_restaurado --no-owner --no-privileges --exit-on-error --single-transaction .\backups_banco\interamed_atual.dump
```

O mesmo arquivo pode ser restaurado pela opção **Restore** do pgAdmin, em um
banco novo. Depois, configure o `DATABASE_URL` local para apontar para ele.

Somente este backup de distribuição e este README são versionados nesta pasta.
Backups de testes, diagnósticos e arquivos temporários permanecem locais.
