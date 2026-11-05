# InteraMed — consulta de interações medicamentosas

Projeto acadêmico que consulta resultados persistidos das bulas profissionais da Anvisa. A API e a interface web não executam scraping, download, leitura de PDF ou inferência durante uma pesquisa. As bulas existentes compõem uma **base fixa**; as etapas offline podem concluir cadastro, extração, identificação, resumos e reescritas pendentes, sem atualizar automaticamente fontes concluídas.

## Funcionalidades

- Consulta individual com resumo geral, cards, paginação e evidências literais, inclusive menções válidas sem medicamento relacionado.
- Seleção de vários medicamentos, com todos os pares únicos e busca nos dois sentidos de cada par.
- Agrupamento das evidências e acesso aos trechos originais persistidos.
- Resumos originais e simples separados, com seus controles e proveniência.
- Fontes identificadas pelo medicamento, registro e expediente de `bulas`.
- Estados explícitos de pendência, revisão, falha e retirada. Ausência de dados não vira conclusão negativa.
- Interface responsiva e navegação por teclado nas sugestões.

Nome ausente não inicia coleta ou preparação automática. A explicação de vínculo mostra o alvo e o critério gravados; citar um componente não atribui a evidência aos demais componentes da formulação. Datas de processamento não são apresentadas como publicação da bula.

## Modelo canônico

O modelo aprovado tem **cinco tabelas de domínio e exatamente 60 campos**:

| Tabela | Campos | Responsabilidade |
| --- | ---: | --- |
| `bulas` | 9 | Cadastro, composição, princípios ativos e controle da coleta inicial. |
| `bulas_interacoes` | 7 | Tópico extraído, estado, revisão e tempos. |
| `interacoes` | 16 | Menção, evidência, processamento e resumos do card. |
| `interacoes_medicamentos` | 4 | Medicamentos relacionados, critério e invalidação. |
| `processamentos_interacoes` | 24 | Identificação, métricas, histórico e resumos gerais. |

O [SQL canônico](banco/modelo.sql) e o [contrato de campos e tipos](banco/modelo.json) são a referência da instalação nova. As seis FKs são NOT NULL; a PK da ligação é `(interacao_id, medicamento_relacionado)`. Triggers verificam a coerência das origens da menção e do processamento, sem adicionar outra FK.

As views de vigência e de registros usam os campos mantidos. A conclusão utilizável é a de maior ID, não invalidada, com extração concluída e texto. Falha ou revisão posterior preserva a conclusão válida anterior. A view de registrados inclui revisão para evitar duplicação na retomada. Resultado negativo exige análise concluída e utilizável.

Uma correção excepcional da fonte exige retirar explicitamente seus processamentos antes de alterar texto, registro ou expediente. Hashes persistidos de versões de PDF/fonte não integram o modelo. Hashes dos controles de reescrita e verificações em memória durante inferência continuam legítimos; veja a compatibilidade e preservação dos JSONBs no [guia do banco](banco/README.md).

## Componentes

```text
Coleta inicial da Anvisa → PDFs locais e cadastro
    → extração: composição, princípios ativos e tópico de interações
    → identificação: menções, evidências e vínculos determinísticos
    → resumos originais e reescritas simples
    → PostgreSQL → API somente de leitura → frontend estático
```

| Diretório | Uso |
| --- | --- |
| `banco/` | Baseline, diagnóstico, migração explícita, backup e restauração. |
| `apps/api_interacoes/` | FastAPI/Pydantic, consultas parametrizadas e pool somente de leitura. |
| `apps/frontend/` | Next.js/React/TypeScript, cards, comparações e evidências. |
| `pipelines/scraping_anvisa/` | Coleta inicial por Selenium/API do Bulário. |
| `pipelines/extracao_interacoes/` | Leitura, OCR seletivo e extração local. |
| `pipelines/identificacao_interacoes/` | Dicionário, composição, resolução e estratégias locais opcionais. |
| `pipelines/resumos_interacoes/` | Geração, validação, persistência e retomada dos resumos. |
| `backups_banco/` | Guia do snapshot histórico e do backup remoto atualizado. |

A identificação preserva critérios por nome, princípio ativo, sais e equivalências declaradas na composição. Classes e alimentos podem permanecer sem vínculo. Os resumos são gravados em transações curtas; por padrão, textos prontos são preservados e apenas pendências são selecionadas. Modelos e PDFs não precisam existir no servidor da consulta.

Vetorizações e inclusão sob demanda são experimentos legados preservados fora do fluxo ativo. Seus pontos de entrada estão desativados; a API não importa seus repositórios nem inicia trabalhadores. A baseline não cria fila ou embeddings. Objetos legados independentes existentes não são apagados pela migração.

