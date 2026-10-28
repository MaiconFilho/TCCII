# Inclusão de medicamentos sob demanda

## Situação atual: suspensa em 21/10/2026

O código foi mantido para apresentação ao orientador, mas o sistema consulta
somente medicamentos já cadastrados. Nomes ausentes exibem uma mensagem de erro;
não acionam scraping nem os demais pipelines. A interface não monta o componente
de preparação, inclusive quando há pedidos antigos no armazenamento do navegador.
A API bloqueia os três endpoints de preparação com HTTP 503 e não inicia o trabalhador.
Os pedidos e logs de diagnóstico existentes foram preservados.

A suspensão ocorre porque os testes reais falharam na verificação de segurança
do Bulário/retornaram HTTP 403, inclusive usando a configuração original do
scraper e com confirmação humana. Não foi validada uma inclusão real até o fim.
Isso não significa que o medicamento não existe na Anvisa.

As opções `API_INCLUSAO_MEDICAMENTOS` (API) e
`NEXT_PUBLIC_INCLUSAO_MEDICAMENTOS` (frontend) são `false` por padrão.
Uma futura reativação exige ambas em `true`, reiniciar a API e refazer o build
do frontend; `API_WORKER_AUTOSTART` controla a partida do trabalhador somente
quando a inclusão está habilitada. Não reativar sem validar o acesso externo.
O scraper independente continua disponível para execução manual.

As seções abaixo documentam a implementação experimental e o histórico dos testes.

O frontend solicita a inclusão quando a consulta local não encontra o medicamento.
A API registra o pedido e inicia automaticamente um trabalhador Python separado, que executa os pipelines,
um medicamento por vez. A consulta HTTP não espera a geração e não carrega
modelos nem Selenium. Nenhum scraping é disparado pelo autocomplete.

**Configuração atual — restauração em 21/10/2026:** a abertura da sessão e os
scripts HTTP de consulta/download foram restaurados ao commit `ac90869`, da
primeira coleta. `navegador.py` e `SCRIPT_FETCH_TEXTO`/`SCRIPT_FETCH_BINARIO`
foram comparados e são idênticos aos originais. O trabalhador usa Chrome normal
e o perfil `pipelines/scraping_anvisa/.chrome-profile-anvisa`; executa as chamadas
`fetch` originais com `Authorization: Guest`. A chamada automática mantém
`aguardar_confirmacao=False`, equivalente ao `--sem-pausa-inicial` já existente
no scraper. O restante deste histórico descreve também abordagens de diagnóstico
anteriores, inclusive o cliente Angular que deixou de ser usado nesta restauração.

**Resultado do teste com a configuração original:** pedido
`3a0a9436-f1ba-4d63-a6c2-650a8d9256b1`, iniciado pelo POST da interface em
21/10/2026. Chrome normal, perfil original e chamadas `fetch` originais;
integração sem pausa inicial, conforme a opção antiga `--sem-pausa-inicial`.
Resultado: uma tentativa, HTTP 403 na primeira página de Aerolin, `FALHA` em
`ANVISA`, 15,82 s até a leitura final. Não houve download nem inferência. Isso
mostra que restaurar esses componentes não foi suficiente para restabelecer o
acesso hoje; não determina a data em que a proteção do portal foi implantada.

**Situação do piloto local em 20/10/2026:** a consulta real de Jaypirca recebeu
HTTP 403 da Anvisa. A fila, a API e o trabalhador funcionaram, mas não foi possível
validar uma inclusão real até o fim. O pedido terminou em `FALHA`, na etapa
`ANVISA`, sem nova bula, sem download e sem inferência. Não houve tentativa de
contornar o bloqueio. Os testes simulados não substituem essa validação externa.

**Teste de Aerolin em 21/10/2026:** a solicitação
`0beee563-7e73-4eda-ae89-7258fff13905` também recebeu HTTP 403 na primeira
página da consulta, após uma tentativa. O fluxo foi acionado e parou em `ANVISA`,
antes de baixar a bula ou carregar modelos. O tratamento de bloqueio agora grava
uma mensagem pública específica: “A Anvisa bloqueou o acesso automático ao
Bulário. Não foi possível obter a bula. Tente novamente mais tarde.” A situação
permanece `FALHA`; não se informa inexistência nem se insiste automaticamente.

