# Identificação estruturada das interações

Este módulo lê os tópicos de interações já gravados em
`bulas_interacoes.trecho_interacoes`, identifica as relações descritas, liga
os alvos aos medicamentos do cadastro e grava tudo no PostgreSQL com controle
de versão. A API só lê o que foi gravado.

Há duas formas de identificar, que gravam nas mesmas tabelas:

| Estratégia | Como identifica | Escopo | Base inteira nesta máquina |
| --- | --- | --- | --- |
| `dicionario` (**padrão**) | procura no tópico os nomes que existem no banco (princípios ativos, também sem sal, e nomes de medicamentos), com regras para ausência, exceção e a própria origem | medicamento × medicamento do banco | cerca de 2 min, sem modelo |
| `atual`, `duas_etapas` | LLM local (Qwen2.5-1.5B, CPU) com validação | também classes, alimentos e alvos fora do banco | semanas |

```text
bulas_interacoes (CONCLUIDO, com texto)
→ dicionario: frases e itens → nomes do banco → regras → recorte literal
  (ou LLM: frases numeradas → janelas → Qwen → JSON → validação)
→ correspondência com bulas.principios_ativos / nomes (exata ou sem sal)
→ processamentos_interacoes + interacoes + interacoes_medicamentos
```

**Não faz parte desta etapa:** scraping, leitura de PDF, OCR, localização da
seção, geração de embeddings, busca semântica, classificação de gravidade e
classificação dos medicamentos em classes. Os textos extraídos não são
alterados. A consulta HTTP fica em [`api_interacoes`](../api_interacoes/README.md).

## Estratégia padrão: nomes do banco (`dicionario`)

Implementada em [`dicionario.py`](dicionario.py), sem modelo e determinística.
Para cada bula:

1. **Trechos.** O tópico é dividido em frases e itens de lista do texto
   original (linha em branco, marcador de lista, ponto ou ponto e vírgula
   seguido de maiúscula).
