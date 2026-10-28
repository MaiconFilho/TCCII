# InteraMed — consulta de interações medicamentosas

O InteraMed é um projeto acadêmico que reúne informações das bulas profissionais
da Anvisa para consultar interações entre medicamentos. O sistema coleta os
documentos, extrai os textos, identifica os medicamentos citados e relaciona
essas citações aos cadastros do PostgreSQL. Uma etapa posterior prepara resumos
para apresentação na interface, mantendo os trechos originais como evidência.

O processamento dos documentos é separado da consulta: a API lê resultados já
gravados e não executa scraping ou modelos de linguagem durante uma pesquisa.

## Funcionalidades

- **Consulta individual:** pesquisa um medicamento cadastrado e apresenta o
  resumo geral e as interações identificadas na própria bula. O botão
  **Ver detalhes** abre os cards e permite carregar mais resultados.
- **Consulta entre vários medicamentos:** permite adicionar medicamentos à
  seleção e verifica todos os pares únicos, inclusive entre os medicamentos
  adicionados. Cada par considera as citações encontradas nas duas bulas.
- **Evidências reunidas:** diferentes trechos do mesmo par ficam agrupados na
  apresentação, com acesso às citações e às respectivas fontes.
- **Identificação da fonte:** mostra a bula de origem, a data de publicação,
  o medicamento ou substância citado e o vínculo com o medicamento consultado.
- **Medicamentos associados:** esclarece quando a evidência menciona somente
  um componente de uma formulação com mais de um princípio ativo.
- **Resumos dos resultados:** apresenta textos curtos nos cards e uma visão
  geral das interações da bula, preservando a consulta aos trechos literais.
- **Estados explícitos:** diferencia análise concluída, pendente, em revisão,
  desatualizada ou com falha, sem transformar falta de dados em conclusão negativa.
- **Interface responsiva:** adapta os campos e resultados a desktop e celular,
  com navegação por teclado nas sugestões e indicadores de carregamento.

A pesquisa usa somente medicamentos já cadastrados. Um nome ausente retorna
uma mensagem de erro e não inicia inclusão nem coleta automática na Anvisa.

## Arquitetura

```text
Bulário Eletrônico da Anvisa
    → scraping: PDFs e cadastro dos medicamentos
    → extração: composição, princípios ativos e tópico de interações
    → identificação: relações, evidências e vínculos com os medicamentos
    → resumos: textos de apresentação dos cards e da própria bula
    → API de consulta → interface web

Tópicos extraídos → vetorização → embeddings no PostgreSQL/pgvector
                                (etapa independente da consulta atual)
```

Cada etapa possui seus próprios módulos, configuração e registros de execução.
Os dados persistidos permitem consultar resultados sem manter modelos carregados
na API e retomar o processamento conforme os controles de cada etapa.

## Tecnologias

| Parte | Tecnologias e uso |
| --- | --- |
| Coleta | Python e Selenium para consulta ao Bulário e download dos PDFs. |
| Extração | PyMuPDF para leitura, OCR seletivo e modelo local para confirmar os dados e limites das seções. |
| Identificação | Python, dicionário derivado dos cadastros e regras de correspondência; estratégias opcionais com LLM. |
| Resumos | Qwen2.5-1.5B-Instruct em GGUF Q4_K_M, executado em CPU por `llama-cpp-python`. |
| Vetorização | Transformers e `intfloat/multilingual-e5-small`, com vetores de 384 dimensões. |
| Persistência | PostgreSQL, pgvector e acesso Python com psycopg. |
| API | FastAPI, Pydantic e pool de conexões somente de leitura. |
| Interface | Next.js, React, TypeScript, Tailwind CSS, shadcn/ui e Three.js no efeito visual do cabeçalho. |
| Verificação | unittest, testes com PostgreSQL isolado, ESLint, TypeScript e GitHub Actions. |

## Como cada etapa foi implementada

### 1. Coleta das bulas

O scraper lê os nomes da planilha de medicamentos, consulta o Bulário com uma
sessão do Chrome e seleciona a bula profissional pela data de publicação.
O PDF é salvo localmente, enquanto o cadastro registra identificação, publicação,
caminho do arquivo, hash, status e tempos da coleta.

