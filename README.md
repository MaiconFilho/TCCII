# InteraMed — identificação de interações e consulta web

O projeto identifica interações descritas nas bulas, relaciona os medicamentos
citados e gera resumos locais para consulta por API e interface web.

O InteraMed é um projeto acadêmico e informativo. Reúne evidências das bulas
profissionais da Anvisa para ajudar a consultar interações entre medicamentos.
Os resultados preservam o texto e a origem da evidência; não substituem avaliação
profissional. Ausência de resultado não comprova ausência de interação.

## Como esta entrega funciona

1. A identificação lê o tópico já extraído em `bulas_interacoes`.
2. A estratégia padrão, `dicionario`, procura nomes de medicamentos e princípios
   ativos do cadastro. Aplica os filtros existentes para negações, exceções e
   menções que não representam uma relação positiva.
3. Cada relação preserva um recorte literal como evidência. As estratégias
   opcionais `atual` e `duas_etapas` usam o modelo local com validação das respostas.
4. A resolução associa o alvo textual aos medicamentos do cadastro pelas regras
   existentes de nome, princípio ativo e equivalência declarada.
5. A etapa opcional [resumos_interacoes](pipelines/resumos_interacoes/README.md)
   gera resumos locais com LLM: um por registro e um geral de 2 a 3 parágrafos por bula.
6. A API consulta resultados vigentes. O frontend permite consultar um medicamento
   ou todos os pares de uma seleção, consolidando as evidências de cada par.

A reorganização em monorepo muda caminhos e imports; não modifica esses critérios.

## Tabelas da identificação

| Tabela ou visão | Responsabilidade |
| --- | --- |
| `processamentos_interacoes` | Histórico de tentativas, fonte, versão, modelo, status, contagens e tempos. |
| `interacoes` | Alvo citado, tipo, resumo, evidência e referência ao processamento que produziu a relação. |
| `interacoes_medicamentos` | Liga cada relação aos medicamentos correspondentes, com critério, fundamento e validade do vínculo. |
| `processamentos_interacoes_vigentes` | Última conclusão não retirada e compatível com o texto e a bula atuais; usada na consulta. |
| `processamentos_interacoes_registrados` | Último resultado compatível, incluindo revisão; usado para controlar a retomada. |

Reprocessar cria histórico. Uma linha antiga em `REVISAO_NECESSARIA` continua na
tabela mesmo quando existe uma conclusão posterior. Conclusão é um estado
operacional, não uma garantia de cobertura completa. A busca por dicionário não
resolve automaticamente as pendências de uma tentativa anterior com LLM.

## Arquitetura e fluxo completo

```text
Anvisa → scraping → PDFs e cadastro no PostgreSQL
       → extração de composição, princípios ativos e tópico de interações
       → identificação → relações e vínculos → API → frontend
                       ↘ vetorização dos textos com pgvector (etapa independente)
```

- **Scraping:** Python e Selenium consultam o Bulário, baixam PDFs e registram a coleta.
- **Extração:** PyMuPDF, OCR seletivo e LLM local leem as bulas e extraem os dados.
- **Identificação:** Python, regras de correspondência e estratégias opcionais com LLM.
- **Vetorização:** embeddings locais e PostgreSQL/pgvector preparam busca semântica;
  os vetores não participam da consulta atual da interface.
- **API:** FastAPI oferece sugestões, consulta individual, evidências e consulta de pares.
- **Frontend:** Next.js, React, TypeScript, Tailwind CSS e shadcn/ui.
- **Persistência:** PostgreSQL armazena cadastro, textos, processamentos, relações e vínculos.

## Estrutura

```text
TCCII/
├── apps/
│   ├── frontend/
│   └── api_interacoes/
├── pipelines/
│   ├── scraping_anvisa/
│   ├── extracao_interacoes/
│   ├── identificacao_interacoes/
│   ├── resumos_interacoes/
│   ├── conversao_interacoes_vetores/
│   └── caminhos.py
├── docs/
├── backups_banco/
├── .github/
├── .gitignore
└── README.md
```

Os arquivos `__init__.py` permitem importar `apps` e `pipelines` como pacotes.
`pipelines/caminhos.py` reconhece os caminhos antigos dos arquivos que foram
movidos, preservando os valores já registrados no banco.

## Executar

Execute os módulos a partir da raiz `TCCII`. Configure cada ambiente Python com o
`requirements.txt` do respectivo módulo e crie seu `.env` a partir de `.env.example`.
Os ambientes locais existentes foram movidos junto com seus módulos. Use
`python -m pip` pelo executável do ambiente para instalar dependências.

Exemplo no PowerShell, usando os ambientes locais existentes:

```powershell
# Conferir opções, sem processar nem alterar o banco
.\pipelines\scraping_anvisa\.venv\Scripts\python.exe -m pipelines.scraping_anvisa --help
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pipelines.extracao_interacoes --help
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pipelines.identificacao_interacoes --help

# Identificação: simulação sem gravação
.\pipelines\extracao_interacoes\.venv\Scripts\python.exe -m pipelines.identificacao_interacoes --simular --todos

# API de consulta
.\apps\api_interacoes\.venv\Scripts\python.exe -m apps.api_interacoes

# Frontend, em outro terminal
cd apps\frontend
npm ci
npm run dev
```

A vetorização usa `python -m pipelines.conversao_interacoes_vetores` com as
dependências próprias instaladas. A criação do banco fica em
[pipelines/scraping_anvisa/criar_banco.sql](pipelines/scraping_anvisa/criar_banco.sql).
As migrações da identificação são explícitas; a API não cria nem migra o banco.

## Documentação por componente

- [Scraping](pipelines/scraping_anvisa/README.md)
- [Extração](pipelines/extracao_interacoes/README.md)
- [Identificação, estratégias e tabelas](pipelines/identificacao_interacoes/README.md)
- [Resumos locais, validações e retomada](pipelines/resumos_interacoes/README.md)
- [Vetorização](pipelines/conversao_interacoes_vetores/README.md)
- [API e contratos](apps/api_interacoes/README.md)
- [Frontend](apps/frontend/README.md)
- [Guia da reorganização](docs/reorganizacao.md)

## Verificações e limites

O workflow executa testes isolados dos pipelines, dos resumos e da API, além de lint,
checagem de tipos e build do frontend. Testes PostgreSQL exigem um banco de testes
isolado; nunca devem usar o banco de trabalho.

PDFs ausentes ou sem seção, falhas de leitura e nomes não reconhecidos limitam a
cobertura. A interface não atribui gravidade clínica. A coleta depende da disponibilidade
e das condições de acesso ao Bulário da Anvisa; falha de acesso não significa
inexistência do medicamento.

Ambientes, credenciais, PDFs locais, modelos, caches, relatórios e backups não
devem ser versionados. Consulte os READMEs internos para requisitos e comandos
que efetivamente gravam resultados.
