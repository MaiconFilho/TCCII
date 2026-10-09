# InteraMed — interações medicamentosas em bulas da Anvisa

O projeto coleta bulas profissionais da Anvisa, extrai composição, princípios
ativos e interações, identifica relações com evidências rastreáveis e oferece
consultas pela API e pela interface web. A vetorização prepara uma busca
semântica futura e não participa da consulta atual da interface.

## Etapas nesta branch

1. **Coleta:** o scraper lê a planilha de medicamentos, consulta o Bulário com
   Selenium, seleciona a bula profissional pela data de publicação, baixa os
   PDFs e registra o controle da execução no PostgreSQL.
2. **Extração:** o PyMuPDF lê os PDFs, aplica OCR seletivo quando necessário e
   identifica o tópico de interações. Uma LLM local confirma a seção e seus
   limites; o texto completo é copiado para `bulas_interacoes`.
   A mesma leitura e o mesmo modelo extraem os princípios ativos e a composição
   para os campos `principios_ativos` e `composicao` da tabela `bulas`.
3. **Validação e retomada:** testes, diagnóstico de pendentes e reprocessamento
   seletivo permitem verificar resultados e tentar novamente falhas.
4. **Vetorização:** cada `trecho_interacoes` é dividido em chunks por tokens e
   convertido em embeddings por um modelo local em CPU. Os vetores normalizados
   são gravados em `bulas_interacoes_embeddings` com pgvector.
5. **Identificação das interações:** a estratégia padrão usa um dicionário
   derivado dos nomes e princípios ativos cadastrados. Ela encontra menções no
   texto, descarta negações e outras citações sem relação positiva e preserva
   trechos literais como evidência. Também existem estratégias opcionais com
   LLM local e validação. Os alvos são ligados aos medicamentos por regras de
   correspondência e gravados em `interacoes` e `interacoes_medicamentos`, com
   histórico em `processamentos_interacoes`.
6. **API de consulta:** FastAPI somente leitura com autocomplete, interações
   por medicamento e relações entre dois medicamentos, com estados explícitos
   para análises pendentes, desatualizadas ou incompletas.
7. **Interface:** Next.js e React permitem pesquisar um medicamento ou verificar
   as interações registradas entre vários medicamentos, mostrando as fontes e
   os trechos de bula que fundamentam os resultados.

A etapa de extração mantém um registro por `nome_normalizado`, com status e
tempos em segundos. O modelo padrão é Qwen2.5 1.5B Instruct GGUF Q4_K_M,
executado em CPU por `llama-cpp-python`. O modo rápido confirma contextos das
bordas da seção e preserva integralmente o texto intermediário.

A vetorização usa `intfloat/multilingual-e5-small` (384 dimensões, licença MIT)
por Transformers em CPU, com carregamento único do modelo e inferência serial.
Textos longos são divididos com sobreposição e sem truncamento; um hash estável
de nome, chunk, texto e modelo evita reprocessar o que não mudou.
`bulas_interacoes` permanece intacta como fonte textual oficial: o vetor nunca
substitui o trecho. A busca por similaridade não faz parte desta etapa.

## Organização

```text
frontend/                     interface web InteraMed em React/Next.js
.github/workflows/tests.yml  testes automatizados
scraping_anvisa/             coleta, planilha e controle dos downloads
extracao_interacoes/         módulos Python, OCR, LLM e persistência
  tests/                    testes isolados e regressões
  main.py                   processamento individual ou em lote
  preparar_ocr.py            preparação do idioma português
  reprocessar_pendentes.py   segunda tentativa seletiva
  diagnosticar_pendentes.py  diagnóstico sem gravar no banco
  .env.example              configuração de exemplo
  README.md                 instalação, funcionamento e comandos
conversao_interacoes_vetores/  chunking, embeddings e persistência com pgvector
  tests/                    testes isolados, sem modelo, GPU ou banco reais
  main_embeddings.py        vetorização individual ou em lote
  criar_tabela.sql          esquema de bulas_interacoes_embeddings e índice HNSW
  consultar_vetores.sql     consultas de conferência dos vetores
  .env.example              configuração de exemplo
  README.md                 modelos avaliados, chunking, hash e comandos
identificacao_interacoes/    relações estruturadas, validação, vínculos e versões
  migracoes/                migrações aditivas das novas tabelas e vínculos
  main_identificacao.py     CLI (python -m identificacao_interacoes)
  README.md                 regras, tabelas e comandos
api_interacoes/              API FastAPI de consulta (python -m api_interacoes)
  README.md                 endpoints, contratos e integração com o frontend
```

## Começar

- [Instalar e executar o scraper](scraping_anvisa/README.md).
- [Instalar, executar e testar a extração](extracao_interacoes/README.md).
- [Consultar o esquema de extração](scraping_anvisa/criar_banco.sql).
- [Instalar, executar e testar a vetorização](conversao_interacoes_vetores/README.md).
- [Consultar o esquema dos vetores](conversao_interacoes_vetores/criar_tabela.sql).
- [Identificar as interações e aplicar a migração](identificacao_interacoes/README.md).
- [Consultar o esquema das interações](identificacao_interacoes/migracoes/001_criar_interacoes.sql).
- [Executar e consultar a API](api_interacoes/README.md).
- [Executar a interface web](frontend/README.md).

Prepare primeiro o banco e os PDFs pela etapa de coleta. Depois configure o
`.env` da extração e comece com um medicamento, conforme o passo a passo do módulo.
A vetorização exige a extensão pgvector no PostgreSQL. A identificação depende
apenas da extração concluída; a API lê o que a identificação gravou. Os dois
novos módulos rodam a partir da raiz do projeto, como `python -m <módulo>`.
