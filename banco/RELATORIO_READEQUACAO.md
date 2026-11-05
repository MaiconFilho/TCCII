# Relatório da readequação do InteraMed — 05/11/2026

**Supabase migrado, API 2.0.0 e frontend publicados, backup final restaurado e validado.**
Não houve stage, commit nem push. Artigo, diagrama e migrações históricas 001–007
foram preservados. Os `.env` locais anteriores não foram substituídos; a conexão
privada de operação ficou em `apps/api_interacoes/.env.supabase`, fora do Git.

## Implementado

- Baseline com cinco tabelas/60 campos, cinco PKs (uma composta), seis FKs NOT NULL,
  índices e views com `security_invoker=true`. SQL canônico: [modelo.sql](modelo.sql).
- Migração explícita `modelo60/v1`: diagnóstico, lock, conferência por PK,
  preservação dos campos mantidos e avanço seguro das sequências. Não apaga linhas
  nem resolve associações com pais arbitrários. RLS/políticas/ACLs existentes preservados.
- Fonte fixa, seleção determinística do maior ID concluído e não invalidado, com
  extração concluída e texto. Falha/revisão posterior não esconde conclusão válida.
  Ausência de extração/processamento/menções/vínculos não vira conclusão negativa.
- Triggers protegem a fonte fixa e a coerência de origem entre menção e processamento.
- API somente de leitura, sem DDL no startup, scraping, inferência ou fila. Fontes
  usam registro e expediente de `bulas`; dados obsoletos saíram do contrato HTTP.
- Frontend mantém navegação e layout; consultas individuais, pares, seleção múltipla,
  cards, resumos e evidências usam o contrato novo. Componentes de formulações só
  recebem atribuição quando ela é comprovável pelo texto citado.
- Pipelines adaptados aos campos mantidos e PDFs por diretório configurável com
  nomes reais. Coleta concluída não é atualizada automaticamente. Inclusão e
  vetorização experimentais ficaram fora do fluxo e do schema final.
- Reescritas antigas foram validadas antes do DROP sem alterar JSONBs/textos.
  Novas entradas usam `linguagem-simples/37`. Alterações de entrada invalidam a
  publicação; falha posterior não limpa essa marca. Hashes transitórios em memória
  e controles legítimos de reescrita não recriam as colunas de fonte retiradas.

## Arquivos principais

| Arquivos | Responsabilidade |
| --- | --- |
| `banco/modelo.sql`, `modelo.json`, `objetos.sql` | Contrato e baseline. |
| `banco/operacao.py`, `compatibilidade.py`, `validar_copia.py` | Diagnóstico, migração, backup e restauração. |
| `apps/api_interacoes/` | SQL, serviços, contratos e manutenção temporária. |
| `apps/frontend/lib/`, `components/interamed.tsx` | Consumidores e apresentação. |
| Repositórios dos quatro pipelines ativos e `pipelines/caminhos.py` | Persistência e resolução de PDFs. |
| Testes e `.github/workflows/tests.yml` | Regressões e integração PostgreSQL. |
| [VALIDACAO_SUPABASE.json](VALIDACAO_SUPABASE.json) | Evidências resumidas sem credenciais. |

## Aplicação no Supabase e preservação

Destino confirmado: projeto `vuowgvmmqbwfhccncxvg`, banco `postgres`, PostgreSQL
17.11, pooler de sessão de São Paulo. Os cards 1387/1388 de Aerolin e processamento
124 retornados pela API anterior foram conferidos diretamente nesse Supabase.
O schema `public` tinha somente as cinco tabelas da aplicação. Todos os problemas
de órfãos, nulos nas FKs, duplicidades e divergências de origem eram zero.

A primeira aplicação foi bloqueada antes do DDL porque um lote Smol ainda gravava.
Site/API anteriores foram restabelecidos enquanto se aguardava a pausa. Após o
usuário confirmar a pausa, gerou-se novo backup de segurança, restaurado e ensaiado
em banco isolado. A API respondeu 503 durante a troca. A migração foi então aplicada
em transação e comparou todos os campos mantidos por PK antes/depois, sem diferenças.

| Tabela | Registros preservados |
| --- | ---: |
| `bulas` | 5895 |
| `bulas_interacoes` | 5867 |
| `processamentos_interacoes` | 5783 |
| `interacoes` | 67351 |
| `interacoes_medicamentos` | 555908 |

Foram preservados **4.512 cards simples e 286 resumos gerais simples Smol**, além
de todos os originais, evidências, versões, status, métricas e JSONBs. O lote terminou
mais um resumo geral entre o primeiro backup e a pausa; o backup definitivo inclui
esse resultado. Modelo publicado: `SmolLM3-Q4_K_M.gguf`, repositório
`ggml-org/SmolLM3-3B-GGUF`, revisão `4965cb60b150737b68a0408c36aeefb65078f894`.

Os digests preservam JSONB com precisão numérica integral, distinguem JSON null de
SQL NULL, normalizam TIMESTAMPTZ pelo instante e conferem FLOAT8 pelos bits. A
comparação cobre todos os registros, sem depender de filtrar apenas pelo nome Smol.
As sequências restauradas também foram conferidas contra os maiores IDs, sem
consumir IDs nem inserir registros em produção.

## Testes

