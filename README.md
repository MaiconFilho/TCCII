# TCC II — Coleta e extração de bulas da Anvisa

O projeto coleta bulas profissionais da Anvisa e extrai seu tópico de interações
medicamentosas para uma base PostgreSQL rastreável.

## Etapas nesta branch

1. **Coleta:** o scraper lê a planilha de medicamentos, consulta o Bulário com
   Selenium, seleciona a bula profissional pela data de publicação, baixa os
   PDFs e registra o controle da execução no PostgreSQL.
2. **Extração:** o PyMuPDF lê os PDFs, aplica OCR seletivo quando necessário e
   identifica o tópico de interações. Uma LLM local confirma a seção e seus
   limites; o texto completo é copiado para `bulas_interacoes`.
3. **Validação e retomada:** testes, diagnóstico de pendentes e reprocessamento
   seletivo permitem verificar resultados e tentar novamente falhas.

A etapa de extração mantém um registro por `nome_normalizado`, com status e
tempos em segundos. O modelo padrão é Qwen2.5 1.5B Instruct GGUF Q4_K_M,
executado em CPU por `llama-cpp-python`. O modo rápido confirma contextos das
bordas da seção e preserva integralmente o texto intermediário.

## Organização

```text
.github/workflows/tests.yml  testes automatizados
scraping_anvisa/             coleta, planilha e controle dos downloads
extracao_interacoes/         módulos Python, OCR, LLM e persistência
  tests/                    testes isolados e regressões
  main.py                   processamento individual ou em lote
  preparar_ocr.py            preparação do idioma português
  reprocessar_pendentes.py   segunda tentativa seletiva
  diagnosticar_pendentes.py  diagnóstico sem gravar no banco
  criar_tabela.sql           esquema de bulas_interacoes
  .env.example              configuração de exemplo
  README.md                 instalação, funcionamento e comandos
```

## Começar

- [Instalar e executar o scraper](scraping_anvisa/README.md).
- [Instalar, executar e testar a extração](extracao_interacoes/README.md).
- [Consultar o esquema de extração](extracao_interacoes/criar_tabela.sql).

Prepare primeiro o banco e os PDFs pela etapa de coleta. Depois configure o
`.env` da extração e comece com um medicamento, conforme o passo a passo do módulo.

## Dados e escopo

A planilha de entrada é versionada. PDFs baixados, bancos, credenciais, modelos
locais, caches e relatórios de execução ficam fora do Git.

Esta branch contém coleta e extração de texto. A conversão em vetores é uma
etapa separada. Os resultados da extração exigem validação documental antes de
serem usados em aplicações clínicas; ausência de um tópico reconhecido não
representa ausência de interações medicamentosas.
