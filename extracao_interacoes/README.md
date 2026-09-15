# Extração de interações medicamentosas

Esta etapa lê as bulas profissionais coletadas pelo scraper e grava o tópico
completo de interações medicamentosas no PostgreSQL. Cada `nome_normalizado`
possui um único registro em `bulas_interacoes`.

O texto é copiado das linhas extraídas do PDF. A LLM confirma a estrutura e os
limites; não redige um resumo nem completa informações ausentes.

## Como funciona

1. **Seleção:** consulta medicamentos com coleta `CONCLUIDO` na tabela `bulas`,
   registro informado e PDF disponível no caminho registrado.
2. **Leitura:** o PyMuPDF extrai o texto de todas as páginas e atribui identificadores
   às linhas. A ordenação considera blocos e espaços entre colunas.
3. **OCR seletivo:** páginas com imagens sem texto suficiente, texto corrompido
   ou letras convertidas em curvas passam pelo Tesseract integrado ao PyMuPDF.
   O resultado fica em cache local; o PDF original é preservado.
4. **Localização:** procura títulos explícitos de interações, inclusive quebrados
   em linhas, com numeração variável ou sem numeração. Sumários, referências
   no corpo e tabelas do histórico de alterações são filtrados.
5. **Limites:** identifica o próximo tópico principal. Subtítulos, listas,
   combinações que exigem precaução e continuações em outras páginas pertencem
   à seção. Respostas curtas como “Não são conhecidas” também podem ser válidas.
6. **Confirmação:** no modo rápido, a LLM recebe contextos das bordas e responde
   um JSON curto. O corpo intermediário é copiado integralmente após validação.
7. **Persistência:** grava texto, identificadores, status e tempos numa transação.
   Se houver diferentes seções válidas de apresentações no mesmo PDF, todas
   são reunidas no mesmo campo. Cópias idênticas são consolidadas.
8. **Relatório:** registra resultados, falhas e métricas em CSV e log locais.

O modo padrão é `rapido`. Ele usa o modelo local
`Qwen/Qwen2.5-1.5B-Instruct-GGUF`, arquivo
`qwen2.5-1.5b-instruct-q4_k_m.gguf`, por meio de `llama-cpp-python`.
A configuração padrão usa CPU, quantização de 4 bits e uma instância do modelo
para todo o lote. Não depende de PyTorch ou Transformers.

Com `EXTRACAO_MODO=completo`, o texto é dividido em janelas para classificação
e localização dos limites pela LLM. Esse modo exige mais inferências e tempo;
o modo rápido não o ativa automaticamente quando encontra uma ambiguidade.

## Estrutura

Os módulos ficam diretamente nesta pasta, sem outra pasta de mesmo nome.

| Arquivo ou grupo | Responsabilidade |
| --- | --- |
| `main.py`, `pipeline.py` | Comandos, seleção e processamento do lote |
| `leitor_pdf.py`, `layout_pdf.py` | Leitura, ordenação e separação de cabeçalhos |
| `ocr.py`, `preparar_ocr.py` | OCR seletivo, cache e instalação do idioma |
| `validacao.py`, `modo_rapido.py` | Títulos, corpo, limites e confirmação curta |
| `servico.py`, `segmentacao.py` | Fluxo de extração e janelas do modo completo |
| `modelo_llm.py`, `provedor_llm.py`, `prompts.py`, `esquemas_llm.py` | Modelo local, instruções e esquemas de resposta |
| `modelos.py`, `erros.py`, `memoria.py` | Tipos, falhas e medição de memória |
| `repositorio.py`, `criar_tabela.sql` | Persistência e esquema PostgreSQL |
| `relatorio.py` | Relatório CSV |
| `reprocessar_pendentes.py`, `diagnosticar_pendentes.py` | Retentativa e diagnóstico |
| `tests/` | Testes isolados e regressões dos formatos de bula |

## Instalação

Pré-requisitos: Python 3.11 ou superior, PostgreSQL e a etapa de scraping
configurada. A tabela `bulas` deve existir no esquema `public` e os PDFs
precisam estar acessíveis pelos caminhos registrados nela.

No Prompt de Comando do Windows, a partir da raiz do repositório:

