# API de consulta das interações

API HTTP em FastAPI com Pydantic, com consultas somente de leitura sobre as tabelas gravadas
por [`identificacao_interacoes`](../../pipelines/identificacao_interacoes/README.md).

**Inclusão suspensa:** `API_INCLUSAO_MEDICAMENTOS=false` por padrão. A API não
inicia o trabalhador e os endpoints de preparação retornam 503 antes de acessar
a fila. As consultas de medicamentos cadastrados continuam funcionando. O código
experimental foi preservado; veja o [histórico e a configuração](../../pipelines/inclusao_medicamentos/README.md).

Quando habilitada, a única escrita nova é na fila de preparação de medicamentos. A API não executa
os pipelines. O [trabalhador separado](../../pipelines/inclusao_medicamentos/README.md)
é iniciado automaticamente pela API e executa a inclusão sob demanda;
`SOLICITACOES_DATABASE_URL` permite uma conexão
de escrita distinta do pool de consultas, que continua somente de leitura.

- Não carrega modelos, não abre Selenium, não executa inferência nem
  migrações. Um teste confere que iniciar a API não importa `llama_cpp`,
  `torch`, `transformers`, PyMuPDF, Selenium nem os módulos de extração.
- Quando vários registros formam um card consolidado, a vigência considera
  os originais e as evidências de todo o grupo, inclusive fora da página
  consultada. As evidências literais continuam retornando separadamente.
- Após atualizar os módulos de rastreabilidade ou a API, reinicie seu processo.
  Uma instância antiga pode rejeitar as publicações da nova versão e retornar
  os resumos originais, mesmo que a reescrita já tenha concluído.
- Conexões com `default_transaction_read_only=on` e `statement_timeout`; SQL
  sempre parametrizado; erros sem credenciais, caminhos ou detalhes internos.
- O backup de distribuição já contém as tabelas, views e colunas de reescrita
  necessárias à consulta. Os SQLs 004/005 versionados registram a etapa original
  dos resumos. Se as tabelas ou views de consulta não existirem, `/api/saude`
  informa `esquema: ausente` e as consultas respondem 503 — a API não cria
  nada. Para esta publicação, restaure o backup conforme seu guia, sem executar
  novas migrações no banco já preparado.
  A ausência da fila afeta os endpoints de preparação, sem alterar os endpoints
  GET de consulta já existentes.

```text
rotas.py         endpoints e validação de parâmetros
servico.py       estados, conclusões, agrupamento do par
repositorio.py   SQL de leitura (pool psycopg 3)
contratos.py     modelos de resposta publicados no OpenAPI
configuracao.py  variáveis de ambiente
app.py           FastAPI, CORS, tratamento de erros
```

## Instalação e execução no Windows

```cmd
cd C:\Users\Desenvolvimento\Desktop\TCC\TCCII
py -3.13 -m venv apps\api_interacoes\.venv
apps\api_interacoes\.venv\Scripts\python -m pip install -r apps\api_interacoes\requirements.txt
copy apps\api_interacoes\.env.example apps\api_interacoes\.env
```

Edite `apps\api_interacoes\.env` (`DATABASE_URL`, `API_CORS_ORIGINS`). A partir da
raiz do projeto:

```cmd
apps\api_interacoes\.venv\Scripts\python -m apps.api_interacoes
```

Documentação interativa: `http://127.0.0.1:8000/docs` (OpenAPI em
`/openapi.json`).

Com a inclusão habilitada, não é necessário outro terminal para ela. O processo da API inicia o módulo
existente `pipelines.inclusao_medicamentos` em segundo plano, usando por padrão o
Python da `.venv` de extração. Ele consulta a fila e chama os pipelines separados.
`API_WORKER_PYTHON` configura outro Python; `API_WORKER_DATABASE_URL` configura
as permissões de escrita dos pipelines na mesma base da fila. Em implantação com
trabalhador externo, defina `API_WORKER_AUTOSTART=false` na API. Dependências,
logs, retomada e configuração estão no [guia da inclusão](../../pipelines/inclusao_medicamentos/README.md).

## Endpoints

