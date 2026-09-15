# TCC II — Coleta, extração e vetorização de bulas da Anvisa

O projeto coleta bulas profissionais da Anvisa, extrai seu tópico de interações
medicamentosas para uma base PostgreSQL rastreável e converte esses trechos em
vetores para a busca semântica futura.

## Etapas nesta branch

1. **Coleta:** o scraper lê a planilha de medicamentos, consulta o Bulário com
   Selenium, seleciona a bula profissional pela data de publicação, baixa os
   PDFs e registra o controle da execução no PostgreSQL.
2. **Extração:** o PyMuPDF lê os PDFs, aplica OCR seletivo quando necessário e
   identifica o tópico de interações. Uma LLM local confirma a seção e seus
   limites; o texto completo é copiado para `bulas_interacoes`.
3. **Validação e retomada:** testes, diagnóstico de pendentes e reprocessamento
   seletivo permitem verificar resultados e tentar novamente falhas.
4. **Vetorização:** cada `trecho_interacoes` é dividido em chunks por tokens e
   convertido em embeddings por um modelo local em CPU. Os vetores normalizados
   são gravados em `bulas_interacoes_embeddings` com pgvector.

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
conversao_interacoes_vetores/  chunking, embeddings e persistência com pgvector
  tests/                    testes isolados, sem modelo, GPU ou banco reais
  main_embeddings.py        vetorização individual ou em lote
  criar_tabela.sql          esquema de bulas_interacoes_embeddings e índice HNSW
  consultar_vetores.sql     consultas de conferência dos vetores
  .env.example              configuração de exemplo
  README.md                 modelos avaliados, chunking, hash e comandos
```

## Começar

- [Instalar e executar o scraper](scraping_anvisa/README.md).
- [Instalar, executar e testar a extração](extracao_interacoes/README.md).
- [Consultar o esquema de extração](extracao_interacoes/criar_tabela.sql).
- [Instalar, executar e testar a vetorização](conversao_interacoes_vetores/README.md).
- [Consultar o esquema dos vetores](conversao_interacoes_vetores/criar_tabela.sql).

Prepare primeiro o banco e os PDFs pela etapa de coleta. Depois configure o
`.env` da extração e comece com um medicamento, conforme o passo a passo do módulo.
A vetorização é a última etapa e exige a extensão pgvector no PostgreSQL.