```cmd
cd extracao_interacoes
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
python preparar_ocr.py
```

Use `copy` apenas na primeira configuração, se ainda não tiver um `.env`.
No Linux/macOS, ative com `source .venv/bin/activate` e use `cp` para criar
o arquivo de configuração.

Edite `DATABASE_URL` no `.env` com a conexão do seu banco. O exemplo usa
uma senha fictícia. Os demais parâmetros e seus valores padrão estão em
[.env.example](.env.example).

O primeiro processamento pode baixar aproximadamente 1,1 GB do modelo GGUF.
`preparar_ocr.py` baixa o modelo de idioma português e verifica seu SHA-256.
Os pesos ficam em `modelos_hf/` e `modelos_ocr/`, fora do Git.

A instalação do backend usa o índice de wheels CPU declarado em
`requirements.txt`. Se não houver wheel compatível com o Python/sistema,
o instalador poderá precisar compilar o backend com ferramentas C/C++.
GPU requer uma instalação compatível do backend e configuração explícita;
o projeto não detecta nem habilita CUDA automaticamente.

## Executar

Todos os comandos abaixo partem de `extracao_interacoes`, com o ambiente ativo.

Teste com um medicamento:

```cmd
python -u main.py --nome-normalizado "aas" --reprocessar --relatorio relatorios/teste_aas.csv
```

Processar até 50 medicamentos ainda não registrados:

```cmd
python -u main.py --inicio 0 --limite 50 --relatorio relatorios/lote_50.csv
```

Processar todos os elegíveis:

```cmd
python -u main.py --todos --relatorio relatorios/lote_completo.csv
```

Sem `--todos` ou `--limite`, o limite padrão é 1. `--inicio` é um deslocamento
na lista elegível ordenada; a lista muda à medida que resultados são gravados.
Para continuar o trabalho normal, use novamente `--todos`, sem calcular um
deslocamento com base no lote anterior.

Por padrão, qualquer nome já presente em `bulas_interacoes` fica fora da seleção.
Use `--reprocessar` para tentar novamente nomes existentes. Cada novo relatório
deve usar um nome próprio se você quiser preservar medições de execuções anteriores.

## Retentar e diagnosticar pendentes

Listar pendências atuais do banco, sem carregar a LLM:

```cmd
python reprocessar_pendentes.py
```

Executar a retentativa:

```cmd
python -u reprocessar_pendentes.py --executar
```

Incluir também falhas `PDF_SEM_TEXTO` de um CSV anterior que ainda não tenham
registro no banco:

```cmd
python -u reprocessar_pendentes.py --relatorio-anterior relatorios/lote_completo.csv --executar
```

O CSV é opcional. Um status atual `CONCLUIDO` sempre prevalece sobre o CSV antigo.
Use `--nome-normalizado "nome"` para restringir a tentativa.

A retentativa cria uma pasta própria em `relatorios/` com uma cópia dos registros
anteriores, o resultado CSV e uma conferência dos registros fora do lote.
**Somente novas conclusões com texto são gravadas por esse utilitário.**
Falhas ficam no relatório e preservam o registro anterior; por isso os status
do CSV e do banco podem diferir.

Para diagnosticar todos os nomes sem conclusão, sem carregar a LLM nem gravar no banco:

```cmd
python -u diagnosticar_pendentes.py --saida relatorios/diagnostico_pendentes.json
```

O diagnóstico executa leitura/OCR e informa títulos, limites, páginas e indícios
encontrados. `SEM_TITULO` é uma classificação automática, não uma comprovação
de ausência. O destino deve ser um arquivo novo.

## Banco de dados

[criar_tabela.sql](criar_tabela.sql) e `repositorio.py` definem o mesmo esquema.
O comando principal cria a tabela se ela não existir. Uma tabela antiga não é
migrada automaticamente.

| Coluna | Conteúdo |
| --- | --- |
| `nome_normalizado` | Chave primária e referência à tabela `bulas` |
| `numero_registro`, `expediente` | Identificadores textuais da bula |
| `trecho_interacoes` | Título e corpo completo; NULL quando não confirmado |
| `status_extracao` | Conclusão, ausência de seção ou revisão manual |
| `detalhe_revisao` | Motivo da pendência |
| `tempo_leitura_segundos` | Leitura e OCR/cache |
| `tempo_inferencia_segundos` | Chamadas ao modelo |
| `tempo_total_segundos` | Extração completa, incluindo validações |

