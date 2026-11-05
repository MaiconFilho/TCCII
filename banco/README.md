# Banco do InteraMed — modelo60/v1

O contrato canônico está em [modelo.sql](modelo.sql) e [modelo.json](modelo.json):
`bulas` (9), `bulas_interacoes` (7), `interacoes` (16),
`interacoes_medicamentos` (4) e `processamentos_interacoes` (24).
São 60 campos, cinco PKs (uma composta) e seis FKs obrigatórias. Nenhuma tabela
extra é usada pela consulta ou pelos pipelines mantidos. As views e triggers
comuns estão em [objetos.sql](objetos.sql), também incorporados no SQL canônico.

`operacao.migrar` é a migração explícita e transacional `modelo60/v1`.
Os arquivos 001–007 da identificação são históricos: não foram editados e não
são reaplicados pelo executor. Este reconhece a baseline nova, verifica a base
já consolidada ou recusa uma base antiga, indicando o comando de atualização.
A versão fica no comentário da view; não se cria uma sexta tabela de controle.

## Regras de seleção e fonte fixa

Uma conclusão utilizável exige extração `CONCLUIDO` com texto, processamento
concluído e `invalidado_em IS NULL`. Entre esses resultados, ganha o maior `id`.
Uma falha ou revisão posterior não oculta a conclusão válida anterior.
A view de registrados considera também revisão, apenas para a retomada.
Ausência de extração, processamento, menções ou vínculos não comprova resultado
negativo. Revisão e retirada explícita continuam distintas.

A fonte não é atualizada automaticamente. Uma correção excepcional do texto
extraído, registro ou expediente exige invalidar explicitamente os resultados
antes de alterar a fonte; o banco rejeita a alteração silenciosa. Isso substitui
a antiga invalidação por hashes de versões de fontes. A API lê registro e
expediente diretamente de `bulas`; não inventa data de publicação ou URL de PDF.

As duas direções da coerência entre origem da interação e seu processamento são
protegidas por triggers, com bloqueio do pai, sem adicionar outra FK. Exclusões
dos pais são restritas. Os índices cobrem as seis FKs, inclusive vínculos inativos.

## Instalação nova

Requer PostgreSQL 15 ou superior. Crie um banco vazio por ferramenta administrativa
e configure sua conexão em arquivo `.env` separado ou em `DATABASE_URL`.
Não passe a conexão completa como argumento de shell nem a copie para o frontend.

```powershell
python -m banco.operacao inicializar --env caminho/configuracao-local.env
python -m banco.operacao validar --env caminho/configuracao-local.env
```

Alternativamente, execute `banco/modelo.sql` inteiro em uma transação sobre banco
novo. A entrada `pipelines/scraping_anvisa/criar_banco.sql` contém a mesma baseline.
Não execute nenhum desses SQLs sobre banco existente. Os coletores não recriam
as colunas antigas. A API não executa DDL.

Tabelas novas têm RLS habilitado, sem políticas públicas. Configure explicitamente
o papel de backend necessário e suas políticas de SELECT. Views usam
`security_invoker=true`; não burlam políticas das tabelas. A migração existente
preserva RLS, políticas e ACLs das tabelas, restaura os privilégios e proprietários
das views e não amplia acesso público. A conexão da API usa transações somente
de leitura; credenciais do PostgreSQL nunca vão para o JavaScript do navegador.

## Diagnóstico e migração de base existente

```powershell
python -m banco.operacao diagnosticar --env apps/api_interacoes/.env.supabase --saida tmp/operacao/diagnostico
```

O relatório inclui contagens, nulos e órfãos em cada FK, duplicidades de PK,
origens divergentes, campos/tipos, constraints, views dependentes, RLS, políticas,
ACLs, modelos/origens dos resumos e objetos fora do modelo. O destino exibido
contém apenas host, porta, nome do banco e identificador não sensível do projeto Supabase. O projeto de produção informado é
`vuowgvmmqbwfhccncxvg`, via pooler de sessão de São Paulo, banco `postgres`.
Confirme também no painel que essa é a conexão configurada na API publicada.

A operação bloqueia os casos inconsistentes; não apaga registros nem inventa
processamentos para preencher FKs. Uma baseline anterior à etapa 007 pode receber
os quatro campos opcionais de reescrita/proveniência ausentes, inicialmente NULL.
Outros campos ausentes bloqueiam a operação. Tipos divergentes também bloqueiam.
Dependências externas incompatíveis bloqueiam `DROP COLUMN ... RESTRICT`, sem
`CASCADE`. Tabelas legadas independentes ficam intactas e são identificadas no
relatório; fila e embeddings não pertencem ao fluxo entregue.

Antes do DROP, conferem-se as publicações simples legadas com seus hashes
originais e compara-se a seleção antiga e a nova por medicamento/ID. Uma publicação
inválida ou uma mudança de seleção causada pelos metadados retirados bloqueia
a migração, apresentando os IDs envolvidos. Não se republicam resultados antigos
silenciosamente. Esses bloqueios exigem análise dos registros concretos.

