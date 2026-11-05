# Scraping das bulas da Anvisa com Selenium e PostgreSQL

Este projeto lê a planilha `StatusBulasANVISA_formatada_2026-08-26.xlsx`,
agrupa as linhas pelo **nome normalizado do medicamento** e baixa somente uma
bula profissional para cada nome: aquela que possui a **data de publicação
mais recente** no Bulário Eletrônico da Anvisa.

## Regra de seleção

A tabela `bulas` já nasce com `principios_ativos` e `composicao` (ambos `TEXT`).
O scraper coleta o PDF; esses dois campos são preenchidos depois por
`pipelines/extracao_interacoes/main.py`, na mesma execução que extrai as interações.
Não há nova chamada à Anvisa nem carregamento de LLM durante o scraping.

Para cada nome único da planilha, a aplicação:

1. pesquisa `filter[nomeProduto]` no endpoint do Bulário;
2. percorre todas as páginas retornadas;
3. mantém apenas resultados cujo nome normalizado seja exatamente igual ao
   pesquisado;
4. descarta resultados sem bula profissional;
5. interpreta o campo `data`, exibido no site como **Data de Publicação**;
6. seleciona a maior data e baixa somente aquela bula.

O campo `dataAtualizacao` informa a atualização geral da base do Bulário e não
é usado para selecionar nem datar as bulas.

Diferenças de maiúsculas, minúsculas, acentuação e espaços não criam nomes
duplicados. Por exemplo, `Finasterida` e `FINASTERIDA` são controladas pela
mesma chave `finasterida`.

Na planilha incluída, as 8.808 linhas são reduzidas para 5.895 nomes únicos.
Finasterida aparece 16 vezes, mas gera somente uma coleta e um PDF.

Quando duas bulas possuem exatamente a mesma data de publicação, o expediente
e o registro são utilizados como desempate determinístico.

## Por que o Selenium

Chamadas diretas com `curl` e clientes HTTP foram bloqueadas pelo Cloudflare,
enquanto o acesso por uma sessão pública normal do Chrome funcionou. O Selenium
abre o Bulário e executa as chamadas `fetch` dentro do contexto dessa sessão.
O programa não resolve CAPTCHA nem copia tokens. Um HTTP `403` encerra a coleta.
Um HTTP `429` isolado registra o item como limitado, aguarda o tempo informado
pela Anvisa (ou 60 segundos) e segue para o próximo. Três `429` consecutivos
interrompem o lote para não insistir contra a limitação do serviço.

Consultas com muitas páginas recebem uma pausa de um segundo entre páginas para
reduzir rajadas de requisições.

O modo headless foi bloqueado nos testes reais. Use o Chrome visível e minimize
a janela se necessário.

Em 21/10/2026, a pedido do usuário, a abertura da sessão e os scripts de consulta
e download foram restaurados à versão do commit `ac90869` (01/09/2026). A
comparação confirmou igualdade de `navegador.py` e dos dois scripts JavaScript.
O perfil padrão da inclusão voltou a ser `.chrome-profile-anvisa`, o mesmo deste
scraper. A integração automática equivale à opção `--sem-pausa-inicial` existente:
não exige ENTER no terminal. Os controles de fila e as mensagens de erro da
inclusão permanecem. Essa restauração não garante que o serviço externo ainda
aceite as requisições que aceitava na coleta de setembro.

## Estrutura

```text
anvisa_scraper/
  planilha.py       leitura, normalização e agrupamento dos nomes
  navegador.py      abertura da sessão Selenium/Chrome
  api_anvisa.py     paginação, seleção da bula mais recente e download
  controle.py       controle de retomada no PostgreSQL e relatório CSV
  pipeline.py       processamento sequencial do lote
dados/              planilha de entrada
pdfs/               PDFs baixados pelo fluxo atual
controle/           resultado.csv
main.py             entrada do programa
.env.example        modelo da conexão PostgreSQL
criar_banco.sql     criação inicial do banco e das tabelas bulas e bulas_interacoes
consultar_metricas.sql consultas prontas para análise dos tempos
```

## 1. Criar o banco PostgreSQL

Crie um banco vazio por ferramenta administrativa e use
`python -m banco.operacao inicializar --env caminho/banco-local.env`.
O arquivo `criar_banco.sql` também contém a baseline completa de cinco tabelas/60
campos. Não o execute sobre base existente; para atualização, siga
[banco/README.md](../../banco/README.md). O coletor não executa DDL no construtor.

## 2. Configurar a conexão

No CMD, dentro da pasta do projeto:

```cmd
copy .env.example .env
notepad .env
```

Edite o arquivo:

```text
DATABASE_URL=postgresql://postgres:SUA_SENHA@localhost:5432/TCC
```

Substitua `SUA_SENHA` pela senha do usuário PostgreSQL. O arquivo `.env` não
deve ser compartilhado ou enviado ao Git.

## 3. Instalar o projeto

Pré-requisitos: Python 3.11 ou superior e Google Chrome instalado.

Pelo CMD:

```cmd
py -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Também é possível executar `instalar.bat`.

O Selenium Manager localiza ou instala o driver compatível com o Chrome na
configuração padrão.

## 4. Executar um piloto

```cmd
python main.py --inicio 0 --limite 5 --intervalo 5
```

Depois de confirmar que o Bulário abriu normalmente, pressione ENTER. Para não
pedir confirmação nas próximas execuções:

```cmd
python main.py --inicio 0 --limite 50 --intervalo 7 --sem-pausa-inicial
```

Os argumentos continuam iguais, mas agora `--inicio` e `--limite` atuam
sobre a lista de **nomes únicos**, não sobre as 8.808 linhas originais.

## Controle no PostgreSQL e retomada

`bulas` tem os nove campos do [modelo aprovado](../../banco/modelo.sql): nome
normalizado, nome Anvisa, registro, expediente, princípios ativos, composição,
status, tentativas e tempo total. IDs do portal, publicação usada na seleção,
caminho/hash/tamanho de PDF e tempos parciais são dados transitórios da coleta,
sem colunas equivalentes no banco final. Erros detalhados ficam nos logs da execução.

Um cadastro `CONCLUIDO` é ignorado mesmo se o PDF não estiver neste computador:
a base é fixa, sem atualização automática. Itens não concluídos podem recuperar
PDF local pelo nome determinístico e tentar novamente. O diretório do coletor é
configurável; a extração usa `INTERAMED_PDF_DIR` com a mesma localização.

O CSV de controle exporta somente os nove campos mantidos. As consultas de
`consultar_metricas.sql` usam tempo total e distribuição por status. A coleta
real depende do acesso atual ao Bulário e não faz parte dos testes simulados.

## Argumentos

```text
--planilha ARQUIVO       caminho de outro Excel
--aba NOME               nome da aba
--inicio N               índice inicial da lista de nomes únicos
--limite N               quantidade de nomes do lote
--intervalo SEGUNDOS     pausa entre nomes; mínimo aplicado: 3
--espera-429 SEGUNDOS    espera mínima após um HTTP 429; padrão: 60
--max-429-consecutivos N interrompe após N respostas 429 seguidas; padrão: 3
--headless               executa sem janela, mas foi bloqueado pela Anvisa
--sem-pausa-inicial      não pede ENTER após abrir o site
--todos                  seleciona todos os nomes e exige confirmação
```

## Etapa seguinte do TCC

Este pacote termina no download validado dos PDFs. A extração das interações
medicamentosas foi implementada separadamente em `../extracao_interacoes`.
Ela não presume um tópico de número fixo e, nesta versão, não utiliza chunks,
embeddings nem pgvector.