**Revisão da sessão em 21/10/2026:** a integração anterior chamava `fetch`
diretamente com `Authorization: Guest`, sem o cliente Angular da página. O
Bulário atual registra um interceptor de validação de sessão nesse cliente.
A integração passou a usar o `$http` público da própria página, sem ler/copiar
tokens, desativar verificações ou automatizar CAPTCHA. O Chrome normal é o padrão
do trabalhador, e o aplicativo deve estar carregado antes de consultar.
No teste real, apenas trocar headless por Chrome normal ainda recebeu HTTP 403.
Com o cliente da página, a validação de sessão não terminou no limite de 90 s
(97,85 s no teste completo); a inclusão real permanece sem validação até o fim.
Se a página exigir intervenção humana, ela precisa ser concluída pelo usuário.

**Reteste após o usuário informar que desligou a VPN (21/10/2026):** foi criado
um único pedido de Aerolin pela API, UUID `324e29f1-fa67-4f4a-9fbb-1b45aa55f712`.
O POST retornou 202, o trabalhador assumiu o pedido e a consulta automatizada
voltou a retornar 403. Resultado: `FALHA` em `ANVISA`, uma tentativa, em 30,90 s
até a leitura final. Não houve download, extração ou inferência. A mensagem
pública identificou a falha na busca da bula. A inclusão real continua pendente;
o acesso manual do usuário não comprova a validação da sessão separada do Selenium.

**Piloto com confirmação manual (21/10/2026):** pedido
`9c9d0ac3-f22b-45ca-8f4f-2ed35c0b8320`, uma tentativa, `FALHA` em `ANVISA`,
25,84 s até a leitura final. O usuário informou que marcou a confirmação humana
duas vezes antes de a janela fechar. O encerramento veio do `finally` do trabalhador
após o 403, e não comprova que a confirmação tenha sido aceita. Os registros dessa
tentativa guardaram somente o status 403. O diagnóstico passou a preservar códigos
conhecidos `turnstile_*` enviados pelo cliente da página, sem registrar tokens ou
cookies, para distinguir falha da validação de uma resposta 403 genérica.

**Diagnóstico confirmado no pedido seguinte:**
`92bf4abc-92f1-452f-9b74-9763a0877aea`, 36,25 s até a leitura final, código
`turnstile_erro`. A validação de segurança do Bulário rejeitou a execução antes
de ela avançar para download. O usuário voltou a relatar duas confirmações na
caixa de presença humana. A janela fechou quando o trabalhador encerrou o pedido
com falha; isso não indica confirmação aceita.

A documentação da Cloudflare informa que frameworks de automação como Selenium
não são suportados para resolver desafios de produção. Portanto, marcar a caixa
manualmente não garante a liberação de uma sessão automatizada, e trocar somente
headless por Chrome visível não é uma solução comprovada.
Fontes: https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/
e https://developers.cloudflare.com/turnstile/troubleshooting/testing/.
Não foram usados disfarce de automação, resolução automática de CAPTCHA ou cópia
de tokens. A coleta deve permanecer como falha enquanto o acesso não for aceito.

A rota inicial do navegador foi corrigida de `#/bulario/q/` para `#/bulario/`:
abrir resultados sem filtro produzia o aviso “Você precisa informar ao menos um
dos filtros” da captura enviada pelo usuário. Essa correção é independente da
rejeição do Cloudflare e não é apresentada como solução para ela.

Mensagens de falha identificam a etapa (`ANVISA`, `DOWNLOAD`, `EXTRACAO`,
`IDENTIFICACAO`, `VINCULOS`, `VETORES`, `RESUMOS`). A API também adapta mensagens
antigas ao consultar o andamento, sem expor o erro técnico. O frontend faz uma
leitura inicial de pedidos restaurados, inclusive finais, e aplica a mensagem da
etapa aos resultados antigos do armazenamento local. Não repete o polling de
pedidos finais. Alterações no código/configuração encerram o trabalhador entre
pedidos; a API o inicia novamente quando houver acompanhamento ou nova consulta.

