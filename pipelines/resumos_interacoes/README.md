# Resumos de interações

Etapa offline com o modelo local já configurado na identificação. A API apenas
lê os textos prontos, sem executar LLM.

## Onde ficam os textos

- `interacoes.resumo_card`: resumo curto de cada registro de interação.
- `processamentos_interacoes.resumo_geral`: texto principal de 2 a 3 parágrafos,
  ligado à identificação daquela bula.
- No processamento: `resumo_status`, `resumo_gerado_em`, `resumo_modelo`,
  `resumo_erro`, `resumo_tempo_inferencia_segundos` e `resumo_tempo_total_segundos`.
- Em cada interação: `resumo_status`, `resumo_modelo`, `resumo_tempo_segundos`,
  `resumo_erro` e `resumo_atualizado_em` (SQL aditivo `005_controle_resumos_cards.sql`).

Não usa tabelas extras. O SQL aditivo está salvo em
`../identificacao_interacoes/migracoes/004_resumos_interacoes.sql` e pode ser
executado diretamente no pgAdmin. Não é preciso executar o gerenciador de
migrações para rodar o processo; basta que os campos existam.
O script não apaga tabelas ou dados da proposta anterior, caso tenham sido criados.

## Como seleciona pendências

1. Lê as interações elegíveis da identificação vigente da própria bula.
2. Se o geral e todos os cards estiverem preenchidos, ignora a bula.
3. Se faltar só o geral, reutiliza os cards e gera apenas o geral.
4. Se faltar algum card, gera somente os cards ausentes e preserva o geral preenchido.
5. Nulo, texto vazio, espaços, tabs e quebras de linha contam como pendentes.

Trocar modelo ou prompt não torna um resumo preenchido pendente.
Somente `--reprocessar` substitui explicitamente os textos existentes.
Essa substituição não mantém histórico de versões do resumo no mesmo registro;
o histórico da identificação permanece inalterado.

### Revisão dos cards pendentes (23/10/2026)

O prompt simplificado dos cards, sem exemplos com medicamentos fictícios
(A, B, C e D), está disponível somente no benchmark com `--prompt-simplificado`.
O processamento normal mantém o prompt anterior: no piloto o modelo de 1,5B
ainda produziu uma interpretação incorreta apesar de passar pelos validadores.
Não foi promovido a padrão. O prompt do resumo geral também permanece anterior.
As versões `resumos-card-original` e `resumos-card-piloto-2026-10-23` são
registradas nos relatórios CSV, JSONL, benchmark e log, sem novos campos no banco.

As validações aceitam variações tipográficas de aspas quando o conteúdo citado
existe na evidência. Diferenciam a ausência de uma alteração específica (voz
ativa ou passiva, como “não afetou” / “não foi afetada”) de alegações gerais de
ausência de interação ou segurança. Uma negação específica não libera essas
conclusões gerais. Essas verificações continuam sendo heurísticas, não uma
validação clínica automática.

Somente cards sem resumo podem recuperar a seção `nome do alvo: ...` no texto
original da mesma bula. O recorte precisa ocorrer uma única vez, sobrepor a seção
e ter limites identificáveis. A recuperação não junta partes distantes nem usa
texto de outra bula. Em sucesso, a evidência literal corrigida é gravada com os
mesmos IDs; em erro, a evidência e os textos anteriores são preservados.

Nomes com símbolos de tabela ou parênteses incompletos, tabelas com colunas
ambíguas, listas sem efeito e menções explícitas ao alvo como componente da
própria bula geram `RevisaoDaEvidencia`, com motivo no relatório. Permanecem
pendentes; não são apagados nem convertidos em afirmações de ausência de
interação. Conferir a bula é necessário antes de corrigir esses registros.

O benchmark é somente leitura e deve incluir casos pendentes e controles que
já funcionavam. Passar pelos validadores não comprova fidelidade: confira as
respostas com a evidência antes de executar um lote com gravação. A versão do
prompt, sozinha, não seleciona registros prontos para reprocessamento.

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --benchmark-modelo --nome-normalizado clabat --alvo-benchmark tadalafila --limite-interacoes 1 --prompt-simplificado
```

Não combine `--prompt-simplificado` com processamento com gravação: a CLI recusa
essa combinação. Modelos maiores podem ser comparados no mesmo benchmark por
`--modelo-repo` e `--modelo-arquivo`, sem trocar a configuração padrão.

O bloqueio por medicamento impede execução simultânea com a identificação.
A fonte é conferida antes da gravação. Cada card validado é gravado imediatamente
em uma transação curta. Falhas são registradas por card e o processamento continua.
O geral só é gerado depois que todos os cards estão preenchidos. Falha crítica
resulta em `FALHA`; execução limitada com pendências resulta em `PARCIAL`.
Retomar o mesmo comando aproveita os cards já gravados.

### Corrigir lista separada de seu efeito e alegações sem suporte

Algumas bulas têm blocos `Medicamentos: ... / Efeitos da interação: ...`. O
recorte antigo podia terminar na lista. A leitura agora recupera o bloco literal
completo do tópico da mesma bula, limitado pelo próximo `Medicamento(s):`. O LLM
recebe também o efeito em destaque. A nova identificação já preserva esse bloco.
Não se usa o efeito de uma lista vizinha nem se amplia um recorte ambíguo.
Se o LLM devolver somente o efeito compartilhado e omitir o alvo, um rótulo com
o nome do alvo é acrescentado apenas após confirmar que ele pertence à lista;
o efeito e o texto completo continuam sujeitos às mesmas validações.

Afirmações de interação desconhecida, falta de estudos, ausência de interação ou
segurança são recusadas quando não constam na evidência. Para esses blocos, a
validação também preserva a possibilidade (`pode`) e a via nasal, quando descritas.
Cards antigos com esses problemas não são reutilizados como moldes.

Na raiz TCCII, para conferir a seleção **sem gravar nem carregar modelo**:

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos --corrigir-inconsistentes --listar
```

O resumo geral recebe o medicamento pesquisado como origem explícita, separado
dos alvos. Cada parágrafo deve explicar as interações dessa origem e citar seu
nome. Uma descrição isolada de outro fármaco é rejeitada, com uma nova tentativa.
Se for necessário montar o parágrafo com os cards, ele também identifica a bula
de origem e passa pela validação de contexto. A relação legítima com outro
medicamento não é apagada: o texto explicita em qual bula ela foi descrita.

`--corrigir-inconsistentes` também seleciona gerais cujos parágrafos perderam esse
contexto. Quando só o geral precisa de correção, todos os cards são preservados;
apenas o geral é gerado novamente. `--listar` informa as duas situações.

