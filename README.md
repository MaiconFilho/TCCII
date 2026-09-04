# Scraping das bulas da Anvisa com Selenium e PostgreSQL

Este projeto lê a planilha `StatusBulasANVISA_formatada_2026-08-26.xlsx`,
agrupa as linhas pelo **nome normalizado do medicamento** e baixa somente uma
bula profissional para cada nome: aquela que possui a **data de publicação
mais recente** no Bulário Eletrônico da Anvisa.

## Regra de seleção

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
criar_banco.sql     criação inicial do banco
remover_colunas_desnecessarias.sql  migração de esquemas antigos
consultar_metricas.sql consultas prontas para análise dos tempos
```

## 1. Criar o banco PostgreSQL

O PostgreSQL precisa estar instalado e em execução.

No pgAdmin, conecte-se ao banco padrão `postgres`, abra o **Query Tool** e
execute:

```sql
CREATE DATABASE "TCC";
```

A aplicação cria e documenta automaticamente a tabela `bulas` dentro desse
banco. O nome é minúsculo e pode ser usado sem aspas nas consultas SQL.

Se a tabela tiver sido criada por uma versão anterior do projeto, conecte o
Query Tool ao banco `TCC` e execute `remover_colunas_desnecessarias.sql` uma vez.
Essa migração remove somente as quatro colunas obsoletas e não usa `CASCADE`.

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

## Controle no PostgreSQL

A chave primária da tabela é `nome_normalizado`. Para cada nome, são gravados:

- nome normalizado e nome retornado pela Anvisa;
- quantidade de ocorrências daquele nome na planilha;
- registro, expediente e ID do produto selecionado;
- ID protegido da bula profissional;
- data de publicação usada na seleção;
- status, tentativas e mensagem de erro;
- caminho, tamanho e hash SHA-256 do PDF.
- tempo da consulta, do download e do processamento total.

Os campos de métricas possuem comentários gravados no catálogo do PostgreSQL.
As definições são:

- `tempo_consulta_segundos`: paginação, leitura das respostas e seleção da bula
  com maior data de publicação (`data` na API);
- `tempo_download_segundos`: transferência e recebimento do PDF;
- `tempo_total_segundos`: consulta, verificação de reaproveitamento ou download,
  validação e salvamento do arquivo.

Exemplo para consultar as métricas:

```sql
SELECT
    nome_normalizado,
    tempo_consulta_segundos,
    tempo_download_segundos,
    tempo_total_segundos
FROM bulas
ORDER BY tempo_total_segundos DESC;
```

O arquivo `consultar_metricas.sql` contém consultas adicionais para calcular
médias, mínimos, máximos e distribuição por status.

Um item só é ignorado quando possui status `CONCLUIDO` no PostgreSQL e o PDF
existe no caminho registrado. Se o arquivo for apagado, será baixado novamente.

Status possíveis:

- `CONCLUIDO`
- `NOME_NAO_ENCONTRADO`
- `DATA_PUBLICACAO_INVALIDA`
- `SEM_BULA_PROFISSIONAL`
- `ERRO_RESPOSTA`
- `ERRO_INESPERADO`
- `LIMITE_REQUISICOES`
- `INTERROMPIDO_BLOQUEIO`

O relatório legível é exportado para `controle/resultado.csv`.

## Retomada e novas tentativas

Se a execução for interrompida, execute novamente o mesmo comando. Nomes
concluídos serão ignorados e os demais serão tentados novamente.

Registros com status `CONCLUIDO` e PDF existente são sempre ignorados, sem exigir
uma nova consulta. Se uma falha anterior removeu o caminho do banco, mas o PDF
válido ainda existe com o nome determinístico esperado, a aplicação recupera o
arquivo no PostgreSQL e também evita um novo download.

Somente a data de publicação usada para selecionar a bula mais recente é gravada
no banco. Os valores auxiliares originais retornados pela API não são persistidos.

```cmd
python main.py --inicio 0 --limite 4000 --intervalo 10 --sem-pausa-inicial
```

O banco `"TCC"` e a tabela `bulas` substituem completamente o antigo arquivo
`controle/coleta.sqlite3`. O histórico do SQLite não é importado; esta versão
começa um novo controle por nome.

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

## Próxima etapa do TCC

Este pacote termina no download validado dos PDFs. A próxima etapa será extrair
o tópico `6. INTERAÇÕES MEDICAMENTOSAS`, dividir o texto em chunks e gravá-lo
no PostgreSQL com pgvector.