## Arquitetura para apresentar ao orientador

```text
Pesquisa e autocomplete local
    → Consultar interações (inclusão automática se ausente)
    → POST /api/processamentos
    → solicitacoes_medicamentos no PostgreSQL
    → trabalhador Python separado
    → consulta e PDF → extração → identificação → vínculos → vetores → resumos
    → resultados persistidos → API de consulta → interface
```

A fila guarda o andamento. Os dados dos medicamentos, as evidências, os vetores
e os resumos continuam nas tabelas dos módulos existentes. A API decide se o
medicamento já está cadastrado. O orquestrador começa pela consulta à Anvisa com o
nome pesquisado e chama os serviços existentes; não faz uma auditoria inicial do cadastro.

## Fluxo

1. O autocomplete mantém a busca local. Uma sugestão aproximada precisa ser
   selecionada; o sistema não escolhe silenciosamente a primeira sugestão.
2. O usuário aciona **Consultar interações**, como na consulta comum. Se o nome
   estiver ausente, a inclusão começa automaticamente.
   Não há botão separado de busca externa. **Adicionar** também prepara um nome
   ausente no campo de outros medicamentos; a consulta do conjunto espera todos.
3. O POST normaliza o nome com a função do scraper. Reutiliza uma solicitação
   ativa, quando houver. Caso contrário, a API verifica somente se há cadastro
   em `bulas`: responde 200 sem criar trabalho se houver. Não audita extração,
   vetores, identificação ou resumos de medicamentos já cadastrados. A consulta
   normal continua mostrando os dados e os estados existentes desses medicamentos.
4. Nos demais casos, responde 202 com UUID. Pedidos ativos do mesmo nome são
   reutilizados, inclusive com diferenças de caixa, acentos e espaços.
5. A primeira etapa do trabalhador é `ANVISA`: consulta o nome informado usando
   a sessão pública e as funções existentes do scraper. Não consulta o cadastro
   antes disso, não usa o PDF local para dispensar a busca e não exige planilha.
   Se a identidade já foi confirmada nesta mesma solicitação (seleção ou retomada),
   preserva essa confirmação. Depois do scraping, os controles de fonte e etapas
   continuam permitindo retomar uma solicitação interrompida sem refazer o que ela concluiu.
6. A consulta percorre as páginas e mantém a seleção da bula profissional pela
   data de publicação e pelos desempates existentes. Um nome exato é preferido.
   Alternativas, mesmo uma única alternativa de nome diferente, exigem seleção.
   Depois da confirmação, a fila verifica novamente a identidade para evitar
   processar duas vezes o mesmo medicamento por pedidos com nomes diferentes.
7. O download valida a assinatura do PDF. O nome do arquivo inclui o hash, de
   modo que uma nova versão não sobrescreve o PDF anterior. Só depois do download
   válido o cadastro é inserido/atualizado pelo `ControleColeta`.
8. A extração chama `ServicoExtracaoInteracoes`, `ServicoExtracaoCadastro` e os
   repositórios existentes. Tópico e composição são independentes: uma parte
   concluída não é refeita apenas porque a outra está pendente.
9. A identificação chama sua entrada `main` para um nome, preservando a estratégia
   configurada. Resultados vigentes são reaproveitados; revisão não vira conclusão.
10. Os vínculos são atualizados pelo `CatalogoMedicamentos` e por
    `RepositorioIdentificacao.atualizar_vinculos`, apenas para relações vigentes
    cujos alvos correspondam ao novo cadastro. Não se executa novamente a
    identificação das outras bulas, nem se invalidam os demais vínculos da relação.
11. A vetorização usa o provedor e o `ServicoEmbeddings` atuais. O controle de hash
    evita nova inferência quando os chunks/modelo coincidem. Neste fluxo, chunks
    excedentes de uma versão anterior são desativados, sem excluir registros.
12. Os resumos chamam a entrada atual de `resumos_interacoes` para um nome:
    preenchem somente o que falta. Cada card mantém a gravação imediata e o geral
    continua associado à identificação vigente.