Na transação, há lock exclusivo nas cinco tabelas e comparação dos 60 campos por
PK antes/depois: textos longos, JSONBs, IDs, relações, status, tempos e datas.
Qualquer diferença provoca rollback. As sequências avançam pelo menos até o
maior ID; nunca retrocedem. Uma repetição sobre a baseline final é segura e não
reaplica os SQLs antigos. Nenhuma geração de resumo é disparada pela operação.

## Resumos Smol e compatibilidade das reescritas

São preservados todos os resumos e metadados, independentemente do nome gravado
para o modelo. Confira os agrupamentos de modelos e também os metadados de
`reescrita_controle`; não presuma que Smol aparece apenas em `resumo_modelo` ou
que produziu somente a versão simples. O manifesto tem digest de cada registro
em todos os campos mantidos; a comparação não depende de identificar o nome Smol.

Na publicação nova `linguagem-simples/37`, o digest de entrada usa apenas origem,
processamento, alvo, princípios ativos, original e evidências mantidos. Hashes
continuam sendo controles legítimos de reescrita, sem guardar campos de fonte
removidos em JSONB. Cálculos em memória para revalidar uma inferência em andamento
também continuam permitidos.

Publicações antigas são validadas antes da migração com sua entrada original.
Se aprovadas, continuam disponíveis pelo controle de publicação e hash do texto,
sem alterar seus JSONBs ou metadados. Depois disso, triggers marcam
`entrada_invalidada` quando original, evidência, grupo, origem ou princípios ativos
mudam. A API oculta uma reescrita inválida; uma publicação nova validada limpa a
marca. Outros cards com alvo diferente continuam válidos. Uma entrada alterada e marcada volta a ser pendência; falhas de tentativa não
limpam essa marca nem republicam o texto antigo. Publicações ainda válidas não
são refeitas por mudança de versão. A migração não executa lotes.

## Backup, restauração e ensaio obrigatório

Use `pg_dump`/`pg_restore` compatíveis com a versão do servidor. No Windows, se não
estiverem no PATH, configure apenas o diretório dos binários:

```powershell
$env:PG_BIN = 'C:/Program Files/PostgreSQL/17/bin'
python -m banco.operacao backup-seguranca --env apps/api_interacoes/.env.supabase --saida tmp/operacao/seguranca
```

O backup usa snapshot consistente compartilhado com o manifesto. Exporta estrutura
e dados do schema `public`, incluindo views, funções e sequências, com os dados
reais existentes. Não é uma cópia dos serviços Supabase, nem de `auth`/`storage`.
Inventarie extensões, papéis e dependências externas antes da restauração; se
usados, instale-os no destino isolado. O código não remove tabelas de módulos
independentes nem inclui credenciais em argumentos de subprocessos.

O manifesto JSON registra origem sem credenciais, instante UTC, escopo, SHA-256
do arquivo, contagens, migração/diagnóstico e digests por PK. Mantenha dump e
manifesto protegidos e ignorados pelo Git; podem conter dados de módulos legados.
O backup de segurança fica separado do final.

Crie uma base **local vazia**, com `test` no nome, e prepare outro arquivo `.env`:

```powershell
python -m banco.operacao restaurar --env tmp/copia-test.env --manifesto tmp/operacao/seguranca/interamed_seguranca_DATA.json --saida tmp/operacao/restauracao-seguranca
python -m banco.validar_copia --env-copia tmp/copia-test.env --manifesto tmp/operacao/seguranca/interamed_seguranca_DATA.json --saida tmp/operacao/copia-validada.json
```

A restauração recusa banco remoto, nome sem `test` e destino não vazio. Confere
o hash do dump e cada registro por PK. O ensaio migra essa cópia e verifica API,
resumos retornados, fontes, paginação, sugestões, pares nos dois sentidos e CORS.
Seu relatório liga o resultado ao SHA-256 exato do backup.

## Aplicação remota e implantação coordenada

1. Prepare API/frontend desta versão e seus builds em preview. Na Vercel, a API
   usa a entrada `main.py` do checkout de publicação; o frontend usa
   `apps/frontend`. Não faça commit/push para executar esta tarefa.
2. Abra uma janela de manutenção: pause todos os escritores, inclusive lotes de
   resumo e trabalhadores antigos; evite requisições públicas durante a troca
   incompatível de schema/API. Publicar o frontend sozinho não resolve isso.
3. Gere o backup de segurança diretamente do Supabase atual, restaure e valide
   essa cópia. Confira o destino remoto e os modelos/controles reais.
4. Aplique com o manifesto desse backup e relatório dessa cópia:

```powershell
python -m banco.operacao migrar --env apps/api_interacoes/.env.supabase --confirmar-destino vuowgvmmqbwfhccncxvg/postgres --janela-manutencao --manifesto tmp/operacao/seguranca/interamed_seguranca_DATA.json --copia-validada tmp/operacao/copia-validada.json --saida tmp/operacao/migracao-remota
```

5. O comando confere o estado do backup novamente sob lock. Escrita concorrente
   faz a aplicação parar, exigindo novo backup e novo ensaio. A flag CLI de manutenção
   declara uma pausa operacional; ela não pausa sozinha serviços da Vercel.
   Na API, `API_MANUTENCAO=true` responde 503 com `Retry-After: 60`, preserva CORS
   e não abre nem consulta o banco. Lotes externos precisam ser pausados separadamente.