O controle permite reconhecer coletas concluídas e PDFs existentes. Bloqueios,
limitação de requisições e ausência de resultados possuem tratamentos distintos.
O acesso depende das condições atuais do portal; uma falha de acesso não é
tratada como comprovação de que o medicamento não existe.

### 2. Extração do conteúdo

O PyMuPDF lê o PDF. Quando a leitura exige reconhecimento de caracteres,
é aplicado OCR seletivo. As regras de localização e o modelo local auxiliam a
confirmar a seção e seus limites; o tópico de interações permanece como texto
extraído da bula, em vez de ser substituído por uma explicação gerada.

A etapa também extrai composição e princípios ativos, usados posteriormente
para relacionar os nomes citados aos medicamentos cadastrados. Status, revisão
e tempos permitem acompanhar falhas e executar novas tentativas.

### 3. Identificação das interações

A estratégia padrão, `dicionario`, consulta os nomes e princípios ativos do
cadastro e monta uma estrutura de busca em memória. Ela percorre os trechos
extraídos, encontra os nomes e aplica regras para descartar negações, exceções,
autorreferências e outras menções que não sustentam uma relação positiva.

O dicionário não é uma tabela pronta de interações: é uma lista de termos
derivada dos medicamentos existentes. A relação surge quando um desses termos
é encontrado no tópico de interações de uma bula e passa pelos filtros.

A resolução do alvo procura correspondências por nome, princípio ativo e
equivalências declaradas na composição, incluindo as regras existentes para
sais e formas relacionadas. Cada vínculo mantém seu critério e fundamento.
Quando uma formulação contém vários componentes, o vínculo não significa que
todos eles foram citados no trecho.

Também existem as estratégias opcionais `atual` e `duas_etapas`, com modelo
local e validação das respostas. O escopo do dicionário é limitado aos nomes
resolvidos pelo cadastro; não se infere automaticamente uma classe farmacológica
para todos os medicamentos que poderiam pertencer a ela.

### 4. Geração dos resumos

O serviço lê as interações da identificação vigente e verifica os campos de
resumo. Por padrão, ignora o que já está preenchido e gera somente as pendências.
Cada card validado é gravado em uma transação curta, permitindo retomar o lote.

Os cards podem vir de três caminhos: apresentação literal de um trecho curto,
texto escrito pelo LLM ou reaproveitamento de um texto já validado para outro
alvo da mesma oração e do mesmo efeito. Esse reaproveitamento tem restrições
para não misturar efeitos diferentes ou trocar os papéis dos medicamentos.

Os prompts recebem a evidência e o alvo explicitamente. Quando a bula separa
uma lista de medicamentos da descrição do efeito, o contexto recupera as duas
partes do mesmo bloco. As validações verificam formato, nomes, contexto,
negações e afirmações sem suporte, mas não substituem conferência clínica.

O resumo geral é preparado a partir dos cards preenchidos, agrupados por tipo
de efeito. Pode conter de um a três parágrafos, conforme os dados disponíveis,
mantendo o medicamento pesquisado como origem. A substituição de resumos prontos
exige as opções explícitas de reprocessamento ou correção do módulo.

### 5. Vetorização

O módulo divide os tópicos extraídos em chunks por tokens, com sobreposição,
e gera embeddings normalizados. Os vetores são persistidos com pgvector e um
controle por hash identifica conteúdos já processados.

Essa representação foi preparada para busca semântica futura. Os vetores não
participam da consulta atual da interface nem selecionam as evidências usadas
para gerar os resumos.

### 6. API e interface

A API consulta o PostgreSQL com SQL parametrizado e publica contratos Pydantic.
O controle de vigência considera a compatibilidade entre a análise e sua fonte,
evitando apresentar uma análise antiga como se correspondesse ao texto atual.
Os resumos são lidos dos campos persistidos, sem inferência durante a requisição.