13. Antes de concluir, o trabalhador verifica novamente a prontidão da fonte.
    O frontend consulta o GET a cada cinco segundos, encerra nas situações finais
    e abre automaticamente o resultado. Na seleção de vários medicamentos, refaz
    a consulta de todos os pares após a preparação do conjunto.

## Reserva e retomada

`solicitacoes_medicamentos` registra nome solicitado, identidade confirmada,
estado, etapa, opções de seleção, datas, tentativas, checkpoints e erro interno.
Não armazena uma bula fictícia para uma pesquisa sem resultado.

- Índices únicos parciais impedem dois pedidos ativos pelo mesmo nome/identidade.
  A criação e a confirmação usam transações curtas e advisory locks por nome.
- A reserva usa `FOR UPDATE SKIP LOCKED`. Um advisory lock de sessão adicional
  permite somente um trabalhador deste fluxo por banco, mesmo que dois comandos
  sejam iniciados. A sessão de reserva fica aberta, sem transação longa.
- Download e inferência acontecem fora das transações de reserva.
- Ao iniciar, o trabalhador que obteve o lock recupera pedidos deixados em
  `EM_PROCESSAMENTO`. As tentativas são preservadas e limitadas.
- A sessão e a tentativa são conferidas entre etapas e durante a execução dos
  filhos. Perda da reserva interrompe o trabalhador e encerra seus processos
  de inferência; o processo antigo não sobrescreve a reserva retomada.
- Falhas temporárias podem ser reagendadas, com no máximo três tentativas por
  pedido por padrão. HTTP 429 respeita `Retry-After`; HTTP 403 encerra o pedido.
  Não há repetição infinita nem contorno de bloqueios.
- Os pipelines confirmam o que já existe, e o checkpoint de vínculos inclui a
  fonte. Assim, uma falha posterior não força novo download ou extração válidos.
- O acompanhamento fica no armazenamento local do navegador. Atualizar ou sair
  da página não cancela o trabalhador. Para localizar um pedido em outro navegador,
  é necessário conhecer o UUID; não há conta de usuário ou sincronização de histórico.

Estados: `AGUARDANDO`, `EM_PROCESSAMENTO`, `AGUARDANDO_SELECAO`, `CONCLUIDO`,
`NAO_ENCONTRADO`, `FALHA` e `REVISAO_NECESSARIA`.

Somente uma consulta externa válida sem resultados produz `NAO_ENCONTRADO` e a
mensagem **Medicamento não encontrado no Bulário Eletrônico da Anvisa.** Timeout,
403, resposta inválida ou indisponibilidade produzem falha, nunca ausência de
medicamento. Falta de bula profissional/data válida exige revisão.

## Migração e compatibilidade

`006_inclusao_medicamentos.sql` é aditiva e idempotente. Cria a fila e acrescenta:

- `bulas.sha256_cadastro` e `bulas_interacoes.sha256_pdf_fonte`, para vincular as
  novas extrações ao PDF. Para registros legados sem essas marcas, mantém a
  compatibilidade por registro/expediente já utilizada pelo projeto.
- `ativo`, `fonte_sha256` e `total_chunks_fonte` nos vetores. Novas verificações
  registram a fonte e a quantidade de chunks; os hashes existentes ainda conferem
  o modelo e o texto. Vetores legados são aceitos quando os hashes e a cobertura
  do texto atual conferem; caso contrário, o trabalhador verifica a etapa com o
  tokenizer existente e só infere se os hashes realmente mudaram.

As tabelas de interações e seus históricos não são recriadas. As entradas CLI
anteriores continuam disponíveis. A API não aplica migrações automaticamente.

## Configuração separada

**API — `apps/api_interacoes/.env`:** `DATABASE_URL` continua atendendo às
consultas com conexão somente de leitura. `SOLICITACOES_DATABASE_URL` pode fornecer
uma conexão separada para escrever apenas na fila; quando omitida, usa o mesmo
DSN, sem a opção de leitura que é adicionada exclusivamente ao pool de consultas.
O usuário da fila precisa de SELECT nas fontes e de SELECT/INSERT/UPDATE na fila.
Mantenha `API_CORS_ORIGINS` e `EMBEDDING_MODEL_ID` coerentes com a instalação.