Para corrigir os cards detectados e os resumos gerais fora de contexto:

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos --corrigir-inconsistentes
```

Para um piloto com gravação, substitua `--todos` por `--nome-normalizado fluidsoro`.
Não acrescente `--reprocessar`: ele é incompatível com essa seleção. Ao gravar um
card corrigido com sucesso, seu recorte de evidência também é ampliado com texto
literal da bula; os IDs e vínculos permanecem. Em falha, o registro anterior fica
preservado. O tópico completo não é alterado. Repetir o comando pula os cards
que já foram corrigidos. Essa detecção cobre os padrões descritos, não comprova
automaticamente toda possível incorreção clínica de um resumo.

Benchmark real do caso de linezolida, sem gravação:

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --benchmark-modelo --nome-normalizado fluidsoro --alvo-benchmark linezolida --limite-interacoes 1
```

## Executar na raiz TCCII

Reutiliza o ambiente da extração, seu provedor GGUF e a configuração
`pipelines/identificacao_interacoes/.env`. Um `.env` nesta pasta pode sobrescrever
a configuração, mantendo as variáveis do terminal como prioridade.

```powershell
# Piloto: uma bula com resumo pendente
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes

# Uma bula específica
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --nome-normalizado marevan

# Benchmark: evidências distintas primeiro, depois o geral com esses cards; não grava resumos
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --benchmark-modelo --nome-normalizado marevan --limite-interacoes 30

# Benchmark de outro GGUF (mesmos trechos)
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --benchmark-modelo --nome-normalizado marevan --limite-interacoes 30 --modelo-repo Qwen/Qwen2.5-0.5B-Instruct-GGUF --modelo-arquivo qwen2.5-0.5b-instruct-q4_k_m.gguf

# Gerar no máximo cinco cards pendentes da bula
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --nome-normalizado marevan --limite-interacoes 5

# Até três bulas pendentes
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --limite 3

# Todas as pendentes, somente quando você decidir ampliar
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos
```

Não acrescente `--reprocessar` para retomar pendências: ele força a substituição.
CSV (uma linha por bula), JSONL de auditoria (uma linha por card, com a origem
`literal`, `reaproveitado` ou `llm`) e logs ficam em `relatorios/` e `logs/`,
ignorados pelo Git. Registram tempo, respostas brutas, evidências, chamadas,
memória do worker, quantidade de cards gerados e traceback dos erros. Nenhum
modelo é carregado quando a seleção não encontra pendências.

### Preencher a base inteira

Em 13/10/2026 havia 4.504 bulas com interações e 66.234 cards. Pela regra de
origem, cerca de 24,5 mil cards são literais, 23,9 mil reaproveitados e 17,9 mil
escritos pelo LLM (~8 s cada), mais o geral (~50 s por bula): em torno de 3 a 4
dias de CPU nesta máquina. O processamento é retomável: cada card é gravado na
hora e a seleção pega só o que estiver vazio. Interromper com Ctrl+C perde no
máximo o card em andamento; rodar o mesmo comando de novo continua de onde parou.
Bulas com `FALHA` ficam pendentes e voltam na próxima execução, só com os cards
que faltam.

```powershell
# Em lotes de 200 bulas (repita até aparecer "Nenhuma bula ... pendente")
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --limite 200

# Ou todas as pendentes numa única execução
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos
```

Depois de corrigir o código no meio da carga, `--refazer-antes "AAAA-MM-DD HH:MM"`
(horário local) também refaz as bulas com algum card ou geral gravado antes dessa
data, além das pendentes. Use sempre a mesma data ao retomar: as bulas refeitas
ganham data nova e não voltam para a fila.

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos --refazer-antes "2026-10-13 16:30"
```

O worker local fica separado do processo que grava no banco. `--timeout 90`
limita cada chamada; ao exceder, o worker é encerrado e o próximo card pode
recarregá-lo. O terminal informa progresso a cada dez segundos. Há no máximo
uma nova tentativa para resposta rejeitada na validação; timeout não é repetido
automaticamente para o mesmo card. Os limites padrão são `--contexto 4096`,
`--tokens-card 192` e `--tokens-geral 320` (por parágrafo), temperatura zero e
CPU. Para comparar outro GGUF use `--modelo-repo` e `--modelo-arquivo`. Download
e carregamento são medidos separadamente. A RAM indicada é a do worker, não de
todo o computador.

## Modelo

Mantido o `Qwen2.5-1.5B-Instruct` Q4_K_M (GGUF, llama-cpp-python, CPU). Benchmark
de 13/10/2026 na Marevan, 10 cards (as 6 evidências distintas e 4 da lista longa)
mais o geral, com os prompts e validações atuais:

| Modelo | Cards válidos | Média por card | Geral | Carga | Pico RAM do worker |
| --- | --- | --- | --- | --- | --- |
| 0.5B | 3/10 | 5,0 s | 11,6 s | 0,6 s | 723 MB |
| 1.5B | 10/10, sem retentativa | 6,1 s | 33,5 s | 1,0 s | 1.920 MB |
| 3B | 10/10 (1 com eco do prompt, hoje barrado) | 11,2 s | 80,0 s | 2,7 s | 3.131 MB |

O 0.5B inverteu direções e papéis ou devolveu texto vazio; o 3B dobra tempo e
memória sem ganho visível. Esses números são anteriores aos cards explicativos e
ao reaproveitamento por trecho, que reduziu as chamadas por bula.

## Interface e validação

A consulta individual mostra somente as interações da própria bula, com resumo
geral e “Ver detalhes”. Cada card usa seu resumo; “Ver evidência” mantém o trecho
literal. A consulta de pares continua nos dois sentidos e não mostra resumo geral.
Sem resumo pronto, a interface preserva os detalhes e o recorte anterior.

Cada card tem uma de três origens (coluna `interacoes.resumo_modelo`):

- **Literal** (`trecho_literal`): trecho com até 400 caracteres, sem lista de
  fármacos (menos de 4 vírgulas) e com um só medicamento na frase. O card é a
  própria frase da bula, sem marcadores como • e sem LLM. Pedir ao 1.5B que a
  copiasse palavra por palavra falhava em outras bulas. Título de seção em caixa
  alta no início ("INTERAÇÕES MEDICAMENTOSAS") sai, e rótulo em linha própria
  ganha ponto ("Lítio\nAumentos..." → "Lítio. Aumentos..."); quebra no meio da
  frase, terminada em "de", "com", "e" etc., só vira espaço.
- **LLM**: nos demais, o modelo explica a interação com as próprias palavras, em
  até duas frases para leigos, citando só o medicamento do card.
- **Reaproveitado**: alvos citados na mesma oração do mesmo trecho, com o mesmo
  efeito, recebem o texto do primeiro, trocando só o nome do medicamento
  ("Amiodarona pode aumentar..." → "Paracetamol pode aumentar..."). Artigo ou
  contração antes do nome vira preposição simples ("pela amiodarona" → "por
  paracetamol").

O reaproveitamento vale para qualquer bula: as orações vêm da mesma separação
usada na identificação. Não reaproveita quando a oração traz aumento e redução
ao mesmo tempo ("podem aumentar (ex.: ciclosporina) ou diminuir (ex.:
lamotrigina)"), nem quando o texto do primeiro já cita o outro medicamento
("carbidopa com levodopa"); nesses casos o LLM escreve cada card, com o primeiro
como exemplo. Ao retomar, os cards já gravados servem de molde.

O geral é montado a partir dos cards gravados, incluindo os que já estavam
preenchidos. Cards que só diferem pelo alvo viram uma linha ("pode ser
potencializada por A, B e C"). As linhas são separadas por efeito (aumento,
redução, ambos ou sem direção) e cada grupo vira um parágrafo, gerado e validado
apenas contra as próprias linhas. Assim o geral tem de 1 a 3 parágrafos: um
medicamento cujas interações só aumentam o efeito recebe um parágrafo, sem
enchimento. Pedir o texto inteiro numa chamada fez o modelo de 1,5B enumerar as
interações, inverter quem potencializa quem e acrescentar riscos ausentes.
Parágrafo cortado pelo limite de tokens fica até a última frase completa. Se o
LLM falhar duas vezes num parágrafo, ele é montado com os textos dos cards do
grupo, para a bula não ficar sem resumo geral.

A validação confere formato e tom, não fidelidade clínica: o card é uma
explicação do LLM e a evidência literal continua em "Ver evidência". Rejeita
resposta truncada, JSON inválido, texto vazio ou longo demais, repetição, eco das
instruções (inclusive da correção: "A tentativa anterior foi rejeitada"), nome
genérico dos exemplos ("efeito de X"), aspas ausentes da bula, lista ou título no
parágrafo, certeza absoluta e gravidade ausente da bula ("grave", "fatal"); no
card, exige o nome do medicamento. Na nova tentativa, o motivo vai curto ao fim
do pedido ("Atenção: ..."); se o modelo o copiar, o filtro de eco barra. Os
exemplos do prompt usam o nome da bula, e não "X". Se a primeira resposta só foi
rejeitada por citar outro medicamento do trecho e a nova tentativa falhar, fica a
primeira — com temperatura zero, a bula falharia sempre do mesmo jeito. Todo card
e parágrafo termina pontuado. Antes de validar, o texto perde marcadores • e quebras do PDF, e
nomes com 6+ letras voltam à grafia da evidência ("mefenámico" → "mefenâmico").
Entradas acima do contexto geram erro, sem truncamento silencioso.

## Arquivos

| Arquivo | Função |
| --- | --- |
| `__main__.py` | Argumentos, ambiente, modelo, CSV e log. |
| `repositorio.py` | Seleção de campos vazios, leitura e atualização do PostgreSQL. |
| `servico.py` | Gera somente o que falta e coordena gravação. |
| `prompts.py` | Instruções do resumo geral e dos individuais. |
| `validacao.py` | Verificações das respostas. |
| `provedor.py` | Worker local, timeout real, caminho do GGUF e consumo de memória. |
| `benchmark.py` | Amostra de evidências, tempos e respostas sem gravação. |
| `tests/` | Testes simulados e integração com banco isolado. |

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m unittest discover -s pipelines/resumos_interacoes/tests -t .
```