| Método e caminho | Função |
| --- | --- |
| `GET /api/saude` | banco e esquema disponíveis |
| `GET /api/medicamentos/sugestoes?q=&limite=` | autocomplete sem LLM |
| `GET /api/interacoes?medicamento=&pagina=&tamanho_pagina=` | interações de um medicamento |
| `GET /api/interacoes/par?medicamento_a=&medicamento_b=` | relações entre dois medicamentos |
| `POST /api/processamentos` | solicita inclusão por nome; 202 com UUID ou 200 se já cadastrado |
| `GET /api/processamentos/{uuid}` | andamento e alternativas encontradas |
| `POST /api/processamentos/{uuid}/selecao` | confirma uma alternativa retornada pela Anvisa |

Os três endpoints de preparação estão suspensos por padrão e respondem 503 sem
consultar ou escrever na fila. A descrição a seguir vale quando a inclusão é habilitada.
A decisão de inclusão fica na API: o POST reutiliza uma solicitação ativa ou
confere apenas a existência do nome em `bulas`. Não verifica a conclusão de todos
os pipelines de um cadastro existente. Uma nova solicitação começa diretamente
pela consulta à Anvisa com o nome pesquisado. Os controles posteriores servem à
retomada da própria solicitação, não ao reprocessamento automático da base.

### Autocomplete

- `q`: 1 a 100 caracteres, normalizado como `bulas.nome_normalizado` (caixa,
  acentos e espaços). Curingas `%` e `_` são escapados.
- Ordem: nomes que começam pelo termo, depois palavras que começam pelo termo,
  depois os que o contêm; empate por `nome_normalizado`.
- `limite`: padrão `API_AUTOCOMPLETE_LIMITE` (6), teto `API_AUTOCOMPLETE_LIMITE_MAXIMO`.
- Devolve `nome_normalizado` (identificador para as consultas) e
  `nome_exibicao` (`nome_anvisa`).

### Interações de um medicamento

- `medicamento` precisa ser um `nome_normalizado` exato (o autocomplete o
  fornece); texto não normalizado responde 422 e inexistente, 404.
- Inclui somente relações da própria bula (`DESCRITA_NA_BULA_PESQUISADA`),
  inclusive classes, alimentos e alvos sem medicamento associado.
- `resumo_geral` traz status, texto, data de geração e identificação de origem.
  Sem geração concluída, o status é `PENDENTE` e o texto é nulo.
- `total_interacoes` conta alvos agrupados; não há mais `totais_por_sentido`.
- Paginação (`pagina` ≥ 1, `tamanho_pagina` 1–100) por `id`, após o filtro de
  citações não positivas. O total da paginação conta registros de evidência.
- `resumo_card` é o resumo de apresentação, nulo enquanto não existir geração
  concluída. `resumo` permanece como fallback; `evidencia` permanece literal.
- Cada relação traz `resumo`, `evidencia` literal, `gravidade: "desconhecida"`,
  a `fonte` (registro, expediente, `sha256_pdf`, `data_publicacao_bula`) e o
  `processamento` (`data_processamento`, versão, modelo), além de até
  `API_LIMITE_MEDICAMENTOS_RELACIONADOS` medicamentos vinculados e o total.
  Na estratégia padrão da identificação (`dicionario`), o resumo também é um
  recorte literal da evidência e `processamento.modelo` é
  `"nenhum (busca por nomes do banco)"`.
- `criterio_vinculo` de cada medicamento relacionado: `PRINCIPIO_ATIVO_EXATO`,
  `PRINCIPIO_ATIVO_SEM_SAL` ("varfarina" no texto, "varfarina sódica" no
  cadastro), `COMPOSICAO_EQUIVALENCIA_DECLARADA` ou `NOME_MEDICAMENTO_EXATO`.

### Relações entre dois medicamentos

- Considera os dois sentidos (`BULA_A_CITA_B`, `BULA_B_CITA_A`).
- Os itens incluem `resumo_card` quando disponível. Não retorna resumo geral.
- Itens com a mesma origem, sentido e evidência ficam num único grupo; efeitos
  e evidências diferentes nunca são descartados.
- `alertas_nao_confirmados`: alertas sobre **classes** das duas bulas. Não
  confirmam interação com o outro medicamento (a API não classifica
  medicamentos).