O frontend consulta as sugestões, resolve o medicamento escolhido e solicita os
resultados. Na seleção de vários medicamentos, monta todos os pares únicos e
consulta até quatro pares simultaneamente. A janela de evidências reúne os
trechos e explica o que fundamentou o vínculo apresentado.

## Organização do repositório

```text
TCCII/
├── apps/
│   ├── api_interacoes/                API e contratos de consulta
│   └── frontend/                      interface web
├── pipelines/
│   ├── scraping_anvisa/               coleta de PDFs e cadastro
│   ├── extracao_interacoes/           leitura, OCR e extração dos dados
│   ├── identificacao_interacoes/      identificação e vínculos
│   ├── resumos_interacoes/            geração dos textos de apresentação
│   ├── conversao_interacoes_vetores/  embeddings dos tópicos
│   └── caminhos.py                   compatibilidade dos caminhos de arquivos
├── backups_banco/                     backup de distribuição e restauração
├── .github/workflows/                 verificações automatizadas
├── .gitignore
└── README.md
```

Os módulos são executados a partir da raiz, com `python -m pipelines.<modulo>`
ou `python -m apps.api_interacoes`. Cada componente mantém suas dependências,
exemplo de configuração e testes. `pipelines/caminhos.py` reconhece caminhos de
PDFs anteriores à reorganização sem reescrever os valores persistidos.

## Dados e rastreabilidade

| Tabela ou visão | Responsabilidade |
| --- | --- |
| `bulas` | Cadastro, publicação, composição, princípios ativos e controle da coleta. |
| `bulas_interacoes` | Tópico extraído, status da extração e identificação da fonte. |
| `processamentos_interacoes` | Histórico da identificação, versão, modelo, status e resumo geral. |
| `interacoes` | Alvo, evidência literal, processamento de origem e resumo do card. |
| `interacoes_medicamentos` | Vínculos com os medicamentos, critério, fundamento e validade. |
| `bulas_interacoes_embeddings` | Chunks, vetores, modelo e controle da vetorização. |
| `processamentos_interacoes_vigentes` | Conclusão compatível com a fonte atual e não retirada. |
| `processamentos_interacoes_registrados` | Último resultado compatível usado no controle de retomada, inclusive revisão. |

Reprocessar a identificação preserva seu histórico. Uma tentativa antiga em
revisão pode continuar registrada mesmo após uma conclusão posterior.
Os resumos, por sua vez, são atualizados nos próprios registros e não mantêm
histórico de versões do texto quando substituídos explicitamente.

## Executar o sistema

### Requisitos e banco

O ambiente verificado usa Python 3.13, Node.js 22 e PostgreSQL 18. A extensão
pgvector fica na etapa local de vetorização; não é necessária para restaurar o
backup de distribuição nem para as consultas atuais da API.
Instale as dependências de cada componente pelo seu `requirements.txt` ou
`package-lock.json`; modelos e ambientes virtuais não fazem parte do Git.

O [backup de distribuição](backups_banco/README.md) contém o esquema e os dados
dos módulos publicados, incluindo os resumos existentes no horário do snapshot.
Seu guia informa a data, o hash e os comandos para restaurar em outro banco.
A restauração foi conferida em banco isolado, incluindo consultas pela API.
PDFs, modelos, credenciais e diagnósticos locais não estão no backup.

Para construir uma base do zero, siga os guias de coleta e extração e aplique
explicitamente os SQLs necessários. As migrações da identificação ficam em
`pipelines/identificacao_interacoes/migracoes/`; a API não cria nem migra tabelas.

### API

Na raiz do projeto, no PowerShell:

```powershell
py -3.13 -m venv apps/api_interacoes/.venv
.\apps\api_interacoes\.venv\Scripts\python.exe -m pip install -r apps/api_interacoes/requirements.txt
Copy-Item apps/api_interacoes/.env.example apps/api_interacoes/.env
```

Configure `DATABASE_URL` em `apps/api_interacoes/.env` para o banco restaurado
ou preparado. Em seguida:

```powershell
.\apps\api_interacoes\.venv\Scripts\python.exe -m apps.api_interacoes
```