Os três tempos usam `NUMERIC(12,3)`: `90.500` representa 90,5 segundos,
ou 1 minuto e 30,5 segundos. O total não inclui carregamento/download do modelo,
smoke test, seleção no banco ou persistência do resultado. O tempo de OCR já
está incluído na leitura e no total.

Para atualizar uma tabela antiga, execute manualmente no banco:

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.bulas_interacoes
    ADD COLUMN IF NOT EXISTS tempo_leitura_segundos NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS tempo_inferencia_segundos NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS tempo_total_segundos NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS status_extracao TEXT,
    ADD COLUMN IF NOT EXISTS detalhe_revisao TEXT;

ALTER TABLE public.bulas_interacoes
    ALTER COLUMN tempo_leitura_segundos TYPE NUMERIC(12,3)
        USING round(tempo_leitura_segundos::numeric, 3),
    ALTER COLUMN tempo_inferencia_segundos TYPE NUMERIC(12,3)
        USING round(tempo_inferencia_segundos::numeric, 3),
    ALTER COLUMN tempo_total_segundos TYPE NUMERIC(12,3)
        USING round(tempo_total_segundos::numeric, 3);

UPDATE public.bulas_interacoes
SET status_extracao = CASE
    WHEN trecho_interacoes IS NULL THEN 'SEM_SECAO_INTERACOES'
    ELSE 'CONCLUIDO'
END
WHERE status_extracao IS NULL;

ALTER TABLE public.bulas_interacoes
    ALTER COLUMN status_extracao SET DEFAULT 'CONCLUIDO',
    ALTER COLUMN status_extracao SET NOT NULL;
COMMIT;
```

Para consultar os resultados e métricas:

```sql
SELECT nome_normalizado, status_extracao, trecho_interacoes,
       tempo_total_segundos,
       round(tempo_total_segundos / 60, 2) AS tempo_total_minutos
FROM public.bulas_interacoes
ORDER BY nome_normalizado;
```

## Status e limites de interpretação

- `CONCLUIDO`: seção validada e texto disponível.
- `SEM_SECAO_INTERACOES`: nenhum título explícito confirmado pela execução.
- `REVISAO_MANUAL`: estrutura ambígua, portfólio/anexos ou leitura OCR sem
  confirmação do título. O fluxo principal registra a pendência com texto NULL;
  uma revisão não substitui uma conclusão existente.
- Demais falhas — como PDF sem texto, OCR indisponível, resposta inválida,
  memória ou banco — ficam no CSV/log e precisam de nova tentativa ou correção.

As regras aceitam numeração com parênteses, títulos quebrados, pequenas variantes
de pontuação e respostas curtas. O histórico regulatório é identificado por
combinações de cabeçalhos, datas e rótulos, incluindo páginas de continuação.

O escopo é o tópico dedicado a interações. Informações sobre interações dentro
de advertências ou outros tópicos não são coletadas automaticamente.
`SEM_SECAO_INTERACOES` não significa que o medicamento não tenha interações.

OCR pode errar caracteres e doses. A detecção de colunas e as validações são
heurísticas: formatos ainda desconhecidos exigem revisão contra o PDF.
A adequação a computadores com 8 GB depende dos demais programas em execução
e da configuração; quantização e o limite de memória não garantem um consumo fixo.

## Testes e versionamento

```cmd
python -m unittest discover -s tests -v
```

Os testes usam documentos sintéticos e provedores simulados. Não exigem download
do modelo, PDFs coletados, GPU ou PostgreSQL real. Cobrem leitura, títulos,
continuações, histórico, OCR, validação de respostas e persistência.
O GitHub Actions executa os testes do scraper e da extração.

O Git versiona código, testes, dependências, SQL, documentação e `.env.example`.
Ambientes virtuais, `.env`, PDFs, modelos, caches, logs, relatórios, backups e
arquivos temporários são locais. O script antigo `auditar_pdfs.py` usado na
investigação local foi substituído nesta entrega por `diagnosticar_pendentes.py`.