`API_WORKER_AUTOSTART=true` é o padrão: ao iniciar a API, ela abre o trabalhador
em um processo separado, sem janela no Windows. Pedidos e consultas de andamento
também verificam se ele continua ativo; se encerrou, podem relançá-lo, com intervalo
mínimo de 30 segundos entre partidas. O lock do PostgreSQL impede dois trabalhadores
de processarem a fila simultaneamente, inclusive após reiniciar a API. O filho
continua independente ao fechar a página ou a API; reiniciar a API não cancela um
processamento já em curso. Erros de inicialização ficam nos logs internos.

`API_WORKER_PYTHON` permite escolher o Python com as dependências dos pipelines.
No ambiente local, usa por padrão a `.venv` de `extracao_interacoes`; se ausente,
usa o Python que iniciou a API (nesse caso, instale nele as dependências do trabalhador).
`API_WORKER_DATABASE_URL` informa uma conexão com permissão para os pipelines, na
mesma base da fila. Se omitida, usa o DSN do `.env` do trabalhador, depois o da
fila/API. Para hospedar o trabalhador em outra máquina, use
`API_WORKER_AUTOSTART=false` e mantenha a entrada CLI existente nesse ambiente.

**Trabalhador — `pipelines/inclusao_medicamentos/.env`:** `WORKER_DATABASE_URL`
ou `DATABASE_URL` aponta para a mesma base com permissão para os pipelines.
Na instalação local, sem esse arquivo, reaproveita o `.env` da identificação.
`WORKER_HEADLESS=false` é o padrão, por compatibilidade com a sessão pública.
O Chrome pode aparecer durante a coleta; não é necessário executar comandos nem
pressionar ENTER no terminal. Uma validação humana exigida pelo Bulário não pode
ser dispensada pelo sistema. Há intervalo de polling, limite de tentativas,
timeout de etapa e caminhos opcionais para PDFs e perfil de Chrome no exemplo.
`WORKER_PYTHON_EXTRACAO`, `..._IDENTIFICACAO`, `..._VINCULOS`, `..._VETORES` e
`..._RESUMOS` permitem ambientes Python distintos; por padrão usa seu próprio Python.

**Modelos e PDFs:** cada filho continua lendo o `.env` de sua etapa e o cache já
existente. O GGUF continua em `extracao_interacoes/modelos_hf`; os embeddings usam
`conversao_interacoes_vetores/modelos_hf`. Não há modelo novo. O trabalhador e seus
filhos precisam acessar esses arquivos e os PDFs. A API só precisa do PostgreSQL;
não precisa montar pastas de modelos ou PDFs. Caso utilize outro modelo de
embeddings, informe o mesmo `EMBEDDING_MODEL_ID` na API e no trabalhador.

## Comandos no Windows

Na raiz `TCCII`, usando o ambiente local existente:

```powershell
# Instalar as dependências do trabalhador no ambiente dos pipelines
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pip install -r pipelines\inclusao_medicamentos\requirements.txt

# Aplicar as migrações aditivas, inclusive a 006
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pipelines.identificacao_interacoes.aplicar_migracoes

# Terminal 1 — API (inicia o trabalhador automaticamente)
.\apps\api_interacoes\.venv\Scripts\python.exe -m apps.api_interacoes

# Terminal 2 — frontend
cd apps\frontend
npm.cmd run dev
```

Se API/frontend já estavam abertos antes da alteração, reinicie-os para carregar
os endpoints e a interface novos. Não é necessário ativar o ambiente via
`Activate.ps1`. `npm.cmd` funciona no PowerShell sem mudar sua política de scripts.

Não é necessário executar um comando de inclusão ao pesquisar: o usuário usa
**Consultar interações**. O módulo de inclusão apenas coordena os serviços já
existentes, guarda o andamento e decide quais etapas faltam; não substitui os pipelines.