2. **Nomes do banco.** A lista de busca sai do cadastro: cada princípio ativo
   de `bulas.principios_ativos`, a sua forma sem sal ou hidrato ("varfarina
   sódica" → "varfarina", "cloridrato de amiodarona" → "amiodarona") e
   `nome_normalizado`/`nome_anvisa`. Em cada trecho vale o nome mais longo
   ("ácido acetilsalicílico" antes de "ácido"); siglas de três letras só em
   maiúsculas ("AAS").
3. **Descartes**, todos registrados como rejeição no relatório:
   - a própria origem, também por outro nome ou sigla da mesma substância e por
     enantiômero/racemato ("cetirizina" na bula da levocetirizina);
   - oração que nega a interação ("não foram observadas interações", "não
     alterou a farmacocinética", "não havia interações clinicamente
     relevantes"), verificada numa janela em volta do nome;
   - nome citado como exceção ("exceto a hidrocortisona", "mas não pantoprazol");
   - minerais e substâncias do organismo citados como medida ou mecanismo
     ("níveis séricos de potássio", "diuréticos poupadores de potássio",
     "recaptação de dopamina");
   - avisos de excipientes ("Atenção: contém lactose");
   - termos de [`termos_ignorados.txt`](termos_ignorados.txt): marcas que são
     palavras comuns ("limiar", "nesta"), parâmetros de exame ("glicose",
     "ureia"), diluentes, excipientes e erros do cadastro ("revestido",
     "RDC 60/12"). A lista foi revisada pelos nomes mais citados na base
     inteira, nunca por bula, e pode ser editada;
   - nome sem nenhum medicamento do banco além da origem.
4. **Relação.** Uma por nome citado em cada trecho distinto, até três trechos
   por nome. A evidência é um recorte literal: o trecho inteiro até 1.200
   caracteres; acima disso, do começo da frase (onde costuma estar o efeito:
   "A atividade da varfarina pode ser potencializada por …") até pouco depois
   do nome, ou uma janela de 400 caracteres quando o nome está longe do
   começo, como em tabelas. O **resumo também é literal**: o que vem depois de
   "Nome:" num item estruturado, ou a própria evidência. Nenhum texto é gerado.
5. **Vínculos** pela mesma correspondência da estratégia com LLM (ver
   Correspondência com medicamentos). Status `CONCLUIDO_COM_RELACOES` ou
   `CONCLUIDO_SEM_RELACOES`; esta estratégia não gera revisão pendente.

A versão (`dicionario/1#<hash>|banco=<hash>|validacao/5`) muda quando muda o
código, a lista de termos ignorados ou o conjunto de nomes do banco. Por isso,
depois de coletar bulas novas basta rodar `--todos` de novo: só o que ainda
não tem a versão atual é processado, e os nomes novos passam a ser
encontrados também nas bulas antigas.

**Escopo deliberado:** só medicamento × medicamento que existe no banco.
Classes ("anticoagulantes orais", "salicilatos"), alimentos e substâncias sem
bula coletada não geram relação. Exemplo real: o Marevan cita "salicilatos" e
a Aspirina cita "anticoagulantes orais"; como nenhum cita o outro pelo nome,
o par Marevan × Aspirina sai como "não identificada nas fontes analisadas".

### Resultados (5 de outubro de 2026)

Base inteira copiada para o banco isolado de testes e processada pela CLI
(5.764 tópicos, i7-12700K, sem modelo):

| Etapa | Tempo | Resultado |
| --- | --- | --- |
| `--simular --todos` | 28 s | 4.509 bulas com relações, 1.255 sem |
| `--todos` (gravação) | 82 s e 127 s (duas execuções) | 66.758 relações, 554.869 vínculos, 398.399 pares bula → medicamento |
| nova execução `--todos` | ~1 s | nada a fazer (mesma versão) |

API sobre esses dados: cerca de 0,3 s por consulta de par e 1 s para listar as
1.614 relações ligadas ao Marevan (paginadas).

Qualidade nas referências locais da avaliação, contando só alvos
`PRINCIPIO_ATIVO`/`MEDICAMENTO` que existem no banco:

| Conjunto | Esperadas | Acertos | Falsos positivos | Precisão | Revocação |
| --- | --- | --- | --- | --- | --- |
| ajuste (aspirina, ecasil-81, marevan, untral, a saude da mulher) | 81 | 77 | 1 | 0,987 | 0,951 |
| avaliação (ailuk, diad, digevita, vigadexa, vocety) | 42 | 40 | 2 | 0,952 | 0,952 |

Ressalvas: as referências foram rotuladas por IA e ainda não foram revisadas
por uma pessoa; duas regras gerais ("não havia interações" e
enantiômero/racemato) foram criadas depois de olhar erros do vocety, que é do
conjunto de avaliação, então ele deixou de ser totalmente independente; das
200 relações medicamentosas esperadas nas referências, 77 citam substâncias
que não existem no banco e ficam fora do escopo (contando-as, a revocação cai
para 0,575 no ajuste e 0,606 na avaliação; a precisão não muda). Numa leitura de 90 relações
sorteadas da base inteira, cerca de 9 em 10 descreviam interação de fato; os
erros restantes eram, sobretudo, ausências escritas de forma não prevista e
trechos de tabelas clínicas coladas no tópico pela extração (apixabana).

Os conjuntos de referência e relatórios da avaliação permanecem fora do repositório.

## Fonte dos dados

Só entram registros com `bulas_interacoes.status_extracao = 'CONCLUIDO'` e
texto preenchido. Para cada um são lidos de `bulas`: `nome_normalizado`
(origem), `nome_anvisa` (exibição), `principios_ativos`, `numero_registro`,
`expediente`, `sha256_pdf`, `id_produto` e `data_publicacao_anvisa`.

Antes de processar, a CLI confere que registro e expediente de
`bulas_interacoes` são os mesmos de `bulas` e que o SHA-256 do texto calculado
no PostgreSQL é igual ao calculado no Python; divergência vira
`FONTE_INCOERENTE` e nada é processado. O medicamento de origem sempre vem do
registro selecionado, nunca do modelo. Os chunks vetoriais não são usados: o
texto literal é o `trecho_interacoes` completo.

## Modelo: reutilização e avaliação

Esta seção e as duas seguintes (tipos de alvo, validação) e a de janelas
valem para as estratégias com LLM (`atual`, `duas_etapas`); a estratégia
padrão não carrega modelo.

O provedor é o mesmo da extração (`extracao_interacoes.modelo_llm.LlamaCppProvider`,
uma instância, CPU, semáforo de concorrência 1), importado só pela CLI e com
limites próprios: contexto de 8.192 tokens e **até 3.072 tokens de saída**
(a extração usa 256 para localizar seções). O GGUF já baixado em
`extracao_interacoes/modelos_hf` é reaproveitado.

Localizar uma seção não prova que o modelo interpreta relações, então ele foi
avaliado nesta tarefa:

1. **Formato livre de lista** (`{"relacoes": [...]}` com a evidência copiada):
   com temperatura 0, o modelo não encerrava a lista e repetiu o mesmo item até
   o limite de tokens em todos os testes — com a gramática do esquema completo,
   com a gramática genérica de JSON e com `repeat_penalty` 1,1. O tópico do
   Untral, de 388 caracteres, consumiu 1.024 tokens de saída sem terminar.
2. **Formato adotado: um item por frase.** O texto vai em frases numeradas e a
   gramática (`prefixItems` + `const`) exige exatamente uma entrada por frase,
   na ordem. A resposta sempre termina, e a evidência é a própria frase,
   recortada do texto original pelo identificador — sem cópia nem paráfrase.
3. **Exemplos de classe.** O modelo registrava "anticonvulsivantes" e omitia
   "carbamazepina, fenobarbital, fenitoína" citados na mesma frase. Quando uma
   classe é aceita e a frase tem marcador de exemplos ("como", "por exemplo",
   ":"), uma segunda pergunta curta ao modelo lista os integrantes citados; cada
   nome passa pela mesma validação contra a frase (`IDENTIFICACAO_EXEMPLOS_DE_CLASSE`).
   A pergunta é feita uma vez por frase, com até 60 nomes: no Marevan o modelo
   rotulou 41 fármacos da lista como "classe", e uma pergunta por classe custou
   43 chamadas e 31 minutos; com uma pergunta e teto de 20 nomes, o fim da
   lista se perdeu (cetoconazol, paracetamol…). Atingir o teto marca revisão.
   A resposta também traz o tipo de cada nome (princípio ativo, medicamento ou
   outra substância), o que recupera fármacos que o modelo tinha rotulado como
   classe e permite vinculá-los.
4. **Releitura de frases-lista.** No Marevan, a frase que lista ~50 fármacos
   que potencializam a varfarina voltou sem nenhum alvo. Uma frase com 200
   caracteres ou mais, ao menos quatro vírgulas e nenhum alvo é relida sozinha
   pelo modelo (`IDENTIFICACAO_RELEITURA_LISTAS`); "nenhum alvo" quer dizer
   nenhuma relação aceita, porque propostas deslocadas também deixam a frase
   sem análise real. O gatilho só decide reler;
   quem identifica os alvos é o modelo, e cada alvo passa pela validação.
5. **Correção enxuta.** A rodada de correção reaproveita o prompt da janela e
   lista só os alvos rejeitados, citando o trecho omitido (por exemplo, "não
   devendo ser administrado…"). Reenviar a resposta anterior fez o prompt
   passar do contexto em janelas densas.

**Testado e descartado — âncora de alinhamento.** O modelo às vezes escreve na
entrada de uma frase o conteúdo da seguinte (resumos trocados no Ecasil‑81;
"creme vaginal de miconazol" atribuído à frase-lista do Marevan). Um campo
`inicio` com as três primeiras palavras copiadas de cada frase permitiria
reatribuir os alvos, mas reduziu a revocação nos casos de referência de 88,9%
para 66,7% e, no Marevan, de 20 para 9 relações. Os deslocamentos continuam
sendo barrados pela validação (`ALVO_AUSENTE_NA_EVIDENCIA`,
`RESUMO_SEM_APOIO_NA_EVIDENCIA`) e corrigidos quando possível.

Resultado com 8 casos de referência rotulados à mão
(material de avaliação local não versionado), modelo real, mesma máquina:

| Métrica | Sem etapa de exemplos | **Versão adotada** (exemplos, releitura, validação 3) | Com âncora (descartada) |
| --- | --- | --- | --- |
| Revocação dos alvos esperados | 55,6% (10/18) | **88,9% (16/18)** | 66,7% (12/18) |
| Tipo correto entre os encontrados | 60% | 75% | 91,7% |
| Condições (dose, negação, via, prazo) preservadas no resumo | 100% | 100% | 100% |
| Alvos proibidos aceitos (ausências, mera menção, origem) | 0 | 0 | 0 |
| Tempo por caso | 8–22 s | 16–24 s | 11–33 s |

Erros que permanecem: grupo alimentar ("alimentos ricos em vitamina K") e
exame laboratorial não detectados; confusões de tipo (álcool como `ALIMENTO`,
heparina como `CLASSE_FARMACOLOGICA`, "anticoagulantes" como
`PRINCIPIO_ATIVO`). Tipo errado afeta vínculos: heparina tratada como classe
não é ligada aos produtos de heparina. Os resultados do piloto em bulas reais
estão em [Execução validada](#execução-validada). Oito casos sintéticos indicam
limitações; não medem a qualidade na base inteira.

O modelo é configurável (`IDENTIFICACAO_HF_MODEL_REPO`/`_FILE`); um modelo
maior tende a errar menos, mas nesta máquina havia menos de 1 GB de RAM livre
durante os testes, o que inviabilizou avaliar um GGUF de 3B ou mais.

## Tipos de alvo

| `tipo_alvo` | Uso | Vínculo com o cadastro |
| --- | --- | --- |
| `PRINCIPIO_ATIVO` | substância de medicamento (varfarina, cetoconazol) | por princípio ativo exato e equivalência declarada na composição |
| `MEDICAMENTO` | nome comercial | por nome exato |
| `CLASSE_FARMACOLOGICA` | classe ou grupo de medicamentos (anticoagulantes, IMAO) | nunca expandida |
| `ALIMENTO` | alimento ou bebida específica (suco de toranja) | não |
| `GRUPO_ALIMENTAR` | grupo de alimentos/bebidas (saladas verdes, bebidas alcoólicas) | não |
| `OUTRA_SUBSTANCIA` | não medicamento nem alimento (álcool, tabaco, planta pelo nome popular) | não |
| `EXAME_LABORATORIAL` | exame cujo resultado pode ser alterado | não |
| `OUTRO` | só quando nenhum tipo serve | não |

Um tipo inexistente devolvido pelo modelo vira rejeição `ITEM_FORA_DO_ESQUEMA`
e entra na rodada de correção; **nunca é convertido em `OUTRO`**.

## Validação das relações

O modelo propõe; o código decide. Cada alvo é conferido contra a frase
indicada (que já é a passagem literal do trecho):

| Rejeição | Regra |
| --- | --- |
| `ALVO_E_ORIGEM` | o alvo é o próprio medicamento ou um de seus princípios ativos, inclusive sem o sal ("varfarina" na bula de "varfarina sódica"); essa inclusão de palavras só descarta, nunca vincula |
| `ALVO_GENERICO` | o alvo começa por termo de efeito, dose ou fisiologia ("drogas", "testes", "mucosa…", "função renal", "doses…"); não vale para `EXAME_LABORATORIAL` |
| `ALVO_AUSENTE_NA_EVIDENCIA` | o alvo não está escrito na frase (comparação por termo inteiro, sem substring) |
| `RELACAO_NAO_POSITIVA` | a oração do alvo declara ausência: "não foram realizados estudos", "não foram observadas", "não alterou", "não parece afetar", "não se sabe", "não há relatos" — salvo recomendação explícita (evitar, cautela, monitorar…) |
| `RESUMO_OMITE_NEGACAO` | a oração tem negação/exceção e o resumo não |
| `RESUMO_OMITE_QUANTIDADE` | dose, quantidade, frequência ou duração da oração ausentes do resumo |
| `RESUMO_OMITE_VIA` | via ou apresentação da oração ausente do resumo |
| `RESUMO_INVENTA_GRAVIDADE` | grave, leve, moderado, fatal etc. no resumo sem estar na evidência |
| `RESUMO_INVENTA_EFEITO` | aumento ou redução no resumo sem estar na evidência ("afetem" ≠ "aumenta") |
| `RESUMO_SEM_APOIO_NA_EVIDENCIA` | menos de 50% das palavras de conteúdo do resumo aparecem na frase (resumo copiado de outra frase) |
| `LISTA_DE_ALVOS_NO_LIMITE` | a frase atingiu 40 alvos; a lista pode estar cortada |

`ALVO_E_ORIGEM`, `ALVO_GENERICO`, `ALVO_INVALIDO` e `RELACAO_NAO_POSITIVA`
apenas descartam ruído do modelo. As demais podem esconder uma relação
verdadeira mal formulada e exigem revisão se não forem corrigidas. A versão
das regras (`VERSAO_VALIDACAO`) entra na versão do processamento.

Rejeições corrigíveis voltam ao modelo em uma rodada de correção
(`IDENTIFICACAO_TENTATIVAS_CORRECAO`). O que não for corrigido deixa o
processamento como `REVISAO_NECESSARIA`: as relações aceitas são gravadas, mas
o resultado é marcado como possivelmente incompleto. Essas regras são
conservadoras e não substituem revisão humana; uma citação literal não prova
que o resumo é fiel.

## Janelas, cobertura e recursos

- O texto é dividido em unidades (frases; linhas quando um bloco sem pontuação
  passa de 1.500 caracteres). Abreviações ("p. ex.", "vide 3.") e números
  decimais não quebram frases; títulos viram contexto.
- As janelas têm até `IDENTIFICACAO_JANELA_TOKENS` (1.000) tokens de unidades
  inteiras e repetem cerca de 150 tokens da janela anterior. O título da
  subseção em que a janela começa vai junto como referência.
- A cobertura é conferida: nenhuma unidade fica fora das janelas.
- Prompt + saída reservada nunca passam do contexto (o llama.cpp reduziria a
  saída em silêncio). Resposta com `finish_reason = length`, JSON incompleto ou
  sem uma entrada por frase divide a janela ao meio (até 4 vezes). Janela
  indivisível que ainda falha torna o processamento `FALHA`, sem gravar
  nenhuma relação: processamento parcial nunca é concluído.
- Repetições da sobreposição são consolidadas por origem, alvo normalizado,
  tipo e evidência; o resumo não entra na identidade.
- O modelo é carregado uma vez por processo, a inferência é serial e a memória
  é medida (RSS antes, pico e depois) com o `MedidorMemoria` da extração, que
  recusa nova inferência acima de `IDENTIFICACAO_LLM_MAX_PROCESS_MEMORY_MB`.

## Correspondência com medicamentos

Comparação por igualdade após a mesma normalização que gerou
`bulas.nome_normalizado` (`scraping_anvisa/anvisa_scraper/normalizacao.py`:
caixa, acentos e espaços). Nunca por substring.

- `PRINCIPIO_ATIVO`: cada entrada de `bulas.principios_ativos` separada por
  `;` é uma substância inteira (`PRINCIPIO_ATIVO_EXATO`); "omeprazol" não liga
  a "esomeprazol". Duas formas literais da própria entrada também valem: a
  substância antes de uma anotação final entre parênteses ("amoxicilina (sob a
  forma tri-hidratada)", "omeprazol (pellets)") e o sufixo "base"
  ("epinefrina base").
- `PRINCIPIO_ATIVO_SEM_SAL` (migração 003): as duas formas sem sal ou hidrato
  também são comparadas, por igualdade da substância inteira — "varfarina"
  liga a "varfarina sódica", "amiodarona" a "cloridrato de amiodarona",
  "esomeprazol" a "esomeprazol magnésico tri-hidratado". Um sal nunca é
  reduzido ao ânion ("cloreto de sódio" não vira "cloreto"). Os sais
  reconhecidos estão em `resolucao.py` (`SAL_ANTES`, `SAL_DEPOIS`).
- Composição, como complemento: só uma equivalência **declarada** e presa a um
  princípio ativo, por exemplo "cloridrato de paroxetina 22,214 mg (equivalente
  a 20 mg de paroxetina base)". Excipientes, veículos e "não contém"/"isento
  de"/"livre de" são ignorados; a passagem fica em `fundamento_vinculo`.
- `MEDICAMENTO`: `nome_normalizado` ou `nome_anvisa` exatos.
- Classes, alimentos e demais tipos são gravados sem vínculo e sem expansão.
- Todos os medicamentos com correspondência válida são vinculados, exceto a
  própria origem.

Quando um medicamento casa por mais de um critério, vale o mais direto (exato,
depois equivalência declarada, depois sem sal). Nomes que só aparecem em parte
("prazol") continuam sem vínculo; a API mostra menções sem vínculo e não
conclui ausência de interação por causa delas.

## Tabelas

Criadas somente por [`migracoes/001_criar_interacoes.sql`](migracoes/001_criar_interacoes.sql)
e ajustadas por [`migracoes/002_vigencia_aprovada.sql`](migracoes/002_vigencia_aprovada.sql)
e [`migracoes/003_vinculo_sem_sal.sql`](migracoes/003_vinculo_sem_sal.sql)
(aditivas, idempotentes, com comentários; a 003 só troca a regra CHECK de
`criterio_vinculo` para aceitar `PRINCIPIO_ATIVO_SEM_SAL`). Nenhuma tabela
anterior à etapa é alterada.

```text
processamentos_interacoes   controle e histórico; uma linha por execução
  id, medicamento_origem → bulas, status, versao_processamento, modelo,
  hash_trecho, caracteres_trecho, numero_registro, expediente, sha256_pdf,
  id_produto, data_publicacao_anvisa, janelas, relações, rejeições, vínculos,
  tempos, pico de memória, detalhe, iniciado_em, concluido_em

interacoes                  todas as relações, com ou sem medicamento associado
  id, medicamento_origem → bulas, alvo, tipo_alvo, resumo, evidencia,
  processamento_id → processamentos_interacoes   (vínculo técnico de versão)

interacoes_medicamentos     zero, um ou vários medicamentos por interação
  PK (interacao_id → interacoes, medicamento_relacionado → bulas),
  criterio_vinculo, fundamento_vinculo, vinculado_em, invalidado_em

processamentos_interacoes_vigentes (view, redefinida pela 002)
  último processamento CONCLUIDO_* não retirado e compatível com o texto e a
  bula atuais; REVISAO_NECESSARIA não entra (antes da 002 entrava)

processamentos_interacoes_registrados (view, 002)
  último resultado não retirado (concluído ou em revisão) da fonte atual; só
  para a retomada em lote não repetir a mesma versão; a API não lê
```

A 002 também acrescenta `invalidado_em` e `motivo_invalidacao` em
`processamentos_interacoes`, preenchidos apenas pela retirada explícita
(`--retirar-processamento`). Vigente significa "concluído operacionalmente e
compatível com a fonte", nunca aprovação clínica.

Todas as chaves estrangeiras são `ON DELETE RESTRICT`. Não há
`chave_deduplicacao` nem coluna com o nome fixo "Anvisa"; a identificação da
bula vem de `medicamento_origem` e da cópia guardada no processamento.

### Controle operacional e histórico

| Situação | Como aparece |
| --- | --- |
| Não processado | nenhuma linha em `processamentos_interacoes` para a fonte atual |
| Concluído com relações | `CONCLUIDO_COM_RELACOES` |
| Concluído sem relações | `CONCLUIDO_SEM_RELACOES` |
| Revisão necessária | `REVISAO_NECESSARIA` (relações aceitas gravadas para revisão; não vigente; a versão concluída anterior, se houver, continua vigente) |
| Falha | `FALHA` (nenhuma relação gravada; o resultado anterior compatível continua vigente) |
| Retirado | `invalidado_em` preenchido por decisão explícita, com motivo; sai da vigência e continua no histórico |

- A versão do texto é o SHA-256 de `trecho_interacoes`; a da bula é registro,
  expediente e `sha256_pdf`; a do processamento é o prompt (com hash das
  instruções e esquemas), o modelo e os parâmetros.
- Se o texto ou a bula mudarem, o resultado antigo sai da view vigente e
  continua no banco, ligado à bula efetivamente usada; a API não o apresenta
  como derivado da versão nova.
- O reprocessamento cria uma nova versão; nada é apagado.
- Uma execução posterior com falha ou em revisão não invalida automaticamente
  a versão concluída anterior; só a retirada explícita a tira da vigência.
- Conclusão de todas as janelas é conclusão operacional, não garantia de que o
  modelo encontrou todas as relações.

### Concorrência e retomada

- Durante a inferência, o processo mantém `pg_try_advisory_lock` do
  medicamento; outro processo recebe `EM_ANDAMENTO_OUTRO_PROCESSO`. Se o
  processo morrer, o PostgreSQL libera o bloqueio ao fechar a sessão.
- A gravação é uma transação curta com `pg_advisory_xact_lock`: revalida a
  fonte (`FOR SHARE`), confere se a mesma versão já foi gravada e insere
  controle, relações e vínculos de uma vez. Qualquer erro desfaz tudo.
- O lote seleciona quem não tem resultado registrado (concluído ou em
  revisão) para a fonte atual, nunca processados primeiro: interromper e
  executar de novo retoma sem duplicar, e um item em revisão não é repetido a
  cada lote. Falhas voltam a ser tentadas.
- `--atualizar-vinculos` recalcula vínculos com o cadastro atual sem o modelo:
  cria os novos, reativa os que voltaram a valer e marca `invalidado_em` nos
  que deixaram de valer — sem DELETE.

## Instalação no Windows

O ambiente da extração já tem todas as dependências:

```cmd
cd C:\Users\Desenvolvimento\Desktop\TCC\TCCII
extracao_interacoes\.venv\Scripts\activate
copy identificacao_interacoes\.env.example identificacao_interacoes\.env
```

Edite `identificacao_interacoes\.env` com a sua `DATABASE_URL`. Em outro
computador, crie um ambiente e instale
`identificacao_interacoes\requirements.txt`.

**Todos os comandos rodam a partir da raiz do projeto**, como módulos
(`python -m ...`), sem alterar `sys.path`.

### Migração

```cmd
python -m identificacao_interacoes.aplicar_migracoes --listar
python -m identificacao_interacoes.aplicar_migracoes
```

O aplicador recusa arquivos com `DROP`, `TRUNCATE` ou `DELETE`; a única
exceção aceita é trocar uma regra CHECK por outra de mesmo nome na mesma
instrução (`DROP CONSTRAINT IF EXISTS ck_x, ADD CONSTRAINT ck_x CHECK …`),
que não apaga dados — é o que a 003 faz. Também é possível executar os
arquivos SQL, em ordem, no pgAdmin, conectado ao banco `TCC`. A CLI e a API
apenas conferem se o esquema existe; nenhuma delas migra sozinha.

## Comandos

```cmd
python -m identificacao_interacoes --simular --todos                 base inteira sem gravar nada (relatório)
python -m identificacao_interacoes --todos                           base inteira, estratégia padrão (sem modelo)
python -m identificacao_interacoes --nome-normalizado marevan
python -m identificacao_interacoes --limite 10
python -m identificacao_interacoes --estrategia atual --nome-normalizado marevan   com a LLM local
python -m identificacao_interacoes --reprocessar --nome-normalizado marevan
python -m identificacao_interacoes --atualizar-vinculos --todos      sem carregar o modelo
python -m identificacao_interacoes --retirar-processamento 17 --motivo "relação incorreta confirmada"
```

| Situação | Comportamento |
| --- | --- |
| Lote (`--limite`, `--todos`) | pula quem já tem resultado registrado (concluído ou em revisão) **da versão atual** |
| `--simular` | identifica e resolve vínculos, grava só o relatório; não bloqueia nem altera o banco |
| `--nome-normalizado X` | sempre seleciona; `IGNORADO_JA_PROCESSADO` se a mesma versão já cobre a fonte, inclusive em revisão |
| `--reprocessar` | nova versão; a anterior vira histórico |
| versão do processamento diferente | processa de novo pelo nome; a versão anterior continua no histórico |

A versão do processamento é conhecida antes de carregar o modelo: se todos os
selecionados já têm resultado registrado da mesma versão, o GGUF nem é carregado.
A retirada (`--retirar-processamento`) também não carrega o modelo e exige
`--motivo`.

Para evitar a consulta ao Hugging Face Hub a cada execução (o GGUF já está no
cache), defina `set HF_HUB_OFFLINE=1`.

### Relatórios e logs

Cada execução grava em `relatorios/` (ignorado pelo Git) um CSV por
medicamento — status, janelas, chamadas, tokens, relações, duplicatas
consolidadas, rejeições, vínculos, tempos (inferência, banco, total) e memória
— e um `_detalhes.jsonl` com cada relação, seus vínculos, cada rejeição e os
avisos, para revisão humana. O log fica em `logs/identificacao_interacoes.log`.
Um relatório com o mesmo nome nunca é sobrescrito.

O CSV separa propostas brutas (só a leitura principal de cada janela),
aceitas antes da consolidação, duplicatas consolidadas, relações finais,
resumos gerados/aceitos/rejeitados e chamadas adicionais. O JSONL traz, por
chamada: janela, etapa, tentativa, finalidade (extração, correção, resumo),
motivo da chamada adicional, tokens de entrada e saída, tempo total, tempo de
leitura do prompt e de geração informados pelo llama.cpp (`null` quando o
backend não informa), tokens reaproveitados do cache, motivo de encerramento
(`length` = truncada), propostas, RSS do processo e memória disponível do
sistema; e a agregação por etapa/finalidade (`chamadas_por_finalidade`). A
"taxa efetiva" (tokens de saída / tempo total) inclui leitura do prompt e
amostragem com gramática; não é velocidade de geração.

## Configuração (`.env`)

| Variável | Padrão | Função |
| --- | --- | --- |
| `DATABASE_URL` | — | Banco `TCC`. |
| `IDENTIFICACAO_HF_MODEL_REPO` / `_FILE` | Qwen2.5-1.5B Q4_K_M | GGUF usado. |
| `IDENTIFICACAO_MODELOS_DIR` | `extracao_interacoes/modelos_hf` | Cache do GGUF. |
| `IDENTIFICACAO_LLM_CONTEXT_SIZE` | `8192` | Contexto do llama.cpp. |
| `IDENTIFICACAO_LLM_MAX_OUTPUT_TOKENS` | `3072` | Orçamento de saída por chamada. |
| `IDENTIFICACAO_JANELA_TOKENS` / `_SOBREPOSICAO` | `1000` / `150` | Janelas. |
| `IDENTIFICACAO_LLM_THREADS` | `0` | `0` = heurística da extração (máx. 4). |
| `IDENTIFICACAO_LLM_MAX_PROCESS_MEMORY_MB` | `5500` | Limite conferido antes de cada inferência. |
| `IDENTIFICACAO_LLM_INFERENCE_CONCURRENCY` | `1` | Precisa ser 1. |
| `IDENTIFICACAO_TENTATIVAS_CORRECAO` | `1` | Rodadas de correção. |
| `IDENTIFICACAO_MAX_DIVISOES_JANELA` | `4` | Divisões por janela. |
| `IDENTIFICACAO_EXEMPLOS_DE_CLASSE` | `true` | Pergunta pelos integrantes citados de uma classe. |
| `IDENTIFICACAO_RELEITURA_LISTAS` | `true` | Relê sozinha a frase-lista longa que voltou sem alvos. |
| `IDENTIFICACAO_ESTRATEGIA` | `dicionario` | `dicionario` (sem modelo), `atual` ou `duas_etapas` (LLM; experimental). `--estrategia` na CLI tem precedência. As variáveis de LLM acima só valem para estas duas. |
| `IDENTIFICACAO_LOTE_RESUMOS` | `6` | Relações por chamada de resumo/correção em `duas_etapas` (1 a 12). |
| `IDENTIFICACAO_TEST_DATABASE_URL` | — | Só testes: banco isolado. |

## Testes

Os testes unitários verificam a estratégia de dicionário, a segmentação, a
validação das evidências, os vínculos, a retomada e as migrações. Execute a
partir da raiz do projeto:

```cmd
python -m unittest discover -s identificacao_interacoes/tests -t . -v
```

Os testes com PostgreSQL real só são habilitados quando
`IDENTIFICACAO_TEST_DATABASE_URL` aponta para um banco isolado cujo nome contém
`test`. Sem essa variável, eles são pulados e não acessam o banco do projeto.
O workflow do GitHub executa a suíte isolada sem carregar o modelo local.

## Execução validada (estratégia com LLM)

Os resultados da estratégia padrão estão na seção dela, acima.

Piloto real no banco `TCC`, em 2 de outubro de 2026, nesta máquina (i7‑12700K,
16 GB de RAM com menos de 1 GB livre durante os testes, CPU,
`IDENTIFICACAO_LLM_THREADS=6`, `HF_HUB_OFFLINE=1`). Foram processados apenas
cinco medicamentos, sempre com `--nome-normalizado` ou `--limite 1`; a base
inteira não foi executada. Cada linha é uma versão do processamento; as
anteriores continuam no histórico.

Resultados vigentes (versão definitiva: prompt 3, releitura 2, validação 3;
exemplos 3, exceto a aspirina, processada com exemplos 2 — teto de 20 nomes):

| Medicamento (caracteres) | Processamento | Status | Relações | Vínculos | Chamadas | Tempo |
| --- | --- | --- | --- | --- | --- | --- |
| untral (388) | 14 | CONCLUIDO_COM_RELACOES | 5 | 16 | 2 | 43 s |
| a saude da mulher (200) | 15 | CONCLUIDO_COM_RELACOES | 2 | 0 | 2 | 42 s |
| ecasil-81 (2.951) | 13 | REVISAO_NECESSARIA | 21 | 20 | 11 | 250 s |
| marevan (2.971) | 18 | REVISAO_NECESSARIA | 103 | 129 | 4 | 613 s |
| aspirina (10.442, 4 janelas) | 17 | REVISAO_NECESSARIA | 76 | 83 | 24 | 2.184 s |

Pico de memória: 2,03 a 2,06 GB de RSS por processo (o GGUF mapeado conta no
RSS).

Histórico no mesmo banco (13 processamentos anteriores preservados) mostra a
evolução das versões no Marevan: 19 relações/18 vínculos (prompt 2) → 20/18
(validação 2) → 9/9 (âncora, descartada) → 103/234 em 31 min (exemplos por
classe) → 79/50 (exemplos uma vez por frase, teto 20) → **103/129 em 10 min**
(teto 60). Na aspirina: 45/58 (prompt 2) → 76/83. "A saude da mulher" passou
de `REVISAO_NECESSARIA` com 0 relações para `CONCLUIDO_COM_RELACOES` quando a
correção passou a citar a restrição omitida. As 207 evidências vigentes são
recortes literais do `trecho_interacoes` atual (conferido por SQL).

Também verificado no banco real: a reexecução de um medicamento já
processado devolveu `IGNORADO_JA_PROCESSADO` sem carregar o modelo;
`--atualizar-vinculos --todos` percorreu os cinco processamentos vigentes sem
carregar o modelo (todos os vínculos mantidos, pois o cadastro não mudou); a
interrupção forçada de uma execução da aspirina não deixou linha parcial nem
bloqueio, e o resultado anterior continuou vigente; o pico de memória ficou
entre 2,0 e 2,1 GB de RSS por processo (o GGUF mapeado conta no RSS); as
contagens de `bulas`, `bulas_interacoes` e `bulas_interacoes_embeddings` não
mudaram com a migração.

Leitura humana dos resultados (detalhes nos `_detalhes.jsonl`):

- **Acertos**: Untral → anticonvulsivantes, carbamazepina, fenobarbital,
  fenitoína e pirimidona, com 16 vínculos exatos (carbamazepina → 4 produtos,
  fenitoína → 7, fenobarbital → 5); aspirina → ISRS e os seis exemplos citados
  (citalopram, escitalopram, fluoxetina, fluvoxamina, paroxetina, sertralina),
  clopidogrel e pemetrexede; Marevan → colestiramina, rifampicina,
  carbamazepina, griseofulvina e, com a releitura, a lista de ~60
  potencializadores (cetoconazol → 28 produtos, paracetamol → 78). Ausências
  ("não foram encontradas interações clinicamente significativas") foram
  barradas em todas as bulas.
- **Erros do modelo que a validação barrou**: alvos que são a própria origem
  (ácido acetilsalicílico na aspirina, biotina no Untral), resumos copiados de
  outra frase, direção de efeito inventada ("aumenta" para "afetem") e
  negações omitidas ("não devendo ser administrado").
- **Erros que passaram**: tipos trocados (fármaco rotulado como classe, o que
  impede vínculo; "dengue" como classe farmacológica); o mesmo alvo gravado
  com dois tipos; resumos de dois alvos da mesma frase trocados entre si
  (ambos citados na frase, então a checagem lexical aceita); frases de
  mecanismo convertidas em alvo antes do filtro de termos genéricos ("fluxo
  sanguíneo renal", "mucosa gastrintestinal").
- **Custo**: de 40 s a 35 min por bula. A releitura e os exemplos aumentam a
  revocação e as chamadas; a base inteira, nesta máquina, seria uma execução de
  vários dias.

## Limitações

- Estratégia padrão: só nomes que existem no banco. Classes ("anticoagulantes
  orais"), alimentos e substâncias sem bula coletada ficam de fora; um par
  clássico como varfarina × ácido acetilsalicílico só aparece se uma das bulas
  citar a outra substância pelo nome.
- Estratégia padrão: a regra de ausência é por palavras; uma negação escrita
  de forma não prevista pode virar relação, e uma frase que nega e afirma ao
  mesmo tempo ("embora nenhuma interação farmacocinética tenha sido
  observada, uma interação farmacodinâmica pode ocorrer") pode ser descartada.
  O resumo literal nem sempre contém o efeito (tabelas, frases com o efeito
  antes da lista).
- Tópicos com texto de outra seção colado pela extração (tabelas clínicas na
  apixabana, várias bulas no mesmo PDF) geram relações a partir desse texto;
  a correção é na extração.
- Qwen2.5-1.5B tem revocação e tipagem limitadas (ver avaliação). O código
  rejeita o que consegue verificar, mas não detecta uma relação que o modelo
  simplesmente não propôs.
- A evidência é a frase inteira. Uma relação que depende de duas frases
  ("Nesses casos, monitore…") só traz a frase que cita o alvo.
- Fora as formas sem sal, nomes diferentes não são equiparados; vínculos
  dependem da grafia do cadastro.
- `REVISAO_NECESSARIA` sinaliza listas possivelmente incompletas; a revisão
  humana dos itens está nos relatórios JSONL, sem tela própria.
- Em CPU, cada janela leva de 15 s a mais de 1 min nesta máquina; a base
  inteira (5.764 tópicos) é uma execução de dias e deve ser feita em lotes.
