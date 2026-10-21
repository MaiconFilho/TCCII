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
