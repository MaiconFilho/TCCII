# InteraMed — resumos de interações medicamentosas

Esta branch implementa a geração de resumos a partir das interações já
identificadas nas bulas. A etapa usa um modelo local para explicar cada interação
e reunir os resultados em um resumo geral do medicamento pesquisado. Os textos
são gravados no PostgreSQL; a API e a interface apenas consultam o que está pronto.

O resumo é uma apresentação do conteúdo encontrado, não uma validação clínica.
O trecho original e sua fonte continuam disponíveis em **Ver evidência**.

## Como funciona

1. **Seleção:** lê a identificação vigente da própria bula. Por padrão, processa
   somente medicamentos com resumo geral ou algum resumo de card ainda vazio.
2. **Contexto:** recupera a evidência literal e, quando a bula separa a lista de
   medicamentos da descrição do efeito, reúne as duas partes do mesmo bloco.
   Não usa o efeito de outro bloco nem de outra bula para completar o texto.
3. **Cards:** trechos curtos, sem lista e com um único medicamento podem ser
   apresentados literalmente. Nos demais casos, o LLM escreve uma explicação.
   Alvos da mesma oração e do mesmo efeito podem reaproveitar um texto validado,
   substituindo o nome, respeitadas as restrições descritas no guia do módulo.
4. **Validação:** verifica formato, nomes, negações, contexto, direção do efeito
   e afirmações sem apoio na evidência. Uma resposta rejeitada pode ter uma nova
   tentativa com a indicação do problema; isso não garante fidelidade clínica.
5. **Gravação:** cada card validado é salvo em uma transação curta. Cards prontos
   são preservados na retomada e não exigem repetir a geração de toda a bula.
6. **Resumo geral:** os cards preenchidos são agrupados por efeito: aumento,
   redução ou efeito nos dois sentidos/sem direção definida. Cada grupo origina
   um parágrafo. O resultado pode ter de um a três parágrafos, conforme os dados,
   sempre com o medicamento pesquisado como origem explícita.

As regras Python preparam o contexto, selecionam os registros e validam as
respostas. O LLM redige os textos dos casos que exigem explicação e os parágrafos
do geral. Se a geração de um parágrafo falhar após as tentativas previstas, o
serviço pode compô-lo com os textos dos cards daquele grupo.

## Modelo e execução

O modelo é **Qwen2.5-1.5B-Instruct**, formato **GGUF Q4_K_M**, executado em CPU
por `llama-cpp-python`. O provedor roda em processo separado, com timeout por
chamada. A configuração reaproveita o ambiente e o cache da identificação.

Os padrões da CLI são contexto de 4.096 tokens, saída de até 192 tokens por card,
320 tokens por parágrafo do geral, temperatura zero e timeout de 90 segundos.
Esses valores não modificam a configuração das etapas anteriores.

## Persistência

| Local | Informação |
| --- | --- |
| `interacoes.resumo_card` | Texto de apresentação do registro de interação. |
| `interacoes.resumo_status`, `resumo_modelo`, `resumo_tempo_segundos`, `resumo_erro`, `resumo_atualizado_em` | Controle da geração por card. |
| `processamentos_interacoes.resumo_geral` | Resumo geral associado à identificação da bula. |
| Campos `resumo_*` de `processamentos_interacoes` | Status, modelo, data, erro e tempos da geração geral. |

Não são necessárias novas tabelas para os resumos. Os campos e controles estão
nos SQLs aditivos [004](pipelines/identificacao_interacoes/migracoes/004_resumos_interacoes.sql)
e [005](pipelines/identificacao_interacoes/migracoes/005_controle_resumos_cards.sql).
Eles podem ser executados diretamente no pgAdmin, caso os campos ainda não
existam. A API não cria campos nem executa migrações automaticamente.

## Executar pela raiz do projeto

Configure `DATABASE_URL` em `pipelines/identificacao_interacoes/.env` ou em
`pipelines/resumos_interacoes/.env`. O exemplo de configuração fica no próprio
módulo. Use um ambiente com suas [dependências](pipelines/resumos_interacoes/requirements.txt).

```powershell
# Um medicamento pendente
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --nome-normalizado marevan

# Todos os medicamentos com resumos pendentes
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos

# Refazer também os resumos já preenchidos
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos --reprocessar

# Conferir inconsistências detectadas, sem gravar nem carregar modelo
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos --corrigir-inconsistentes --listar

# Corrigir somente as inconsistências detectadas
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos --corrigir-inconsistentes
```

`--todos` preserva os textos preenchidos. `--reprocessar` força sua substituição
e não mantém versões anteriores do resumo no mesmo registro. O modo
`--corrigir-inconsistentes` não pode ser combinado com `--reprocessar`.
Não execute dois lotes de resumos simultaneamente.

## Organização da etapa

| Arquivo | Responsabilidade |
| --- | --- |
| `pipelines/resumos_interacoes/__main__.py` | Argumentos, configuração e processamento do lote. |
| `repositorio.py` | Seleção, leitura das fontes e gravação dos resumos. |
| `servico.py` | Coordenação dos cards, reaproveitamento e resumo geral. |
| `prompts.py` | Instruções fornecidas ao modelo. |
| `validacao.py` | Verificação das respostas antes da gravação. |
| `provedor.py` | Processo do modelo, timeout e medição de memória. |
| `benchmark.py` | Execução de amostras sem gravar resumos. |
| `tests/` | Testes simulados e integração com banco isolado. |

O [guia do módulo](pipelines/resumos_interacoes/README.md) detalha os critérios,
a retomada, as correções de contexto, os comandos e os limites conhecidos.

## Apresentação dos resultados

A consulta individual mostra o resumo geral e o botão **Ver detalhes**, que abre
os cards e sua paginação. Cada card usa `resumo_card` quando disponível e mantém
o recorte anterior como alternativa. **Ver evidência** exibe o trecho literal.
Na consulta entre medicamentos, as evidências dos dois sentidos continuam
agrupadas por par. Nenhuma consulta HTTP executa o modelo.

## Verificar

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m unittest discover -s pipelines/resumos_interacoes/tests -t .
```

Os testes com PostgreSQL exigem `IDENTIFICACAO_TEST_DATABASE_URL` apontando para
um banco isolado cujo nome contenha `test`. O workflow valida os resumos com
provedor simulado, além das verificações da API e do frontend.

## Backup do banco

O [backup atualizado](backups_banco/README.md) contém o esquema e os dados dos
módulos publicados, incluindo os resumos já gravados. O guia informa a data,
o hash de integridade, o escopo e os comandos de restauração.