Integração usa somente `IDENTIFICACAO_TEST_DATABASE_URL` configurada com um banco
isolado de nome contendo `test`. Não utilize o banco TCC nessa variável.

## Reescrita em linguagem simples (segunda etapa)

Esta etapa recebe os resumos já existentes e suas evidências. O primeiro modelo
Qwen continua com a função anterior. A reescrita usa **SmolLM3-3B Q4_K_M**, em CPU,
no mesmo `llama-cpp-python`, sem servidor adicional nem API de inferência paga.
O download dos pesos é feito no Hugging Face; a inferência é local.

O [modelo original](https://huggingface.co/HuggingFaceTB/SmolLM3-3B) e o
[GGUF usado](https://huggingface.co/ggml-org/SmolLM3-3B-GGUF) têm licença Apache 2.0.
O artefato de 1.915.305.312 bytes está fixado na revisão
`4965cb60b150737b68a0408c36aeefb65078f894`, arquivo `SmolLM3-Q4_K_M.gguf`.
O model card inclui português e o modo sem raciocínio `/no_think`, utilizado
na mensagem de sistema. Seu contexto de treinamento é 64k, com extensão por
YaRN descrita pelo autor; aqui o limite local padrão é 2.048 tokens por chamada.

Foram consideradas as seguintes opções:

| Opção | Português e licença | Compatibilidade e decisão local |
| --- | --- | --- |
| [Gemma 3 1B IT](https://huggingface.co/google/gemma-3-1b-it) | Família multilíngue; termos Gemma | GGUF Q4_K_M de 806 MB; rodou no runtime atual com cerca de 1,2 GiB, mas recusou reescritas corretas e acrescentou efeitos ausentes em outros testes. Contexto do 1B: 32k. |
| SmolLM3 3B | Português no model card; Apache 2.0 | GGUF de 1,92 GB; executou nos pilotos com pico de aproximadamente 3,4 GiB. Produziu reescritas fiéis e respostas inadequadas, exigindo filtros e conferência manual. É a opção experimental selecionada. |
| [Llama 3.2 3B Instruct](https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct) | Português oficial; licença Llama 3.2 | Outra opção para runtime GGUF, de porte semelhante ao Smol. Não baixada nem medida: não houve justificativa para um terceiro download. Contexto anunciado do original: 128k; quantizações podem ter limites próprios. |
| [Granite 4.0 1B](https://huggingface.co/ibm-granite/granite-4.0-1b) | Português oficial; Apache 2.0 | Q4_K_M de 1,02 GB, revisão `b27c2fe3f211b7f44e80fa620177aea371099aaa`. Ultrapassou 1,75 GiB no início da inferência; worker interrompido. Não atende ao objetivo de cerca de 1,5 GiB neste perfil. |
| [Llama 3.2 1B Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) | Português oficial; licença Llama 3.2 | Q4_K_M de 808 MB, revisão do GGUF `7d1f70022fcab2038000074bd0342e03e1d8b755`. Pico próximo de 1,45 GiB. Coube, mas omitiu qualificadores/negações; respostas barradas. Não promovido. |
| [LFM2.5 1.2B Instruct](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct) | Português não consta nos idiomas declarados; licença LFM 1.0 | Q4_K_M de 731 MB, revisão `8ed288026e23958ad9dfa92d53ed773a8eee7125`. Pico de 1,26 GiB; um card passou e outro perdeu qualificador. Teste em português insuficiente para adoção; não promovido. |

Não foi feita comparação formal de qualidade nem avaliação clínica. O piloto
limitado não autoriza afirmar que o segundo modelo é um avaliador confiável.
O modelo selecionado ainda exige revisão de amostra antes de ampliar o uso.
Os ensaios adicionais usaram Aerolin, Fluidsoro, Marevan, Clabat e Alopurinol,
em simulação. Demonstram limitações
nessa amostra, sem constituir ranking geral dos modelos. Pesos baixados ficam
no disco; só o worker ativo carrega um modelo na memória. As execuções de teste
encerram esse worker ao terminar, inclusive em falha de memória.
O ajuste de instruções na versão 8 prioriza a entrega da reescrita, com revisão
somente para divergência concreta, mantendo as verificações independentes do
modelo. O formato separa sucesso (texto preenchido e motivo vazio) de revisão
(texto vazio e motivo preenchido), evitando respostas contraditórias entre os
campos. O Qwen2.5 da primeira etapa permanece inalterado.

### Fluxo e limites da validação

1. `reescrita.py` seleciona uma bula vigente com resumo pendente de reescrita.
2. A unidade auditada reúne origem, alvo, princípios ativos, original e evidências.
   Na reescrita comum, o prompt recebe o original e os nomes de contexto;
   as evidências ficam nos controles e na conferência, sem completar o parágrafo.
   A reconstrução de um original inconsistente usa seu prompt baseado na fonte.
3. Antes da inferência, verifica o original contra a fonte. Quando encontra uma
   inconsistência, registra o motivo e pede ao mesmo segundo modelo uma nova
   redação baseada na bula, sem usar o original incorreto como referência factual.
   O original continua armazenado sem alterações.
4. O prompt em `prompts.py` (`INSTRUCOES_REESCRITA`) pede linguagem simples e
   JSON com `status`, `texto` e `motivo`. O contrato completo é esse texto do código;
   a versão conjunta do prompt e das verificações está em
   `rastreabilidade.VERSAO_REESCRITA` (`linguagem-simples/35`).
5. Validações independentes da opinião do modelo procuram alterações de direção,
   papéis em construções explícitas, incerteza, negação, restrições, doses, vias,
   condições, gravidade e orientações acrescentadas. JSON vazio, repetido,
   truncado ou fora do formato é recusado. O motivo volta ao modelo para uma
   correção, com até três tentativas por bloco. Uma resposta recusada não é
   publicada; esgotar as tentativas mantém o erro registrado e preserva os dados.
   Na versão 33, a entrada principal continua sendo o primeiro resumo salvo.
   Uma inconsistência nesse original ou uma falha persistente de conteúdo,
   formato ou contexto na reescrita aciona uma recuperação pela evidência
   literal da mesma interação. Uma resposta recusada nunca vira fonte factual.
   Erros de memória, timeout ou encerramento do worker não repetem a inferência
   por essa recuperação: precisam ser resolvidos como erros de execução.
   A reconstrução também tem tentativas limitadas e validação da resposta.
6. O sucesso é gravado por unidade em campos separados. Não altera o original,
   o trecho literal, a identificação, os vínculos nem o PDF.

Esses filtros são heurísticos. Não compreendem toda a semântica clínica e podem
deixar erros passar ou produzir falsos bloqueios. Por exemplo, só conferem
automaticamente alguns padrões de voz ativa/passiva, condições e condutas.
Uma resposta aprovada pelo próprio modelo não basta para publicação: ela também
passa pelos filtros. Ainda assim, passar pelos filtros não comprova fidelidade.
No piloto de Marevan, a conferência manual detectou uma troca de anticoagulação
por coagulação e um efeito indevidamente atribuído ao medicamento sobre seu
próprio princípio ativo. A versão 9 acrescenta bloqueios conservadores para
esses padrões, com testes de regressão. Os textos do piloto eram simulações;
nenhuma dessas respostas foi publicada na base principal.
Na versão 10, números de classes como `beta-2` deixam de contar como doses da
relação com outro integrante da lista. Doses reais continuam obrigatórias, e
subtipos presentes no próprio resumo não podem ser trocados nem omitidos.
Marcadores de lista no início de uma evidência literal não fazem a validação
descartar a oração de contraste após um ponto e vírgula.
As versões 11 e 12 também conferem frequências de administração, limites de
dose, incerteza de observações, raridade, concentração versus eficácia e uma
autorização de uso conjunto que não constava no original. A palavra "inibidores"
numa lista de classes não define, por si só, a direção do efeito do alvo.
O código reconhece "sem, no entanto, aumentar" como negação e mantém a
equivalência entre o nome do princípio ativo com e sem sal ao conferir papéis.
Uma descrição do uso conjunto não é tratada como prova de que o último nome
antes do verbo seja o agente individual do efeito.

O prompt pede a reescrita apenas do resumo original; a evidência serve para
conferência. Uma apresentação nova como "usado para tratar..." ou a inclusão
de um estudo que só aparecia na evidência exige correção da paráfrase. Se o
primeiro resumo apresentar uma inconsistência em relação à evidência, o processo
troca explicitamente para a fonte literal e reconstrói o texto, registrando essa
mudança de referência. A partir da versão 33, esgotar as tentativas de reescrita
do resumo também permite uma recuperação pela evidência literal correspondente.
Essa recuperação é limitada, usa as mesmas verificações e não altera o original.
A verificação do geral procura componentes da própria bula no
meio de enumerações de alvos, sem confundir menções ao componente no efeito.
Todos os parágrafos originais são conferidos antes da primeira inferência.
Se um deles estiver inconsistente, o geral é reconstruído por blocos literais,
validados individualmente e organizados em até três parágrafos. Não mistura a
lista de uma interação com o efeito de outra. A composição só é salva quando
todos os blocos passam; não publica uma parte como se fosse o geral completo.
Na versão 13, anos em citações bibliográficas não contam como doses, e rótulos
como "Interação com X: O uso..." não incluem o restante da explicação na lista
de alvos. A condição de estado de equilíbrio (steady-state) precisa ser mantida.
Na versão 14, Cmax, Cmin e AUC também precisam manter sua identificação: uma
concentração genérica não substitui essas medidas. Os filtros não comprovam
que cada percentual esteja associado à medida correta; essa conferência ainda
faz parte da revisão da amostra contra a fonte.

O geral é tratado por parágrafos, preservando até três blocos; se houver mais,
os excedentes são unidos no terceiro, sem descartar conteúdo. Cada bloco recebe
as evidências dos alvos que nomeia; se não houver associação segura, recebe
todas as fontes. Trechos literalmente iguais são enviados uma só vez, mantendo
os alvos associados. Os IDs completos continuam nas entradas dos relatórios e
nos hashes; o prompt omite esses identificadores internos para reduzir tokens.
Registros da mesma bula, alvo e tipo de alvo são tratados como um card:
o modelo recebe os originais reunidos e todas as evidências são preservadas.
O mesmo resultado é gravado nos registros do grupo em uma transação, e a
interface já elimina textos iguais ao montar o card. Não se compartilha o
resultado entre alvos ou bulas diferentes.
O hash considera todos os originais e evidências do grupo; a API confere também
os registros fora da página atual. Alterar qualquer fonte invalida o conjunto.
Quando o primeiro resumo está inconsistente, o processo pode reconstruí-lo por
blocos literais. Na reconstrução, um prompt compacto reduz instruções repetidas,
mantendo as evidências completas e o guia de vocabulário. Retirar esse guia
fazia o Smol voltar a copiar a redação técnica. Se várias fontes ainda não
couberem juntas, elas são tratadas separadamente e os resultados reunidos.
Uma fonte individual que exceda mesmo esse orçamento não é truncada.
O geral só é salvo se todos os seus blocos forem concluídos.

Números de títulos como "6 — Interações medicamentosas" e identificadores
como "BM 16549" não são doses. Valores de dose, percentuais, duração e população
extraídos da fonte também aparecem como lembretes no prompt. Não se verifica
se uma dose é adequada: procura-se preservar o que o documento informou.
A retomada de `--todos` considera a publicação já salva: texto simplificado
preenchido e `publicado.status = CONCLUIDO`. Mudanças de versão, prompt, modelo
ou agrupamento não colocam esse resultado novamente na fila. Uma tentativa
posterior falha também não apaga o sucesso anterior. Em medicamentos parcialmente
concluídos, somente as unidades ainda sem publicação salva são executadas.
Refazer publicações existentes exige `--reprocessar` explicitamente.
A conferência de fonte e hashes para exibição na API continua separada: um
resultado desatualizado não é apresentado como se correspondesse à fonte nova.

### Prompts e correção automática

Na versão 35, o prompt distingue “risco aumentado” de apenas “risco”. A linguagem
simples precisa preservar “maior risco” ou “maior chance”, mantendo a possibilidade
e o evento descritos. A orientação positiva só é acrescentada em frases sem
negação e não inclui exemplos de eventos ausentes da fonte. As tentativas de
correção de vocabulário também mantêm essa orientação. A verificação de aumento
permanece ativa; não se acrescenta uma palavra ao resultado para forçar aprovação.

Na comparação, “níveis elevados de potássio no sangue” e “níveis altos de potássio
no sangue” descrevem o mesmo estado, sem afirmar que o medicamento elevou esses
níveis. Concordâncias “letal/letais” e “fatal/fatais” também são normalizadas
somente na comparação. A saída não recebe essas substituições e os controles
continuam recusando aumento de ação ou gravidade sem apoio na fonte.

Redução do clearance plasmático é explicada como retirada mais lenta do
medicamento do sangue. O prompt preserva esse sentido e distingue aumento de
atividade de melhora da eficácia; não acrescenta benefício clínico à informação
da bula. A orientação também acompanha as correções de vocabulário.

Se a resposta inventar um dado de concentração ou uma enumeração de exemplos
ausentes da evidência, a próxima tentativa usa um prompt curto com a mesma
referência, sem repetir o rascunho que contém a invenção. Na reescrita normal,
essa referência continua sendo o resumo do primeiro modelo; na reconstrução,
são os trechos literais daquela interação. As validações factuais permanecem
ativas. Afirmações explícitas de quem é conhecido por causar um efeito mantêm
o sujeito original, sem transferir a causa para o rótulo do item.
Os nomes fornecidos nos metadados também participam da conferência de grafia.
Uma grafia semelhante inventada pelo modelo é recusada, não substituída
automaticamente por outro medicamento. O prompt pede a cópia exata do nome
conhecido, inclusive quando a oração da fonte usa apenas a classe.

Listas puramente exemplificativas introduzidas por “tais como” podem fornecer
até três exemplos ao geral, mantendo o aviso comum. Listas com doses, condições,
exceções ou efeitos adicionais não são recortadas por esse procedimento. As
evidências e a entrada auditada permanecem completas. A preferência de iniciar
um parágrafo com “Com” fica no prompt: uma abertura correta diferente não
provoca revisão apenas por essa escolha de estilo.

Na reconstrução do geral, cada bloco aprovado passa a ter progresso salvo em
`processamentos_interacoes.reescrita_controle.progresso_geral`, sem novas tabelas
ou campos. O controle contém o hash da entrada completa, modelo, versão e os
textos/hashes dos blocos aprovados. As transações são curtas, após a validação
do bloco; não ficam abertas durante a inferência. A fonte é conferida novamente
antes de persistir o progresso.

Ao retomar com a mesma fonte, original, modelo e versão, os blocos concluídos
são reaproveitados. Blocos com hash de texto inválido são refeitos. Mudanças de
contexto e `--reprocessar` descartam esse reaproveitamento. O progresso não é
exibido como resumo geral: só o texto completo é publicado. Após o sucesso, o
checkpoint temporário é removido do controle, mantendo os textos originais e
os registros de publicação. Em `--simular`, o progresso fica somente em memória;
nenhum checkpoint é gravado no banco.

A retomada por blocos vale para execuções feitas com esse mecanismo. Execuções
anteriores salvavam somente a unidade completa, sem checkpoints intermediários.

Na versão 34, remissões numéricas completas como “ver seções 3, 4 e 5”, listas,
intervalos e subitens deixam de ser interpretadas como quantidades clínicas.
Antes, só o primeiro número da lista era ignorado e os demais podiam bloquear
um texto correto por suposta omissão de dose. Números com unidades clínicas,
percentuais e durações continuam sendo conferidos, inclusive junto de uma
remissão. O número padrão de tentativas continua sendo três por bloco;
`--tentativas` permite aumentar esse limite, sem remover a validação.

Na versão 33, títulos de seções em remissões entre aspas permanecem exatos no
guia e nas correções. A conferência das citações continua ativa; somente esses
identificadores não precisam ter seu vocabulário simplificado. O texto ao redor
continua sujeito à verificação de linguagem simples.

Se a resposta passou pela conferência dos fatos e foi recusada apenas pelo
vocabulário, a correção recebe um prompt curto com o rascunho e o guia de termos.
Isso evita reintroduzir o mesmo jargão ao copiar a fonte técnica novamente.
A nova saída continua sendo validada contra o primeiro resumo ou a evidência
literal que originou a tentativa; o rascunho não substitui essa referência.
O guia não é publicado diretamente: quem produz a nova frase é o SmolLM3.
Se, mesmo após as tentativas do modelo, persistirem apenas os termos neutros
“concomitante”, “conjuntamente” ou “fármaco”, o acabamento troca respectivamente
por “ao mesmo tempo”, “juntos” ou “medicamento”. Não ajusta efeitos clínicos,
doses ou nomes; preserva títulos de seções e refaz todas as verificações antes
de aceitar o texto. Essa intervenção fica registrada nos controles.

Listas simples de classes entre parênteses que contêm o alvo conhecido podem
fornecer grafias literais ao prompt. Uma palavra nova muito parecida com esses
nomes, como “epleronona” em lugar de “eplerenona”, exige correção pelo modelo.
A semelhança não é usada para substituir nomes, resolver substâncias ou
obrigar a inclusão dos demais exemplos. Essa verificação tem escopo limitado
a essas listas; não é um reconhecedor completo de entidades farmacológicas.

Uma resposta marcada pelo runtime como cortada no limite de saída não é enviada
como rascunho completo na próxima tentativa. O limite cresce, dentro do contexto
e até 768 tokens, e o modelo reinicia a redação. Se ainda falhar, tenta-se a fonte
literal conforme o fluxo acima; nenhuma resposta parcial é publicada. Os controles
registram o limite, os tokens usados e o motivo de encerramento.

Na preparação da reconstrução, cabeçalhos/rodapés “TEXTO LEGAL” ou “Modelo de
bula – Profissional” só são removidos quando o marcador está pareado ao nome
exato da bula e à sua apresentação. Uma dose no corpo clínico não é removida.
Uma enumeração introduzida por “tais como”, sem condições, doses, negações ou
outros efeitos, pode apresentar apenas o alvo, mantendo integralmente o aviso
comum que antecede a lista. A entrada auditada e os textos do banco ficam intactos.
Os prompts distinguem estados de potássio alto/baixo de aumento da ação de um
medicamento; um estado não autoriza inventar uma potencialização.
As equivalências de hipercalemia e hipocalemia seguem as definições de potássio
alto e baixo no sangue do [MedlinePlus](https://medlineplus.gov/ency/article/003484.htm).

Quando a fonte diz explicitamente que um estudo foi realizado com uma substância
estruturalmente semelhante a outra, o prompt distingue a substância estudada da
referência estrutural. A conferência recusa trocas explícitas desses papéis;
semelhança de estrutura não transfere o resultado do estudo a outro medicamento.

Na versão 32, o caminho compacto mantém o guia e a evidência literal; reduz
instruções repetidas para caber no contexto. Datas editoriais de rodapé e
referências explícitas a itens/seções não são tratadas como doses. Valores
clínicos, frequências, medidas e condições continuam sendo conferidos.
Na reconstrução, fontes de componentes diferentes, ressalvas de ausência de
estudos e afirmações com possibilidade distinta são tratadas em blocos
separados. Só o geral completo é publicado.

Tabelas alinhadas de três colunas, com ao menos duas linhas completas e medidas
AUC/Cmax com setas, podem ser reorganizadas no guia por coluna. As células
continuam completas. Quando há um sujeito explicitamente rotulado junto das
medidas, o guia apresenta cada medida, substância e percentual em uma frase.
Não calcula doses nem deduz sujeitos ausentes. Fora desse formato, não reordena
a tabela. A evidência auditada permanece igual à do registro.
Uma comparação adicional recusa percentuais explicitamente trocados entre
medidas e a afirmação de que AUC seria uma porcentagem de Cmax.

O guia une duas construções equivalentes de proibição de prescrição apenas
quando seus medicamentos e qualificadores coincidem. Isso não é uma deduplicação
semântica geral. A orientação de consolidar repetições também permanece no prompt.
Relatos sobre outro medicamento não podem ser transferidos para o medicamento
da bula, e “deve-se ter cuidado” não pode virar “pode-se ter cuidado”.
Quando o primeiro resumo contém a construção “Com base no relato ...,
deve-se ter cuidado ...”, suas duas orações são apresentadas separadamente ao
modelo, copiadas do próprio resumo. Nesse caso, o nome pesquisado e a composição
não entram como contexto concorrente. A referência continua sendo o primeiro
resumo, não uma nova consulta à bula. Os nomes do relato e a orientação de
cuidado precisam permanecer explícitos; uma referência ambígua a “os dois
medicamentos” não pode apagar essa diferença.

A prioridade é corrigir a redação no prompt e testar o resultado. Para o Smol,
as instruções ficam na mensagem `system`, junto de `/no_think`; os dados da bula
ficam na mensagem `user`. Na reescrita normal, há somente um texto original,
sem uma segunda versão com substituições pontuais de palavras. Isso evita
induzir construções como "o medicamento pode ser aumentado" quando é seu
efeito que aumenta. A resposta deve reescrever o original sem acrescentar
estudos, indicações ou outros medicamentos que não estavam no resumo válido.
Um erro concreto volta como instrução de correção ao fim da próxima chamada.
Também é enviado o último rascunho recusado com o guia de vocabulário aplicado,
se couber no contexto, para o modelo revisar a frase inteira. O guia é uma
entrada de correção, nunca uma saída publicada automaticamente.
Nunca se acumulam todas as respostas anteriores
nem se cortam fontes para acomodar esse rascunho.

Na reconstrução pela fonte, o guia usa equivalências explícitas de vocabulário e mantém os dados originais
no prompt. Por exemplo, cefaleia/dor de cabeça e hipotensão/pressão baixa são
termos usados também pelo [Ministério da Saúde](https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z/g/gripe-influenza)
e pela [bula do fabricante](https://www.cristalia.com.br/produto/96/bula-paciente).
A expansão de Cmax preserva o significado de concentração máxima no plasma,
conforme a [documentação da FDA](https://www.fda.gov/training-and-continuing-education/cderlearn/online-training-seminar-fda-process-approving-generic-drugs-presentation-transcript).
O guia orienta a entrada; a saída continua sendo escrita pelo modelo e validada.
Não se substitui uma resposta recusada por um resumo genérico para publicá-la.
Há uma preservação restrita de modalidade: se a referência diz “deve-se ter
cuidado” e o modelo troca somente essa palavra por “pode-se”, o sistema compara
o texto inteiro após o mesmo guia de vocabulário. Só se todo o restante for
equivalente restaura “deve-se” da referência. Diferenças de nomes, doses,
condições, omissões ou acréscimos impedem essa correção. Todas as validações
ainda são aplicadas ao resultado. A saída bruta permanece no log e a ação é
registrada como “preservacao_modalidade_cuidado”, sem alterar a fonte.

Os prompts pedem início direto pelo efeito, combinação ou condição, sem
"Nesta bula" ou "A bula descreve". A validação de redação devolve esse tipo de
introdução para nova tentativa. Remissões específicas a Contraindicações
continuam preservadas.
Um acabamento editorial remove introduções e atribuições finais redundantes
à fonte conhecida, como "A bula de ... descreve" e "segundo a bula de ...".
Não remove condições nem remissões a seções. Respostas brutas e textos finais
continuam disponíveis no log e na auditoria.
O geral recebe orientações diferentes de abertura para os blocos que compõem
cada parágrafo. Só o tipo de abertura é passado; não são enviados trechos ou
nomes dos parágrafos anteriores para o modelo reaproveitar como fatos.

Orientações de vocabulário explicam clearance plasmático como retirada do
medicamento do sangue, sem inventar uma via renal quando ela não aparece.
A definição se baseia no [IUPAC Gold Book](https://goldbook.iupac.org/terms/view/CT06860).
Toxicidade é explicada como efeitos prejudiciais, conforme o
[dicionário do NCI](https://www.cancer.gov/publications/dictionaries/cancer-terms/def/toxicity).
Os termos ainda técnicos e sem explicação são recusados para nova redação.
Uma explicação no próprio texto pode manter o termo entre parênteses, por
exemplo "efeitos prejudiciais (toxicidade)", sem forçar uma revisão inútil.
Não se apagam informações para obter aprovação. "Mais devagar" e "mais rápido" são
reconhecidos como redução e aumento de velocidade somente na comparação
interna dos textos; nenhuma frase publicada é substituída por essas regras.
O contexto de estudo presente no texto reescrito também precisa permanecer.
O guia explica hipercalemia como níveis altos de potássio no sangue, conforme a
[Anvisa](https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/novos-medicamentos-e-indicacoes/lokelma-r-ciclossilicato-de-zirconio-sodico-hidratado-novo-registro).
Efeitos farmacodinâmicos são explicados como efeitos no organismo, de acordo
com a [definição do NICHD](https://www.nichd.nih.gov/health/topics/pharma/conditioninfo).
A explicação de efeitos inotrópicos negativos preserva o enfraquecimento das
contrações do coração, descrito na
[bula de verapamil da FDA](https://www.accessdata.fda.gov/drugsatfda_docs/label/2016/020943s032lbl.pdf).
Essas equivalências orientam a redação; não acrescentam sintomas ou condutas.
Um intervalo entre medicamentos não autoriza inventar qual vem antes ou depois.
Na expressão legada "excreção renal dos níveis de X", a comparação reconhece
que a oração descreve a eliminação de X; não exige acrescentar uma afirmação
sobre concentração para aprovar a frase. Informações explícitas sobre
concentração continuam protegidas, assim como aumento/redução e doses.

Quando dois trechos rotulados têm o mesmo efeito em palavras de conteúdo e
na mesma ordem, o modelo reescreve esse efeito uma única vez. Artigos, formas
de/do/da, pontuação e o conector "devido" não criam efeitos diferentes.
Nomes, números, negações, marcadores causais e os demais qualificadores precisam
coincidir. Se houver diferença, essa composição não é aplicada.
Os rótulos das condições e as remissões a seções são preservados literalmente
e unidos ao efeito reescrito. Isso evita que o modelo invente uma recomendação
ao interpretar "vide Contraindicações", ou desloque a referência para outra faixa.
O controle registra essa composição como "efeito_llm_condicoes_literais";
nas unidades com a preservação restrita de “deve-se”, registra
"texto_llm_modalidade_preservada"; nas demais, "texto_integral_llm".
Os originais ficam intactos.
Nas correções dessa composição, somente a resposta bruta do modelo volta como
rascunho. As condições e remissões acrescentadas pelo sistema ficam fora do
rascunho, evitando que sejam reproduzidas pelo modelo e acrescentadas novamente.
Na conferência de direção, expressões como "os rins eliminam menos" e
"o organismo remove mais" são reconhecidas como redução e aumento da quantidade
eliminada. Essa comparação não altera a saída nem confunde "menos de 15 mg"
com uma redução do efeito.

O geral reúne fontes com os mesmos alvos antes da redação; parágrafos originais
que voltam aos mesmos alvos também são lidos em conjunto. O prompt pede para
consolidar efeitos iguais e manter efeitos ou condições diferentes.
Uma proteção de redação recusa repetições detectadas e perda de remissões.
A ausência de dose não impede a reescrita. Os filtros procuram conservar os
valores existentes, sem avaliar se a dose é clinicamente adequada.
As orientações de vocabulário entram apenas quando o termo aparece na entrada:
por exemplo, efeito analgésico/efeito de aliviar a dor, cardiovascular/coração e
vasos sanguíneos e steady-state/estado de equilíbrio. Na expressão "níveis
subterapêuticos", o prompt explica que os níveis estão abaixo da faixa esperada
para o tratamento e pede para manter explícito de qual medicamento são esses
níveis, sem deduzir perda de eficácia. A distinção de níveis abaixo da faixa-alvo
é usada também pelo [NHS SPS](https://sps.nhs.uk/articles/supporting-lithium-safety-across-the-system/).
O regime de dose única também é pedido expressamente no prompt e conferido como
condição a preservar, evitando generalizar um resultado para uso contínuo.
Os lembretes também conservam o fragmento que liga a dose única à substância,
o regime diário e a população saudável do estudo, quando aparecem na entrada.
Na reescrita de um original com "pode" ou "podem", a instrução pede esses
marcadores expressamente para não transformar possibilidade em certeza.
Quando simplifica um termo que estava entre aspas, o prompt pede para retirar
as aspas da paráfrase. A validação de citações permanece: palavras apresentadas
como uma citação precisam existir literalmente na evidência.

Na comparação por alvo, uma continuação modal em minúscula após um ponto de
extração, como "lista de medicamentos. pode retardar...", permanece associada
à lista. Essa preparação só afeta a referência interna de comparação; não
altera o trecho da bula armazenado. Retardar a eliminação e torná-la mais
lenta são reconhecidos como redução de sua velocidade, sem classificar uma
formulação de liberação retardada como efeito de interação.
Medidas como AUC, AUC∞ e AUC24 aparecem explicitamente nos lembretes e nas
orientações. Se a própria fonte define "exposição sistêmica (AUC)", manter
"exposição sistêmica" é aceito, sem exigir a repetição da sigla. Uma exposição
genérica não substitui Cmax ou Cmin. A orientação conceitual distingue
exposição ao longo do tempo de concentração máxima, conforme a
[documentação da FDA](https://www.fda.gov/media/121311/download).

Na reconstrução de um card, uma oração passiva com um único efeito seguido de
uma lista simples pode ser focalizada no alvo: o prompt recebe o início literal
da oração e o membro literal da lista. Essa seleção não é aplicada quando há
condições, doses, negações ou outros efeitos na lista. Os demais casos recebem
as evidências completas; trechos idênticos são enviados uma única vez.
As fontes completas permanecem na entrada auditada. A comparação só separa os
marcadores editoriais e os exemplos comuns nos casos estritos descritos acima,
mantendo os avisos e as condições da relação. Não se inventa indicação ou via
de uso para explicar o nome do medicamento. Nomes de classes como "inibidores
da ECA" não são tratados, por si só, como afirmações de redução do efeito.
As etapas registram motivo da reconstrução, erros das tentativas, hashes e
aprovação final em `reescrita_controle`, sem criar novas tabelas ou mudar PDFs.

### Campos, vigência e rastreabilidade

As colunas da etapa 007 já existem no banco de publicação e no backup de
distribuição. Esta atualização não cria nem altera migrações; a API pressupõe
que esses campos existam. Os campos anteriores continuam preservados.

| Tabela.campo novo | Conteúdo |
| --- | --- |
| `interacoes.resumo_card_simples` | Versão reescrita do card. |
| `interacoes.reescrita_controle` | JSONB da publicação e da última tentativa. |
| `interacoes.resumo_origem` | Origem real do resumo original nas próximas gravações: `literal`, `reaproveitado` ou `llm`. |
| `processamentos_interacoes.resumo_geral_simples` | Geral reescrito. |
| `processamentos_interacoes.reescrita_controle` | Mesmo controle para o geral. |

O controle registra hash das entradas e do texto, modelo/revisão/runtime,
versão do prompt, execução, status, erro e tempos. `publicado` identifica o
último resultado concluído; `ultima_tentativa` permite registrar uma falha
sem apagar essa publicação. O JSONL da execução contém entradas completas,
IDs, origem do original e saída, para permitir a futura conferência.
Os logs têm respostas brutas. Não são uma avaliação clínica pronta.

`rastreabilidade.py` é compartilhado com a API e usa somente a biblioteca
padrão. O hash inclui resumo original, alvo, princípios ativos, evidência,
processamento, hash do tópico e hash do PDF. Alterá-los torna a reescrita
antiga indisponível para exibição, mesmo que o texto continue armazenado.
Repetir a CLI pula publicações com entrada, modelo e prompt compatíveis.
Mudar o modelo configurado seleciona novas pendências; a API não carrega essa
configuração de inferência e pode servir a publicação anterior enquanto suas
fontes e sua versão de validação continuarem válidas.

Registros antigos podem não informar se o resumo Qwen foi gerado ou reaproveitado.
Eles ficam com origem `nao_registrada` no relatório. Não se inventa a origem
retroativamente. O identificador `trecho_literal` permite reconhecer os literais.

### Windows: instalar, configurar e executar

Execute na raiz `TCCII`. O ambiente de extração já existente pode ser reutilizado;
nesta implementação não foi instalada nova dependência nem outro servidor.
Para preparar outro ambiente equivalente:

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pip install -r .\pipelines\resumos_interacoes\requirements.txt
```

Use o `DATABASE_URL` já configurado nos resumos/identificação. As opções
`REESCRITA_*` estão em `.env.example`. Não copie as variáveis do segundo modelo
para as variáveis `IDENTIFICACAO_*` do primeiro. O backup de distribuição já
inclui as colunas necessárias; não reaplique SQLs no banco de publicação para
executar esta etapa.

```powershell
# Confere a seleção, sem carregar modelo nem gravar.
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pipelines.resumos_interacoes.reescrita --nome-normalizado aerolin --listar

# Piloto pequeno real: até dois cards e o geral, sem gravar nada.
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes.reescrita --nome-normalizado aerolin --limite-cards 2 --simular --contexto 2048 --tokens-geral 384

# Sem opção de seleção: uma bula pendente, com gravação por unidade.
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes.reescrita

# Uma bula escolhida ou um lote limitado. Repetir retoma pendências.
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes.reescrita --nome-normalizado marevan
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes.reescrita --limite 3

# Força nova tentativa inclusive de versões compatíveis já concluídas.
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes.reescrita --nome-normalizado marevan --reprocessar

# Somente posteriormente, após revisar o piloto. Não executado nesta entrega.
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes.reescrita --todos
```

Não use `--reprocessar` para simplesmente retomar. `--limite-cards` limita os
cards por bula, mas mantém a tentativa do geral existente; não significa um
limite total de chamadas. `--simular` executa o modelo sem gravar sucesso ou
falha no banco. `--listar` não executa inferência.

Por padrão: CPU, 2 threads, lote 16, contexto 2.048, saída de 256 tokens por
card e 384 por parágrafo, 180 s por chamada, 600 s para carga/download, limite
de 3.584 MiB do worker e mínimo de 1.800 MiB disponíveis antes da carga.
São limites de execução, não uma reserva de RAM. O provedor encerra o processo
se ultrapassar memória ou prazo, inclusive durante a inferência; Ctrl+C também
fecha o worker. `--tentativas` controla as correções de conteúdo, linguagem e
formato; o padrão é três por bloco e por fonte de recuperação. Persistir uma
falha mesmo após a tentativa pela fonte literal mantém o registro pendente e
não publica texto incorreto. Exceder memória interrompe o lote, com
as unidades restantes pendentes. Depois de timeout, uma próxima unidade pode
recriar o worker; em funcionamento normal há uma carga por processo.

A referência de 8 GB é compartilhada com Windows, PostgreSQL, API e demais
aplicativos. Os testes foram feitos em i7-12700K, 16 GB, Windows 11 Pro, com
pouca memória livre, e não comprovam execução em qualquer computador de 8 GB.
Nos pilotos, o segundo modelo sozinho chegou a aproximadamente 3,4 GiB com
contexto 2k, inclusive com lote 16. Esse valor não inclui os outros aplicativos.
Se a memória livre estiver abaixo do mínimo, não se inicia a inferência nem se
apagam resumos. Não baixe o limite de proteção para forçar uma carga completa.
Não execute a etapa Qwen ao mesmo tempo sem memória disponível para ambas.

Relatórios `reescrita_*.jsonl` e `.json` ficam na pasta já existente `relatorios/`;
logs em `logs/resumos_interacoes.log`. Há medidas separadas de localização/
download, carga, inferência, validação, persistência e total. O pico é o RSS/
working set observado do worker, não o consumo total do computador nem o commit
de memória; pode não capturar picos mais rápidos que a amostragem.
Cada entrada do JSONL é descarregada no arquivo ao terminar a unidade.

### Exibição e avaliação futura

A API apenas lê campos persistidos e valida sua vigência. Na consulta individual,
o geral prefere `texto_simples`; cards individuais e de pares preferem
`resumo_card_simples`. Sem versão compatível, usam os textos originais anteriores.
Não são rotulados como simplificados quando são fallback. A evidência literal
e as regras de agrupamento/origem permanecem as anteriores.

Os IDs, hashes, PDFs e textos de cada execução permitem comparar original,
reescrita e evidência depois. Não foram implementados escores automáticos nem
o estudo completo de avaliação. Usar o segundo modelo para avaliar sua própria
reescrita introduz viés: não é avaliação independente nem padrão-ouro. A futura
avaliação precisa de amostra de referência conferida por pessoas, de preferência
com apoio de profissional qualificado.

Arquivos novos: `reescrita.py` (CLI/coordenação), `rastreabilidade.py` (hashes e
vigência), colunas de reescrita já existentes e `tests/test_reescrita.py`. Os demais ajustes reutilizam
`prompts.py`, `validacao.py`, `repositorio.py`, `provedor.py` e os contratos e
componentes existentes da API/frontend. `servico.py` registra a origem real dos
próximos resumos do primeiro estágio, sem trocar seu modelo nem suas regras.