6. Publique a API nova e verifique `/api/saude` e leituras com o papel real de
   backend. Publique o frontend com `NEXT_PUBLIC_API_URL=https://interamed-api.vercel.app`
   e configure CORS da API para `https://interamed-web.vercel.app` (origem sem barra
   final). Essa variável pública participa do build estático; alterar só o runtime
   não altera os arquivos já publicados. Nenhuma credencial Supabase é pública.
7. Confira no site cards Smol, versões simples, geral, evidências, múltiplos
   medicamentos e navegação responsiva, comparando IDs da amostra preservada.
8. Gere o backup final **diretamente do Supabase readequado**:

```powershell
python -m banco.operacao validar --env apps/api_interacoes/.env.supabase --saida tmp/operacao/validacao-remota
python -m banco.operacao backup-final --env apps/api_interacoes/.env.supabase --saida tmp/operacao/final
python -m banco.operacao restaurar --env tmp/restauracao-final-test.env --manifesto tmp/operacao/final/interamed_final_DATA.json --saida tmp/operacao/restauracao-final
```

9. Sobre a restauração final, rode `verificar_api` de `banco.validar_copia` e as
   consultas da amostra. Confirme os digests e sequências antes de encerrar a
   manutenção. Não restaure o backup antigo sobre a produção como atalho.
10. Antes de versionar o backup final, revise SQL/TOC e dados para segredos,
    permissões, objetos de outros módulos e dados pessoais, além do limite do Git.
    Se não puder ser versionado, mantenha-o fora do commit e registre sua guarda
    e restauração. Não substitua `backups_banco/interamed_atual.dump` sem essa revisão.

Credencial PostgreSQL com senha real e acesso de publicação Vercel são necessários
para executar as etapas remotas. Uma URL com `SUA_SENHA` não concede acesso. Sem
essas condições, o resultado é um pacote local testado, com migração e backup
remotos pendentes; nunca uma declaração de produção atualizada.


### Comandos de publicação sem commit/push

Depois de obter acesso e confirmar os projetos existentes no painel, use a CLI.
Use `vercel whoami` para conferir a conta autenticada. Selecione primeiro o projeto
que serve `interamed-api.vercel.app`; confira que sua raiz contém `main.py` e que
`DATABASE_URL` é a conexão confirmada, com CORS da origem do site.

```powershell
# Primeiro confira os arquivos; nenhum .env privado ou dump deve ser incluído:
npx vercel deploy --dry --json --project prj_kWDwASI27wsrelNkoEmKcSH2OTU8 --cwd TCCII_publish_worktree
# Antes da migração, publique e verifique a resposta 503:
npx vercel deploy --prod --yes --project prj_kWDwASI27wsrelNkoEmKcSH2OTU8 --cwd TCCII_publish_worktree --env API_MANUTENCAO=true --env API_INCLUSAO_MEDICAMENTOS=false --env API_CORS_ORIGINS=https://interamed-web.vercel.app
# Após backup, ensaio, migração e validação, reabra a consulta:
npx vercel deploy --prod --yes --project prj_kWDwASI27wsrelNkoEmKcSH2OTU8 --cwd TCCII_publish_worktree --env API_MANUTENCAO=false --env API_INCLUSAO_MEDICAMENTOS=false --env API_CORS_ORIGINS=https://interamed-web.vercel.app
```

Confira a saúde 2.0.0 e as consultas, então selecione o
projeto que serve `interamed-web.vercel.app`. Confirme no painel a Root Directory
`apps/frontend`, o comando de build e o output configurado para o site atual.
A partir da raiz do checkout, publique o frontend com a URL de API definida no build:

```powershell
npx vercel deploy --prod --yes --project prj_MSUX23l8YXBVaCFvtmVLPorGGSUH --cwd TCCII_publish_worktree --build-env NEXT_PUBLIC_API_URL=https://interamed-api.vercel.app
```

O identificador do projeto ou vínculo de diretório identifica o destino; não prossiga se a seleção
não corresponder ao domínio pretendido. Não crie um projeto novo como substituto
nem altere DNS. As configurações locais da CLI ficam em `.vercel/`, fora do Git.
Os valores de `--env` pertencem ao deployment. Repita-os nas publicações futuras
ou configure os equivalentes no projeto. A conexão PostgreSQL permanece somente
no ambiente protegido de produção. Nunca deixe o deployment de manutenção como
versão final. Resultados reais e bloqueios estão em [RELATORIO_READEQUACAO.md](RELATORIO_READEQUACAO.md).

O [runtime Python](https://vercel.com/docs/functions/runtimes/python) inclui os
arquivos alcançáveis no build: mantenha `banco/modelo.json` no pacote da API.
Consulte também a documentação de [vínculo](https://vercel.com/docs/cli/link) e
[deploy](https://vercel.com/docs/cli/deploy). A publicação real continua condicionada
à validação do banco e aos acessos disponíveis.
