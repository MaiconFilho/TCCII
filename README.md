# TCC II — Pipeline de bulas da Anvisa

Este repositório reúne o desenvolvimento de um pipeline para coleta e futura
análise de interações medicamentosas publicadas nas bulas profissionais da
Anvisa.

O objetivo é construir uma base rastreável a partir do Bulário Eletrônico,
preservando a relação entre o medicamento consultado, a bula selecionada e a
data de publicação informada pela Anvisa.

## Fluxo do sistema

```text
Planilha
→ Selenium
→ Bulário da Anvisa
→ PDFs
→ extração das interações medicamentosas
→ PostgreSQL
→ embeddings
→ pgvector
```

## Estado atual

| Etapa | Situação |
| --- | --- |
| Leitura e normalização da planilha de entrada | Concluída |
| Consulta ao Bulário com Selenium | Concluída |
| Seleção da bula profissional mais recente | Concluída |
| Download e validação dos PDFs | Concluída |
| Controle de execução e retomada no PostgreSQL | Concluído |
| Extração das interações medicamentosas | Implementada |
| Geração de embeddings | Planejada |
| Armazenamento vetorial com pgvector | Planejado |

Os PDFs são gerados localmente pelo scraper e não são versionados no Git. A
planilha utilizada como entrada permanece no repositório para permitir a
reprodução da coleta.

## Tecnologias

- Python 3.11 ou superior;
- Selenium e Google Chrome;
- openpyxl;
- PostgreSQL e psycopg;
- PyMuPDF, PyTorch e Transformers;
- Qwen3-4B-Instruct-2507 executado localmente;
- unittest para testes automatizados;
- GitHub Actions para integração contínua;
- pgvector, previsto para uma etapa futura.

## Estrutura do repositório

```text
.github/workflows/tests.yml   testes automatizados no GitHub Actions
scraping_anvisa/
  anvisa_scraper/             código-fonte do scraper
  dados/                      planilha de entrada versionada
  pdfs/                       saída local dos downloads
  tests/                      testes automatizados
  main.py                     entrada da aplicação
  requirements.txt            dependências Python
  README.md                   documentação técnica do scraper
extracao_interacoes/
  extracao_interacoes/       leitura, inferência, validação e persistência
  tests/                     testes isolados sem modelo ou banco reais
  main.py                    CLI da extração
  requirements.txt           dependências da extração local
  README.md                  documentação técnica da extração
```

## Etapa implementada

O scraper lê os nomes de medicamentos da planilha, consulta o Bulário da
Anvisa em uma sessão do Chrome, percorre os resultados, seleciona a bula
profissional com a data de publicação mais recente e salva o PDF localmente. O
PostgreSQL mantém o controle da coleta, dos arquivos e das tentativas.

O módulo de extração lê todas as páginas de cada PDF com PyMuPDF, envia o texto
integral ao modelo local `Qwen/Qwen3-4B-Instruct-2507`, valida a resposta e
grava a seção oficial em `bulas_interacoes`. A numeração da seção não é
presumida: o modelo utiliza título, contexto, conteúdo e estrutura.

Embeddings, pgvector, busca semântica, API, interface e análise clínica ainda
não fazem parte desta implementação.

Para instalar, configurar, executar e testar o scraper, consulte a
[documentação técnica](scraping_anvisa/README.md).

Para executar a etapa de extração, consulte a
[documentação de interações medicamentosas](extracao_interacoes/README.md).