## Executar localmente

Use Python 3.12 ou superior, Node.js 20.9 ou superior e PostgreSQL 15 ou superior. Instale as dependências de cada componente conforme seus `requirements.txt`/`package-lock.json`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps/api_interacoes/requirements.txt
# Banco PostgreSQL vazio, conexão em arquivo separado:
.\.venv\Scripts\python.exe -m banco.operacao inicializar --env caminho/banco-local.env
```

Para base existente, siga [o procedimento de diagnóstico, cópia, migração, backup e implantação](banco/README.md). **Não** execute a baseline sobre a base existente. Os SQLs históricos 001–007 não são reaplicados pelo executor final.

Configure `apps/api_interacoes/.env` para o banco preparado e inicie:

```powershell
.\.venv\Scripts\python.exe -m apps.api_interacoes
# Em outro terminal:
cd apps/frontend
npm.cmd ci
npm.cmd run dev
```

A API usa `http://127.0.0.1:8000`, com `/docs` e `/openapi.json`; o frontend abre em `http://localhost:3000`. `NEXT_PUBLIC_API_URL` define a API do navegador e participa do **build estático**. CORS precisa permitir a origem exata do frontend. Não inclua conexão PostgreSQL ou chave Supabase em variáveis públicas. Os `.env` locais podem permanecer separados da configuração remota de migração.

Para os pipelines, configure `INTERAMED_PDF_DIR` se os PDFs estiverem fora de `pipelines/scraping_anvisa/pdfs`. A resolução usa o nome real produzido pelo coletor, como `ablok_plus_109740092_profissional.pdf`; nenhum caminho de PDF é lido do banco. A disponibilidade real do scraping depende do Bulário e deve ser verificada separadamente.

## API de consulta — versão 2.0.0

| Método e caminho | Função |
| --- | --- |
| `GET /api/saude` | Conexão, versão e schema de consulta. |
| `GET /api/medicamentos/sugestoes?q=&limite=` | Sugestões cadastradas. |
| `GET /api/interacoes?medicamento=&pagina=&tamanho_pagina=` | Consulta individual e resumo geral. |
| `GET /api/interacoes/par?medicamento_a=&medicamento_b=` | Evidências nos dois sentidos e estados das bulas. |

As respostas não incluem hashes de PDF, datas de publicação, links de PDF fictícios ou fundamentos de vínculo removidos. A interface mantém acesso às evidências e consulta no Bulário com a identificação disponível.

## Testes

```powershell
python -m unittest discover -s pipelines/scraping_anvisa/tests
python -m unittest discover -s pipelines/extracao_interacoes/tests
python -m unittest discover -s pipelines/identificacao_interacoes/tests -t .
python -m unittest discover -s pipelines/resumos_interacoes/tests -t .
python -m unittest discover -s apps/api_interacoes/tests -t .
python -m unittest discover -s banco/tests -t .
# Em apps/frontend:
npm.cmd test
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
```

Configure `IDENTIFICACAO_TEST_DATABASE_URL` e `MODELO60_TEST_DATABASE_URL` somente com banco local isolado contendo `test` no nome. `MIGRACAO60_TEST_DATABASE_URL` deve apontar para **outro banco local descartável**: sua suíte recria o schema para testar o legado. Nunca use `TCC` ou Supabase nessas variáveis. No Windows, `PYTHONUTF8=1` permite registrar logs em UTF-8. Os testes usam dados/provedores simulados, sem coleta ou inferência real.

## Operação e limites

O [guia do banco](banco/README.md) define backup diretamente do Supabase, ensaio isolado, preservação por PK, implantação coordenada e restauração final. O Supabase foi migrado, a API 2.0.0 e o frontend foram publicados e o [backup final](backups_banco/README.md) foi extraído do banco readequado e restaurado em ambiente isolado. O [relatório](banco/RELATORIO_READEQUACAO.md) e as [evidências](banco/VALIDACAO_SUPABASE.json) registram os resultados e a limitação da conferência visual. Todos os valores mantidos foram preservados, incluindo 4.512 cards e 286 resumos gerais simples Smol. Sem commit ou push.

O sistema mostra o que foi identificado nas fontes processadas; cobertura incompleta e nomes não resolvidos limitam os resultados. Os resumos podem ter imprecisões: mantenha a conferência dos trechos literais. A aplicação não classifica gravidade clínica e não substitui orientação profissional.

Artigo e diagrama aprovado não fazem parte desta alteração. Não faça commit/push antes da revisão e validações remotas. A mensagem prevista é “Ajusta banco de dados e atualiza backup”, somente depois de o backup real ter sido gerado e validado.