A API usa por padrão `http://127.0.0.1:8000`. A documentação interativa fica em
`http://127.0.0.1:8000/docs` e o contrato em `/openapi.json`.

### Frontend

Em outro terminal:

```powershell
cd apps/frontend
npm.cmd ci
npm.cmd run dev
```

Abra `http://localhost:3000`. O endereço da API vem de `NEXT_PUBLIC_API_URL`,
com padrão `http://127.0.0.1:8000`. Para outro endereço, use o exemplo de
`.env.local` indicado no guia do frontend. Na exportação estática, esse valor
é definido durante o build.

### Processamento dos dados

As etapas de processamento são executadas separadamente da aplicação web.
Configure o `.env` e o ambiente de cada componente antes de iniciar seus lotes.
Exemplos pela raiz, usando o ambiente da extração já preparado:

```powershell
# Identificação: simulação sem gravar
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pipelines.identificacao_interacoes --simular --todos

# Identificação: gravar a seleção conforme a versão e os controles do módulo
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pipelines.identificacao_interacoes --todos

# Resumos: preencher somente as pendências
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -u -m pipelines.resumos_interacoes --todos
```

Os guias internos detalham a criação dos ambientes, as estratégias, os filtros
de seleção e as opções de retomada, correção e reprocessamento.

## Endpoints de consulta

| Método e caminho | Função |
| --- | --- |
| `GET /api/saude` | Confere conexão e esquema de consulta. |
| `GET /api/medicamentos/sugestoes?q=&limite=` | Sugestões de nomes cadastrados. |
| `GET /api/interacoes?medicamento=&pagina=&tamanho_pagina=` | Interações da própria bula, resumo geral e paginação. |
| `GET /api/interacoes/par?medicamento_a=&medicamento_b=` | Evidências e estados de análise dos dois medicamentos. |

Os parâmetros das consultas de interações usam os identificadores normalizados
fornecidos pelas sugestões. Nome inexistente retorna HTTP 404. Alertas de classes
e menções sem vínculo não confirmam automaticamente uma interação específica.

## Verificações

O GitHub Actions executa testes isolados dos pipelines e da API, valida os resumos
com provedor simulado e verifica lint, tipos e build do frontend. Esses testes
não exigem acesso real à Anvisa nem carregamento dos modelos para inferência.

Os testes de integração exigem `IDENTIFICACAO_TEST_DATABASE_URL` apontando para
um banco separado, cujo nome contenha `test`. Nunca use o banco de trabalho nessa
variável. A restauração do backup também foi verificada em um banco isolado.

Exemplos, com as dependências dos componentes instaladas:

```powershell
# Na raiz
python -m unittest discover -s pipelines/identificacao_interacoes/tests -t .
python -m unittest discover -s pipelines/resumos_interacoes/tests -t .
python -m unittest discover -s apps/api_interacoes/tests -t .

# Em apps/frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
```

## Limites da consulta

O sistema apresenta o que foi identificado nas fontes processadas. Falhas de
extração, nomes não reconhecidos e o escopo da estratégia utilizada podem limitar
a cobertura. Ausência de resultado não comprova ausência de interação.

Os resumos podem conter imprecisões mesmo após as validações automáticas.
Por isso, os trechos literais permanecem acessíveis para conferência. A interface
não atribui uma escala de gravidade clínica e não substitui orientação profissional.

A inclusão automática de medicamentos novos não faz parte da versão publicada.
A coleta depende do acesso permitido pelo Bulário; a aplicação continua
consultando os dados existentes quando o serviço externo estiver indisponível.

## Guias dos componentes

- [Scraping de bulas](pipelines/scraping_anvisa/README.md)
- [Extração e OCR](pipelines/extracao_interacoes/README.md)
- [Identificação, dicionário e vínculos](pipelines/identificacao_interacoes/README.md)
- [Resumos, validações e retomada](pipelines/resumos_interacoes/README.md)
- [Conversão em vetores](pipelines/conversao_interacoes_vetores/README.md)
- [API e contratos](apps/api_interacoes/README.md)
- [Interface web](apps/frontend/README.md)
- [Backup e restauração](backups_banco/README.md)