| Comando/suíte | Resultado |
| --- | --- |
| `python -m unittest discover -s banco/tests -t .` | 16 passaram, PostgreSQL real. |
| `python -m unittest discover -s apps/api_interacoes/tests -t .` | 48 passaram, PostgreSQL real habilitado. |
| `python -m unittest discover -s pipelines/identificacao_interacoes/tests -t .` | 175 passaram. |
| `python -m unittest discover -s pipelines/resumos_interacoes/tests -t .` | 254 passaram. |
| `python -m unittest discover -s pipelines/extracao_interacoes/tests` | 242 passaram. |
| `python -m unittest discover -s pipelines/scraping_anvisa/tests` | 21 passaram. |
| Inclusão experimental | 23 passaram; 8 PostgreSQL legados ignorados. |
| Vetorizações experimentais, dentro do módulo: `python -m unittest discover -s tests` | 142 passaram. |
| Frontend: `npm.cmd test` | 3 passaram. |
| Frontend: `npm.cmd run lint`, `typecheck`, `build` | Passaram. |
| Quatro CLIs ativas com `--help` | Carregaram sem executar lotes. |

São 756 testes Python do fluxo ativo, mais três do frontend; suítes experimentais
foram verificadas separadamente. Ambiente `.venv_readequacao`, Python 3.12.10,
PostgreSQL local 17.5 e Next.js 16.3.8. Provedores foram simulados: **não houve
scraping, download de bulas, inferência real ou execução de lotes** nesta tarefa.

## Publicação e consultas reais

- Site: https://interamed-web.vercel.app — HTTP 200, sete scripts JavaScript 200,
  com `NEXT_PUBLIC_API_URL=https://interamed-api.vercel.app` no build estático.
- API: https://interamed-api.vercel.app — saúde 2.0.0, banco/schema `ok`, CORS
  da origem do site correto; sem rotas da fila legada e sem manutenção ativa.
- Deployment API: `dpl_4TAA8cm6jiqkCqJLdu4ogH5qNATL`.
- Deployment frontend: `dpl_BYjghLd2fRDqeYcBibaHkw6FUyEP`.
- Aerolin, Darunavir, AAS e Marevan foram consultados publicamente; originais,
  cards, simples, gerais, evidências e fontes retornados foram comparados por ID.
  Sete cards simples foram conferidos nessa amostra. Aerolin/Darunavir e seu par
  ficaram idênticos ao estado anterior, exceto pelos campos HTTP retirados.
- Sugestões, paginação, seis combinações de pares para seleção múltipla e o par
  positivo AAS/Fauldmetro nos dois sentidos foram conferidos. Nesse par, os IDs
  566, 567 e 24050 e suas evidências coincidiram com os dados persistidos.
- Saúde, consultas, CORS e leitura dos resumos também passaram sobre a restauração
  final em banco isolado.

**Limitação concreta:** a conferência visual interativa não foi executada porque
o host do navegador integrado permanece indisponível e o acesso anterior ao
Chrome não estava autorizado. HTTP/build/testes não são apresentados como teste
visual de navegação, modais ou responsividade.

## Backups

Backup de segurança após a pausa: `tmp\operacao\supabase\seguranca-pausada\interamed_seguranca_20261105T205457Z.dump`.
Instante UTC: `20261105T205457Z`. SHA-256:
`a87f5d74ceb9128aa8fe696b5f904ac0551dc93014dcd9c6680049ea62c0770a`. Restauração/ensaio:
`tmp/operacao/supabase/copia-pausada-validada.json`.

Backup final extraído **diretamente do Supabase já readequado**:

- Origem: projeto `vuowgvmmqbwfhccncxvg`, banco `postgres`, schema `public`.
- Instante: **05/11/2026 21:04:33 UTC**, **05/11/2026 18:04:33 (São Paulo)**.
- Fonte operacional: `tmp\operacao\supabase\final\interamed_final_20261105T210433Z.dump`.
- Candidato à revisão: [`backups_banco/interamed_atual.dump`](../backups_banco/interamed_atual.dump).
- Tamanho: **20237485 bytes**.
- SHA-256: `d13570d737b3ce8424273015c5a37d236e63baab4d17b3569d44bb5dd271e6bb`.
- Restaurado em `interamed_test_supa_restauracao_final_20261105_202613` e conferido
  por PK/schema/API, controles Smol e sequências. Os digests do backup final são
  iguais aos do backup de segurança em todos os campos mantidos.

O inventário/SQL e os dados foram revisados: somente as cinco tabelas públicas da
aplicação, sem objetos auth/storage/vault, sem senha configurada, URLs com senha,
chaves privadas, tokens nos padrões verificados ou CPFs formatados. O arquivo tem
menos de 100 MiB. Essa busca complementa o inventário; não é uma garantia genérica
de detecção de todo dado sensível. Credenciais e manifestos completos permanecem
ignorados pelo Git. O histórico original foi guardado separadamente em
`tmp/operacao/supabase/historico/interamed_atual_20261021.dump`.
O backup que já estava rastreado no checkout (distinto da cópia histórica da
pasta principal) também foi preservado em `tmp/operacao/supabase/historico/interamed_atual_checkout_original.dump`.
Antes da substituição, seu blob coincidia com o HEAD, sem alterações locais.

## Revisão e operação posterior

A pasta principal não tem `.git`; o checkout de publicação é
`TCCII_publish_worktree`, branch `main`, inicialmente limpo. Fontes, documentação
e backup revisado foram sincronizados nesse checkout, sem stage, commit ou push.
Pacote de fontes: `tmp/readequacao/revisao-modelo60.zip`, sem dumps/credenciais.
Comandos reproduzíveis em [README.md](README.md) e [guia dos backups](../backups_banco/README.md).

Os lotes devem continuar pausados até que executem **o código atualizado**. Não
retome processos antigos que ainda esperam as colunas removidas. A execução real
dos pipelines permanece fora da validação autorizada. A única conferência de UI
pendente é a visual interativa, pelo acesso indisponível ao navegador.