- `mencoes_nao_vinculadas`: substância ou medicamento citado por uma bula cujo
  nome está contido nos nomes do outro, sem vínculo gravado (ex.: o outro
  medicamento entrou no cadastro depois da análise e os vínculos ainda não
  foram atualizados). Não é vínculo nem equivalência; apenas impede a
  conclusão negativa e pede conferência. Sal ou hidrato diferentes já geram
  vínculo (`PRINCIPIO_ATIVO_SEM_SAL`).

### Estados e conclusões

`analise_bula.estado`: `CONCLUIDO_COM_RELACOES`, `CONCLUIDO_SEM_RELACOES`,
`REVISAO_NECESSARIA`, `EM_PROCESSAMENTO` (advisory lock ativo),
`NAO_PROCESSADO`, `DESATUALIZADO` (só há análise de uma versão anterior do
texto ou da bula, não exibida), `RETIRADO` (a análise da versão atual foi
retirada explicitamente após erro reconhecido; nada é exibido e o histórico é
preservado), `FALHA`, `FONTE_INCOERENTE`,
`SEM_TOPICO_INTERACOES`, `EXTRACAO_INDISPONIVEL`, `SEM_BULA`.

| `interacao_identificada` | Quando | Mensagem |
| --- | --- | --- |
| `true` | há relação nas fontes analisadas | "Interação identificada nas fontes analisadas." |
| `false` | análise concluída (sem revisão pendente) e nada encontrado | "Não foi identificada interação nas fontes analisadas." |
| `null` | pendente, desatualizado, falha, revisão ou menção não vinculada | explica por que não há conclusão |

Só processamentos `CONCLUIDO_*` vigentes (compatíveis com a fonte e não
retirados) fornecem relações. Um resultado em revisão não é publicado como
conclusão: sem versão concluída anterior, a bula aparece como
`REVISAO_NECESSARIA` e entra em `bulas_em_revisao`; com versão concluída
anterior compatível, esta continua sendo a exibida. Uma falha posterior também
não retira a versão concluída.

A ausência de registros nunca vira conclusão negativa, e a mensagem negativa
não afirma inexistência. `cobertura` informa quantas bulas elegíveis foram
analisadas; enquanto o corpus não estiver completo e sem revisões,
`lista_exaustiva` é `false` e a mensagem diz que a lista não é exaustiva.

## Exemplos

Respostas reais do piloto (banco `TCC`, 2 de outubro de 2026), resumidas:

```http
GET /api/medicamentos/sugestoes?q=Carba
```

```json
{"termo": "carba", "sugestoes": [
  {"nome_normalizado": "carbaglu", "nome_exibicao": "Carbaglu"},
  {"nome_normalizado": "carbamazepina", "nome_exibicao": "CARBAMAZEPINA"},
  {"nome_normalizado": "dacarbazina", "nome_exibicao": "dacarbazina"},
  {"nome_normalizado": "dietilcarbamazina citrato", "nome_exibicao": "DIETILCARBAMAZINA CITRATO"},
  {"nome_normalizado": "oxcarbazepina", "nome_exibicao": "OXCARBAZEPINA"},
  {"nome_normalizado": "teucarba", "nome_exibicao": "TEUCARBA"}]}
```

```http
GET /api/interacoes?medicamento=carbamazepina&tamanho_pagina=2
```