Somente para diagnóstico ou execução em outro servidor, a CLI continua disponível.
Para um piloto isolado, desative antes a partida automática na API
(`API_WORKER_AUTOSTART=false`) e confirme que nenhum trabalhador já está ativo.
Depois de obter o UUID pelo POST/interface:

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.inclusao_medicamentos --uma-vez --solicitacao UUID_DO_PEDIDO
```

Esse comando opcional atende somente aquele pedido. Sem `--uma-vez`, o trabalhador
permanece consultando a fila. Na operação normal, a API inicia esse processo em
segundo plano, e a reserva mantém os pedidos e as etapas compatíveis após interrupções.

## Endpoints

| Método | Caminho | Comportamento |
| --- | --- | --- |
| POST | `/api/processamentos` | Corpo `{"nome":"nome pesquisado"}`; 202 com UUID, ou 200 quando já cadastrado |
| GET | `/api/processamentos/{uuid}` | Estado, etapa, opções e mensagem pública |
| POST | `/api/processamentos/{uuid}/selecao` | Corpo `{"nome_normalizado":"opcao retornada"}`; 202 para continuar |

O GET de um pedido que reutilizou outro acompanha o pedido principal. Erros
técnicos, DSNs, caminhos e tracebacks ficam no servidor. Os endpoints de consulta
individual, sugestões e pares mantêm os contratos anteriores.

## Testar pela interface

1. Inicie API e frontend. O trabalhador é iniciado pela API.
2. Digite um nome ausente e clique em **Consultar interações**.
3. Confira o estado real exibido; se houver alternativas, selecione a correta.
4. Atualize a página para verificar a retomada do acompanhamento.
5. Após conclusão, confira autocomplete, resumo geral, **Ver detalhes** e
   **Ver evidência**. Para vários medicamentos, prepare os ausentes e consulte
   o conjunto; a consulta entre pares permanece sem resumo geral.
6. Sem resultado externo confirmado, a mensagem de medicamento não encontrado
   permite corrigir o nome e iniciar outra consulta. Em caso de bloqueio, a
   solicitação fica como falha e não publica uma conclusão sobre interações.

## Testes e piloto desta entrega

| Suíte | Casos descobertos | Resultado da execução |
| --- | --- | --- |
| Inclusão sob demanda | 31 | Sem falhas; inclui testes da fila em PostgreSQL isolado |
| API | 52 | Sem falhas; 16 integrações antigas puladas sem banco de testes configurado |
| Scraping | 18 | Sem falhas |
| Extração | 242 | Sem falhas |
| Identificação | 175 | Sem falhas; 26 integrações puladas |
| Vetores | 142 | Sem falhas |
| Resumos | 69 | Sem falhas; 10 integrações puladas |

- Testes da inclusão incluem nomes fora da planilha, alternativas, ausência,
  falhas externas, tentativas limitadas, perda de reserva e retomada só do cadastro
  ou só dos resumos. Provedores são simulados nos testes unitários.
- A revisão do fluxo confirma que o trabalhador chama o scraping antes de ler
  qualquer cadastro. Na API, um cadastro existente não dispara auditoria das
  etapas; uma solicitação ativa continua sendo acompanhada mesmo depois do
  download inserir a bula, sem antecipar a conclusão.
- A fila foi exercitada em PostgreSQL 18.6 isolado, incluindo concorrência por nome,
  confirmação da identidade, reserva atômica, lock global, seleção e reinício.
  O banco criado para inspeção é `interamed_inclusao_test_20261020_3002eb`.
  Os testes novos não limpam registros: usam nomes únicos por execução.
- A partida automática foi validada com um processo Python real e banco vazio
  isolado (`interamed_inclusao_test_automatico_489b10`): o trabalhador obteve o lock,
  a API respondeu normalmente e sair do ciclo da API não encerrou o filho. O processo
  criado pelo teste foi encerrado ao final. Nenhum pedido ou consulta externa foi feito.
  Os testes unitários verificam a partida na inicialização, em pedidos/seleção e no
  acompanhamento, a ausência de partida por resultados prontos/finais, o intervalo
  entre relançamentos e a configuração de um trabalhador externo.
- API, scraping, extração, identificação, vetores e resumos tiveram suas suítes
  afetadas executadas. As integrações antigas dependentes de um banco de testes
  explícito continuam sendo puladas quando a variável não é configurada.
- Frontend: lint, TypeScript e build estático passaram. Uma verificação com Chrome
  headless e API simulada confirmou consulta individual, pares, preparação,
  restauração após refresh, seleção ambígua e mensagem de ausência. A revisão do
  acionamento automático passou em 11 verificações, incluindo a remoção do botão
  externo, conjuntos com nome principal/secundário ausente e preparação ao adicionar.
  Desktop 1440 px
  e celular 390 px ficaram sem overflow horizontal. Isso não valida a Anvisa real.
- **Piloto real:** Jaypirca, UUID `0605da29-026a-4fe4-aafc-97b2e05bfe72`, em 20/10/2026.
  POST 202, GET final 200 com `FALHA`/`ANVISA`, devido a HTTP 403. Tempo real entre
  início do POST e consulta final: **17,424 s**. O cadastro permaneceu com **5.895
  bulas**. Uma consulta normal de Marevan respondeu 200 após o piloto.
- Na verificação real de Marevan já pronto, o POST respondeu 200/`CONCLUIDO`,
  com `id: null`; a fila permaneceu com a mesma quantidade de pedidos. Não houve
  navegador, nova inferência ou reprocessamento desse medicamento.

Para repetir os testes unitários:

```powershell
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m unittest discover -s pipelines/inclusao_medicamentos/tests -t .
.\apps\api_interacoes\.venv\Scripts\python.exe -m unittest discover -s apps/api_interacoes/tests -t .
```

Para testar a fila com PostgreSQL real, aplique o esquema/migrações em um banco
isolado e configure `INCLUSAO_TEST_DATABASE_URL`. O teste recusa o banco TCC e
qualquer banco cujo nome não contenha `test`. Não aponte essa variável à produção.

## Arquivos desta implementação

| Arquivo | Responsabilidade |
| --- | --- |
| `inclusao_medicamentos/repositorio.py` | Fila, transações, confirmação, reserva e prontidão |
| `inclusao_medicamentos/estado.py` | Estados públicos e verificação leve de compatibilidade |
| `inclusao_medicamentos/orquestrador.py` | Ordem das etapas e reaproveitamento |
| `inclusao_medicamentos/coleta.py` | Adaptação do scraper para um nome sem planilha |
| `inclusao_medicamentos/etapas.py` | Adaptadores dos pipelines existentes, executados em filhos |
| `inclusao_medicamentos/__main__.py` | Loop, lock do trabalhador, tentativas e timeout |
| `inclusao_medicamentos/tests/` | Testes unitários e da fila em banco isolado |
| `inclusao_medicamentos/.env.example` e `requirements.txt` | Configuração e dependências do trabalhador |
| `identificacao_interacoes/migracoes/006_inclusao_medicamentos.sql` | Migração aditiva |
| `scraping_anvisa/anvisa_scraper/api_anvisa.py` | Consulta reutilizável por nome e alternativas, com validação de resposta |
| `conversao_interacoes_vetores/repositorio.py` | Opção para preservar chunks excedentes e marcas da fonte |
| `apps/api_interacoes/app.py`, `rotas.py`, `contratos.py`, `configuracao.py`, `.env.example` | Partida automática do trabalhador, endpoints, contratos OpenAPI, CORS POST e conexão separada da fila |
| `apps/api_interacoes/tests/test_preparacao.py` | Contratos HTTP, partida do processo e proteção de dados internos |
| `apps/frontend/components/preparacao-medicamento.tsx` | Acompanhamento, seleção e persistência local |
| `apps/frontend/components/interamed.tsx` e `lib/api.ts` | Preparação automática ao consultar/adicionar e integração com as consultas existentes |
| Testes de migrações/vetores e `.github/workflows/tests.yml` | Verificações dos pontos alterados |
| READMEs e `.gitignore` | Instruções e exclusão de logs/perfil de navegação do versionamento |

Limites: sem uma resposta pública válida da Anvisa, não há como finalizar a
inclusão real neste ambiente. Não foi feito deploy, processamento da base inteira
ou alteração de estratégia/modelo. Não há prioridade de fila, contas, notificações
ou execução distribuída; a separação entre API e trabalhador permite hospedá-los
em ambientes diferentes posteriormente.
