# API InteraMed — consulta 2.0.0

FastAPI/Pydantic sobre os resultados persistidos do [modelo aprovado](../../banco/README.md).
A API usa pool com `default_transaction_read_only=on`, timeout e SQL parametrizado.
Não executa DDL, coleta, download, inferência ou escrita em fila, nem inicia trabalhadores.
A inclusão automática legada não integra a base fixa; `API_INCLUSAO_MEDICAMENTOS=true`
é recusado explicitamente em vez de reativar uma sexta tabela.

## Banco e execução

Prepare a instalação nova com `python -m banco.operacao inicializar` ou migre uma
cópia validada da base existente conforme o guia do banco. Os SQLs históricos
001–007 não são reaplicados no modelo final. `/api/saude` verifica os 60 campos,
tipos, views e versão; startup não tenta reparar schema.

```powershell
python -m pip install -r apps/api_interacoes/requirements.txt
# Configurar DATABASE_URL e API_CORS_ORIGINS em apps/api_interacoes/.env
python -m apps.api_interacoes
```

A configuração remota da migração pode ficar em outro arquivo `.env`, preservando
os `.env` locais. Nunca coloque credenciais do banco no frontend. Configure CORS
com origens explícitas, sem `*`. Na produção informada:
`API_CORS_ORIGINS=https://interamed-web.vercel.app`.

Durante a troca coordenada, `API_MANUTENCAO=true` devolve 503 com
`Retry-After: 60`, sem abrir nem consultar o banco e mantendo CORS. O padrão é
`false`. Essa opção não interrompe lotes externos de escrita: pause-os antes do
backup definitivo e da migração. Reabra a API apenas após a validação do schema.

## Contrato

| Método/caminho | Parâmetros |
| --- | --- |
| `GET /api/saude` | — |
| `GET /api/medicamentos/sugestoes` | `q`, `limite` |
| `GET /api/interacoes` | `medicamento`, `pagina`, `tamanho_pagina` |
| `GET /api/interacoes/par` | `medicamento_a`, `medicamento_b` |

Os identificadores vêm das sugestões. Nome ausente retorna 404; parâmetro inválido,
422; indisponibilidade de consulta, 503. Consulte `/docs` e `/openapi.json`.
Não existem mais endpoints `/api/processamentos` da inclusão experimental.

A consulta individual mantém alvos sem vínculo. A consulta de pares considera os
dois sentidos e separa evidências vinculadas, alertas de classes e menções não
vinculadas. Não expande uma classe para medicamentos automaticamente.
Paginação e contagem filtram as mesmas relações, antes de construir a página.

Vigência usa a maior conclusão por ID, não invalidada e com extração concluída.
Falha/revisão posterior não retira uma conclusão válida anterior. Pendência,
revisão, falha e retirada não são ausência de interação. Não existe estado de
fonte desatualizada baseado na atualização automática de bulas.

`fonte` contém medicamento, `numero_registro` e `expediente`, lidos de `bulas`.
Não há cópias de metadados de fonte no processamento, hashes de PDF, publicação,
URL de PDF fictícia ou `fundamento_vinculo`. `data_processamento` continua sendo
a data do processamento. O alvo e critério gravados sustentam a apresentação.

`resumo`, `resumo_card` e `resumo_geral.texto` são independentes das versões simples.
Controles legados são pré-validados pela migração sem alteração dos JSONBs.
Publicações novas usam a entrada dos campos mantidos, versão 37; triggers invalidam
uma versão simples se suas entradas mudarem. O hash do texto e o status da
publicação continuam obrigatórios. Nenhum resumo é regenerado para atender uma
requisição. Detalhes dessa compatibilidade estão no guia do banco.

## Verificação

```powershell
python -m unittest discover -s apps/api_interacoes/tests -t .
```

`IDENTIFICACAO_TEST_DATABASE_URL` habilita os testes com PostgreSQL real isolado.
Não use produção nem o banco `TCC`. A operação remota e o backup real exigem conexão
com credencial válida; saúde da API publicada sozinha não comprova a migração.