```json
{
  "medicamento": {"nome_normalizado": "carbamazepina", "nome_exibicao": "CARBAMAZEPINA"},
  "analise_bula": {"estado": "NAO_PROCESSADO", "mensagem": "A bula ainda não foi analisada.",
                   "em_processamento": false, "processamento": null, "fonte": null},
  "interacao_identificada": true,
  "mensagem": "Interação identificada nas fontes analisadas. 2 de 5764 bulas elegíveis analisadas (1 com revisão pendente); a lista não é exaustiva.",
  "cobertura": {"bulas_elegiveis": 5764, "bulas_analisadas": 2, "bulas_em_revisao": 1,
                "corpus_completo": false, "lista_exaustiva": false, "observacao": "..."},
  "relacoes": [{
    "id": 2, "sentido": "DESCRITA_EM_OUTRA_BULA",
    "alvo": "carbamazepina", "tipo_alvo": "PRINCIPIO_ATIVO",
    "resumo": "Anticonvulsivantes, como a carbamazepina, fenobarbital, fenitoína e a pirimidona, são antagonistas da biotina.",
    "evidencia": "Anticonvulsivantes, como a carbamazepina, fenobarbital, fenitoína e a pirimidona, são antagonistas da biotina, os quais podem levar a uma menor\nabsorção da biotina e, consequente, à redução dos níveis séricos desta vitamina quando administrados ao mesmo tempo.",
    "gravidade": "desconhecida",
    "fonte": {"medicamento": {"nome_normalizado": "untral", "nome_exibicao": "Untral"},
              "numero_registro": "105730483", "expediente": "0193542251",
              "sha256_pdf": "e67b8988…", "data_publicacao_bula": "2025-02-11T15:45:56-03:00", "url_pdf": null},
    "processamento": {"id": 1, "estado": "CONCLUIDO_COM_RELACOES",
                      "data_processamento": "2026-10-02T18:13:06-03:00", "versao_processamento": "…", "modelo": "…"},
    "medicamentos_relacionados": [
      {"nome_normalizado": "carbamazepina", "nome_exibicao": "CARBAMAZEPINA", "criterio_vinculo": "PRINCIPIO_ATIVO_EXATO"},
      {"nome_normalizado": "tegretard", "nome_exibicao": "TEGRETARD", "criterio_vinculo": "PRINCIPIO_ATIVO_EXATO"}],
    "total_medicamentos_relacionados": 4}],
  "paginacao": {"pagina": 1, "tamanho_pagina": 2, "total": 1, "total_paginas": 1}
}
```

```http
GET /api/interacoes/par?medicamento_a=ecasil-81&medicamento_b=marevan
```

```json
{
  "interacao_identificada": null,
  "mensagem": "Análise sem conclusão: uma bula cita pelo nome uma substância ou medicamento contido nos nomes do outro, sem vínculo gravado (por exemplo, cadastro atualizado depois da análise). Confira as menções não vinculadas. Há alertas sobre classes de medicamentos que não confirmam interação específica com o outro medicamento.",
  "cobertura_completa": false,
  "evidencias": [],
  "alertas_nao_confirmados": [{"sentido": "BULA_A_CITA_B", "itens": [{"alvo": "betabloqueadores", "tipo_alvo": "CLASSE_FARMACOLOGICA"}]}],
  "mencoes_nao_vinculadas": [{"sentido": "BULA_A_CITA_B", "evidencia": "O ácido acetilsalicílico pode deslocar a ligação da varfarina às proteínas plasmáticas, …",
                              "itens": [{"alvo": "varfarina", "tipo_alvo": "PRINCIPIO_ATIVO", "criterio_vinculo": null}]}]
}
```

O Ecasil‑81 cita "varfarina"; o Marevan tem "varfarina sódica". Como a
correspondência é exata, não há vínculo — e, por isso mesmo, a API não conclui
ausência de interação. Erros de interpretação:

```http
GET /api/interacoes?medicamento=Carbamazepina   → 422 {"erro": "parametro_invalido", "mensagem": "medicamento deve ser o nome_normalizado devolvido pelo autocomplete.", "detalhes": []}
GET /api/interacoes?medicamento=nao%20existe   → 404 {"erro": "medicamento_nao_encontrado", "mensagem": "Medicamento não encontrado: nao existe.", "detalhes": []}
```

## Integração futura com o frontend

O frontend (`apps/frontend/`, Next.js com `output: "export"`) é estático: o
navegador chamará a API diretamente, por isso a origem dele precisa estar em
`API_CORS_ORIGINS` (`http://localhost:3000` no desenvolvimento). Nenhum
arquivo do frontend foi alterado nesta etapa. Mapeamento sugerido para os tipos
de `apps/frontend/lib/interacoes-data.ts`:

| Frontend | API |
| --- | --- |
| lista de sugestões (`MED_LIST`, 6 itens) | `GET /api/medicamentos/sugestoes` → `nome_exibicao`, guardando `nome_normalizado` |
| `findDrug(q)` → `DrugRecord` | `GET /api/interacoes?medicamento=<nome_normalizado>` |
| `DrugRecord.name` | `medicamento.nome_exibicao` |
| `Interaction.drug` | `alvo` (na bula pesquisada) ou `fonte.medicamento.nome_exibicao` (em outra bula) |
| `Interaction.severity` | `gravidade` — sempre `"desconhecida"`; não copiar as gravidades de demonstração |
| `Interaction.summary` | `resumo` |
| `evidence.excerpt` | `evidencia` (literal) |
| `evidence.updated` | `fonte.data_publicacao_bula` (publicação); `processamento.data_processamento` é outra data |
| `evidence.url` | `fonte.url_pdf` — hoje sempre `null`; o botão atual abre a **busca** do Bulário e deve continuar rotulado como busca |
| `filterByPair` | `GET /api/interacoes/par`, exibindo `evidencias`, `alertas_nao_confirmados` e `mencoes_nao_vinculadas` em blocos distintos |
| estado vazio | usar `interacao_identificada` e `mensagem`; `null` não é "sem interação" |

`url_pdf` é nulo porque `bulas.id_bula_profissional` guarda um token temporário
do Bulário (com expiração), não um identificador permanente do PDF.

## Configuração (`.env`)

| Variável | Padrão | Função |
| --- | --- | --- |
| `DATABASE_URL` | — | Banco `TCC`. |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Endereço do uvicorn. |
| `API_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Origens permitidas, separadas por vírgula; `*` é recusado. |
| `API_AUTOCOMPLETE_LIMITE` | `6` | Sugestões padrão. |
| `API_AUTOCOMPLETE_LIMITE_MAXIMO` | `20` | Teto do parâmetro `limite`. |
| `API_TAMANHO_PAGINA` | `20` | Página padrão. |
| `API_LIMITE_MEDICAMENTOS_RELACIONADOS` | `20` | Vinculados listados por relação. |
| `API_POOL_MINIMO` / `_MAXIMO` | `1` / `4` | Pool psycopg. |
| `API_TEMPO_LIMITE_CONSULTA_MS` | `5000` | `statement_timeout`. |
| `API_CACHE_COBERTURA_SEGUNDOS` | `60` | Cache do resumo de cobertura. |
| `API_WORKER_AUTOSTART` | `true` | Inicia o trabalhador em processo separado. |
| `API_WORKER_PYTHON` | `.venv` de extração, se existir | Python com as dependências dos pipelines. |
| `API_WORKER_DATABASE_URL` | `.env` do trabalhador, depois fila/API | Conexão dos pipelines, na mesma base da fila. |

## Testes

### Resumos simplificados persistidos

As colunas de reescrita, correspondentes à etapa 007, já existem no banco de
publicação e no backup distribuído. Esta atualização não cria nem altera
migrações. `resumo_card_simples` e `resumo_geral.texto_simples`
são campos opcionais na resposta, separados dos textos originais. Só são
retornados quando o controle de publicação corresponde ao hash das entradas
atuais, ao texto salvo e à versão do prompt/validação. Alterar o resumo original,
a evidência, o processamento ou a versão da fonte invalida a simplificação.

A conexão permanece somente leitura. Nenhuma requisição executa inferência,
importa o runtime do LLM ou modifica a base. A geração é uma etapa offline:
[instruções da reescrita](../../pipelines/resumos_interacoes/README.md#reescrita-em-linguagem-simples-segunda-etapa).

### Executar

```cmd
cd C:\Users\Desenvolvimento\Desktop\TCC\TCCII
apps\api_interacoes\.venv\Scripts\python -m unittest discover -s apps\api_interacoes\tests -t . -v
```

`test_api.py` usa um repositório simulado (contratos, estados, validação,
paginação, par, CORS, erros sem detalhes internos, OpenAPI) e confere em um
subprocesso que a API inicia sem bibliotecas de inferência.
`test_postgres_api.py` roda contra um banco isolado
(`IDENTIFICACAO_TEST_DATABASE_URL`, ver o README da identificação): alvos sem
vínculo preservados, par nos dois sentidos, alerta de classe separado, menção
de sal diferente e sua resolução por `--atualizar-vinculos`, pendente versus
concluído sem relações, `EM_PROCESSAMENTO`, fonte alterada, paginação estável,
autocomplete com acentos e curingas, e conexão somente leitura.
