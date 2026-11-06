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

## Banco

O modelo aprovado tem **cinco tabelas de domínio e exatamente 60 campos**:

| Tabela | Campos | Responsabilidade |
| --- | ---: | --- |
| `bulas` | 9 | Cadastro, composição, princípios ativos e controle da coleta inicial. |
| `bulas_interacoes` | 7 | Tópico extraído, estado, revisão e tempos. |
| `interacoes` | 16 | Menção, evidência, processamento e resumos do card. |
| `interacoes_medicamentos` | 4 | Medicamentos relacionados, critério e invalidação. |
| `processamentos_interacoes` | 24 | Identificação, métricas, histórico e resumos gerais. |

O [SQL completo de criação](banco/modelo.sql) é a referência da instalação nova. As seis FKs são NOT NULL; a PK da ligação é `(interacao_id, medicamento_relacionado)`. Triggers verificam a coerência das origens da menção e do processamento, sem adicionar outra FK. A pasta `banco/` contém somente esse SQL; a API lê dele a estrutura esperada, sem executar DDL.

As views de vigência e de registros usam os campos mantidos. A conclusão utilizável é a de maior ID, não invalidada, com extração concluída e texto. Falha ou revisão posterior preserva a conclusão válida anterior. A view de registrados inclui revisão para evitar duplicação na retomada. Resultado negativo exige análise concluída e utilizável.

Uma correção excepcional da fonte exige retirar explicitamente seus processamentos antes de alterar texto, registro ou expediente. Hashes persistidos de versões de PDF/fonte não integram o modelo. Hashes dos controles de reescrita e verificações em memória durante inferência continuam legítimos. Publicações simples antigas foram validadas na migração; seus textos e JSONBs permanecem preservados. Alterações posteriores de entrada invalidam a publicação até uma nova reescrita válida.

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
| `banco/modelo.sql` | Criação completa das cinco tabelas e seus objetos de apoio. |
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
# Criar um banco PostgreSQL novo e vazio:
createdb -h localhost -U postgres interamed
psql -h localhost -U postgres -d interamed -v ON_ERROR_STOP=1 --single-transaction -f banco/modelo.sql
```

**Não execute o SQL de criação sobre uma base existente.** Uma atualização exige backup e migração explícita validada em cópia isolada. O Supabase do site já foi readequado; não é necessário repetir a migração. O executor `python -m pipelines.identificacao_interacoes.aplicar_migracoes` cria somente uma instalação vazia ou verifica o modelo atual; não reaplica os SQLs históricos.

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

## Verificações

```powershell
python -m compileall -q apps pipelines
# Em apps/frontend:
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
```

A CI também cria o schema em PostgreSQL isolado e verifica saúde, sugestões, consultas individuais e de pares, evidências e CORS. Não executa scraping nem inferência real. Os arquivos de testes que já existiam foram mantidos sem as alterações desta readequação; eles ainda contêm expectativas do contrato anterior e não são apresentados como uma suíte compatível com o modelo atual.

## Operação e limites

O Supabase foi migrado, a API 2.0.0 e o frontend foram publicados e o [backup final](backups_banco/README.md) foi extraído do banco readequado e restaurado em ambiente isolado. Todos os valores mantidos foram preservados, incluindo 4.512 cards e 286 resumos gerais simples Smol. Relatórios, manifestos completos e ferramentas da operação ficam locais; não são dependências do sistema.

O sistema mostra o que foi identificado nas fontes processadas; cobertura incompleta e nomes não resolvidos limitam os resultados. Os resumos podem ter imprecisões: mantenha a conferência dos trechos literais. A aplicação não classifica gravidade clínica e não substitui orientação profissional.

Artigo e diagrama aprovado não fazem parte desta alteração. Antes de substituir um backup ou publicar outra alteração de schema, valide os dados preservados e a leitura da API em banco isolado.
