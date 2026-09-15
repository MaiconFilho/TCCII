# Conversão das interações medicamentosas em vetores

Este módulo lê os trechos já gravados em `bulas_interacoes.trecho_interacoes`,
converte cada trecho em um ou mais vetores com um modelo de embeddings local e
grava os vetores em `bulas_interacoes_embeddings` com pgvector.

```text
trecho_interacoes
→ modelo de embeddings do Hugging Face
→ vetor
→ PostgreSQL com pgvector
```

## Objetivo

A base textual de interações medicamentosas já existe e permanece intacta. O
que falta é uma representação numérica que permita comparar semanticamente os
trechos. Esta etapa produz apenas essa representação.

O serviço foi escrito para ser reaproveitado sob demanda pela aplicação futura,
quando um usuário pesquisar um medicamento ausente:

```text
scraping → download da bula → extração das interações → gravação em
bulas_interacoes → geração do embedding → gravação do vetor → busca por
similaridade
```

**Nesta etapa não estão implementados** busca por similaridade, resposta ao
usuário, API pública, scraping, leitura de PDFs, análise clínica, treinamento
ou fine-tuning. O fluxo atual de extração das interações não foi alterado.

## Modelos avaliados

Comparação feita em setembro de 2026, considerando português, similaridade
semântica, textos médicos, consumo em CPU com cerca de 8 GB de RAM, licença e
manutenção. Nenhum LLM generativo foi considerado: embeddings vêm de encoders.

| Modelo | Dim. | Máx. tokens | Parâmetros | Licença | Observação |
| --- | --- | --- | --- | --- | --- |
| **`intfloat/multilingual-e5-small`** | **384** | **512** | **117,7 M** | **MIT** | **Escolhido.** Treinado em 100 idiomas, português incluído; forte em recuperação semântica; o menor dos modelos de qualidade comparável. |
| `intfloat/multilingual-e5-base` | 768 | 512 | 278,0 M | MIT | Mesma família, qualidade um pouco melhor, porém ~2,4× mais parâmetros e inferência bem mais lenta em CPU. |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384 | **128** | 117,7 M | Apache-2.0 | **Alternativa de menor consumo.** Mesmo tamanho do escolhido, mas a janela de 128 tokens fragmentaria muito os trechos longos das bulas. |
| `Alibaba-NLP/gte-multilingual-base` | 768 | 8192 | 305,4 M | Apache-2.0 | Janela longa e boa qualidade, mas exige `trust_remote_code` (código de terceiros executado localmente) e consome bem mais memória. |
| `BAAI/bge-m3` | 1024 | 8192 | ~568 M | MIT | Melhor qualidade multilíngue da lista e janela de 8.192 tokens, dispensando chunking na maioria das bulas; descartado pelo peso (~2,2 GB em float32) em uma máquina de 8 GB. |

### Modelo escolhido e justificativa

**`intfloat/multilingual-e5-small`** — dimensão 384, limite de 512 tokens,
licença MIT.

1. **Português nativo.** É multilíngue por treinamento (não uma tradução
   adaptada) e tem o português entre os idiomas declarados.
2. **Feito para similaridade e recuperação.** A família E5 é treinada com
   contraste para busca semântica, que é exatamente o uso futuro previsto.
3. **Cabe em 8 GB com folga.** 117,7 M de parâmetros; o processo inteiro ficou
   em **menos de 900 MB de RSS** nesta máquina (medição adiante), deixando o
   restante da RAM livre para o PostgreSQL e para o sistema.
4. **Janela de 512 tokens.** Quatro vezes maior que a do MiniLM, o que reduz
   bastante a fragmentação dos trechos longos de bulas.
5. **Licença MIT** e manutenção ativa (mais de 12 milhões de downloads mensais
   no Hugging Face, atualizado em 2026).
6. **Compatível com Transformers puro**, já presente no projeto.

A alternativa de menor consumo é
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Ela foi mantida
como segunda opção deliberadamente porque **também produz 384 dimensões**: é
possível trocar `EMBEDDING_MODEL_ID` sem recriar a tabela nem o índice, já que a
coluna `VECTOR(384)` continua válida. O custo da troca é a janela de 128 tokens.

**Sobre textos médicos.** Não foi adotado um modelo clínico especializado: os
disponíveis são treinados em inglês (PubMedBERT, BioBERT e similares) e
perderiam o português das bulas. Um modelo multilíngue geral de boa qualidade é
a escolha mais segura aqui.

**Sobre quantização.** É possível (`torch.quantization.quantize_dynamic` ou
exportação ONNX/INT8), mas não foi necessária: o modelo em float32 já ocupa
cerca de 450 MB. A quantização foi deixada de fora para não introduzir perda de
qualidade sem ganho real de viabilidade.

### Ficha técnica

| Item | Valor |
| --- | --- |
| Modelo | `intfloat/multilingual-e5-small` |
| Dimensão do vetor | 384 |
| Limite de entrada do modelo | 512 tokens |
| Limite útil por chunk | 508 tokens (512 − 2 especiais − 2 do prefixo) |
| Tamanho dos pesos | ~450 MB (float32, `model.safetensors`) |
| Licença | MIT |
| Biblioteca | `transformers` + `torch` (CPU) |
| Prefixo exigido | `passage: ` ao indexar |

## Memória: estimada e observada

O projeto **não promete** consumo fixo. Os números abaixo foram medidos com
`psutil` nesta máquina (Windows 11, PostgreSQL 18, CPU, `EMBEDDING_BATCH_SIZE=8`,
`EMBEDDING_CHUNK_SIZE=450`):

| Momento | RSS observado |
| --- | --- |
| Processo Python com torch importado | 36 MB |
| Depois de carregar o modelo (uma única vez) | 670 MB |
| Trecho curto (200 caracteres, 1 chunk, 48 tokens) — pico | 758 MB |
| Maior trecho da base (142.137 caracteres, 71 chunks, 27.677 tokens) — pico | **870 MB** |
| Ao final da execução | 836 MB |

Tempos observados: carregamento inicial de 65 s (inclui o download de ~450 MB
na primeira execução); 0,20 s para o trecho curto; 10,4 s para o maior trecho
da base inteira.

A estimativa para outras máquinas fica entre 0,7 e 1,5 GB, variando com a
compilação do PyTorch e o tamanho do lote. O limite de
`EMBEDDING_MAX_PROCESS_MEMORY_MB` (padrão 5.500) é conferido **antes** de cada
inferência; se já tiver sido ultrapassado, o status é `LIMITE_MEMORIA` e nada é
gravado.

### O que garante o baixo consumo

- o modelo é carregado **uma única vez** por execução, nunca por registro nem
  por requisição;
- processamento **sequencial**, um medicamento por vez;
- `EMBEDDING_INFERENCE_CONCURRENCY` é obrigatoriamente 1 e a configuração
  recusa qualquer outro valor;
- lotes pequenos e configuráveis (`EMBEDDING_BATCH_SIZE`, padrão 8);
- `gc.collect()` entre lotes e descarte explícito dos tensores;
- `torch.inference_mode()`, sem grafo de gradientes;
- número de threads limitado a no máximo 4 quando `EMBEDDING_THREADS=0`;
- CPU é o padrão; a GPU só é usada com `EMBEDDING_DEVICE=cuda`.

## Instalação

```cmd
cd conversao_interacoes_vetores
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edite o `.env` com a sua `DATABASE_URL`.

**Atalho nesta máquina:** o ambiente de `extracao_interacoes/.venv` já tem
`torch`, `transformers`, `psycopg`, `psutil` e `python-dotenv` nas versões
exigidas, então dá para reaproveitá-lo e evitar baixar o PyTorch de novo
(cerca de 2,5 GB):

```cmd
cd conversao_interacoes_vetores
..\extracao_interacoes\.venv\Scripts\activate
python main_embeddings.py --limite 1
```

### Download inicial do modelo

O download acontece **apenas na primeira execução**. Os pesos ficam no cache
local `conversao_interacoes_vetores/modelos_hf/`, que está no `.gitignore`.
Nenhum peso, cache ou arquivo de modelo é versionado. Execuções seguintes
carregam do disco.

### Extensão pgvector

A vetorização exige o pgvector instalado no servidor PostgreSQL:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

A aplicação executa esse comando sozinha, mas só funciona se os arquivos da
extensão já estiverem instalados no servidor. Se a extensão não estiver
disponível, a CLI para com uma mensagem explícita e **não** processa nada —
nenhum registro é lido ou alterado.

**Situação desta máquina (setembro de 2026):** PostgreSQL 18 com **pgvector
0.8.6 instalado e ativo** no banco `TCC`. A vetorização foi executada e
validada ponta a ponta (resultados adiante).

O pgvector não publica binários para Windows — os releases trazem apenas o
código-fonte —, então a instalação exige compilar com MSVC e Windows SDK:

```cmd
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
set "PGROOT=C:\Program Files\PostgreSQL\18"
nmake /F Makefile.win
nmake /F Makefile.win install
```

O `nmake install` grava em `C:\Program Files`, então precisa de um terminal
aberto como Administrador. Em seguida:

```cmd
psql -U postgres -d TCC -c "CREATE EXTENSION vector;"
```

Como alternativa, o pgvector já vem pronto na imagem Docker oficial
`pgvector/pgvector:pg18`, o que evita a compilação local.

## Configuração (`.env`)

| Variável | Padrão | Função |
| --- | --- | --- |
| `DATABASE_URL` | — | Mesma do restante do projeto. |
| `EMBEDDING_MODEL_ID` | `intfloat/multilingual-e5-small` | Modelo no Hugging Face. |
| `EMBEDDING_DEVICE` | `cpu` | `cpu` ou `cuda`. |
| `EMBEDDING_BATCH_SIZE` | `8` | Chunks por lote de inferência. |
| `EMBEDDING_MAX_INPUT_TOKENS` | `512` | Teto de tokens enviados ao modelo. |
| `EMBEDDING_NORMALIZE` | `true` | Normalização L2 antes da gravação. |
| `EMBEDDING_CHUNK_SIZE` | `450` | Tokens por chunk. |
| `EMBEDDING_CHUNK_OVERLAP` | `60` | Tokens repetidos entre chunks vizinhos. |
| `EMBEDDING_THREADS` | `0` | `0` usa a heurística segura (máx. 4). |
| `EMBEDDING_MAX_PROCESS_MEMORY_MB` | `5500` | Limite conferido antes da inferência. |
| `EMBEDDING_INFERENCE_CONCURRENCY` | `1` | Precisa ser 1 neste perfil. |
| `EMBEDDING_PASSAGE_PREFIX` | `passage: ` | Prefixo exigido pela família E5. |

O `python-dotenv` remove o espaço final das variáveis, então o código recoloca
o espaço de `passage: ` automaticamente.

## Estrutura da tabela

`bulas_interacoes` **não foi alterada** e continua sendo a fonte textual
oficial. O vetor nunca substitui `trecho_interacoes`. Os vetores ficam em uma
tabela separada:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS bulas_interacoes_embeddings (
    id BIGSERIAL PRIMARY KEY,
    nome_normalizado TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    texto_chunk TEXT NOT NULL,
    texto_hash TEXT NOT NULL,
    dimensao INTEGER NOT NULL,
    quantidade_tokens INTEGER,
    embedding VECTOR(384) NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_embeddings_bula_interacao
        FOREIGN KEY (nome_normalizado)
        REFERENCES bulas_interacoes (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT uq_embedding_chunk
        UNIQUE (nome_normalizado, chunk_index)
);
```

`numero_registro` e `expediente` **não** foram duplicados: são obtidos por
junção com `bulas_interacoes` e `bulas`.

Não há coluna `modelo`: o projeto usa um único modelo de embeddings, e o
identificador dele continua fazendo parte do `texto_hash`, o que mantém a
detecção de troca de modelo sem custo de armazenamento. A contrapartida está
registrada em [Limitações](#limitações).

Antes de criar qualquer coisa, o repositório confere que
`bulas_interacoes.nome_normalizado` é textual e `NOT NULL`, verifica se a
extensão `vector` existe e compara a dimensão declarada na coluna com a
dimensão real do modelo carregado. Se houver divergência, a execução para com
`DIMENSAO_INVALIDA` e **nada é alterado**. O módulo nunca executa `DROP TABLE`,
`DROP COLUMN` nem apaga dados existentes.

### Migração de bancos da versão anterior

A primeira versão desta tabela tinha as colunas `modelo` e `criado_em`. Em um
banco que já as tenha, a aplicação **para antes de gravar** e pede a execução
dos comandos `ALTER TABLE` que estão no final de
[`criar_tabela.sql`](criar_tabela.sql): eles removem as duas colunas e trocam a
restrição única, **sem apagar nenhum vetor**. Bancos novos já nascem no formato
atual e não precisam de nada disso.

## Chunking

Estratégia baseada no tokenizer do próprio modelo, em `chunking.py`:

1. o trecho é tokenizado com o tokenizer do modelo, sem tokens especiais, de
   modo que a contagem corresponda ao que será codificado;
2. o tamanho do chunk é reduzido automaticamente para o limite útil do modelo
   (508 tokens), nunca o ultrapassando;
3. **se o texto couber**, gera-se um único vetor com `chunk_index = 0` e
   `texto_chunk` igual ao **trecho completo original**, sem passar por
   destokenização — o texto original não é alterado;
4. **se não couber**, o texto é cortado em janelas de `EMBEDDING_CHUNK_SIZE`
   tokens que avançam `chunk_size − overlap` por vez;
5. a sobreposição é configurável e serve para não partir uma interação ao meio;
6. gera-se um vetor por chunk, e a ordem de leitura é preservada pelo
   `chunk_index` crescente (0, 1, 2, …);
7. a última janela sempre alcança o último token: **nada é truncado nem
   descartado silenciosamente**.

Com os padrões atuais (450/60), o maior trecho da base — 27.677 tokens —
produziu 71 chunks. O trecho mediano da base (~1.900 caracteres) cabe em 2
chunks.

Nos chunks intermediários o `texto_chunk` é o texto reconstruído pelo tokenizer
(destokenização), que pode diferir do original em espaços e acentuação de
subtokens. Isso é esperado e não afeta `bulas_interacoes`, que permanece a
fonte oficial do texto.

## Normalização e índice

Os vetores são normalizados em L2 antes da gravação (`EMBEDDING_NORMALIZE=true`,
padrão). A norma medida dos vetores gravados é 1,000000.

Isso é adequado porque a busca futura usará **distância de cosseno**: com
vetores unitários, o cosseno equivale ao produto interno, a comparação fica
numericamente estável e independente do comprimento do chunk — um chunk longo
não ganha peso sobre um curto só por ter mais tokens.

O índice é criado com `--criar-indice`, depois de confirmar que a dimensão da
coluna bate com a do modelo:

```sql
CREATE INDEX IF NOT EXISTS idx_bulas_interacoes_embeddings_hnsw
ON bulas_interacoes_embeddings
USING hnsw (embedding vector_cosine_ops);
```

**HNSW em vez de IVFFlat** porque: (a) o IVFFlat precisa de dados já carregados
para treinar as listas e teria de ser recriado conforme a base cresce, enquanto
o HNSW pode ser criado antes e aceita inserções incrementais — o que é
exatamente o caso de uso sob demanda previsto; (b) o HNSW tem recall melhor para
o mesmo tempo de consulta; (c) o volume aqui (dezenas de milhares de chunks em
384 dimensões) é pequeno o bastante para que o custo de memória do HNSW não
seja problema. Nenhum índice é criado com dimensão incompatível.

## Hash e idempotência

Para cada chunk é calculado um SHA-256 estável sobre:

```text
nome_normalizado · chunk_index · texto_chunk · modelo
```

(os campos são unidos pelo separador `\x1f`, para que a concatenação não gere
colisões). O `texto_hash` do relatório resume os hashes dos chunks do
documento.

Consequências:

- embeddings já existentes para o mesmo texto e o mesmo modelo são **ignorados
  sem chamar o modelo** (`IGNORADO_JA_EXISTENTE`), o que torna o reprocessamento
  barato;
- alteração do texto muda o hash e dispara a regeneração;
- troca de modelo muda o hash e dispara a regeneração da linha avaliada —
  o identificador do modelo continua dentro do hash mesmo sem a coluna
  `modelo` na tabela;
- a restrição `UNIQUE (nome_normalizado, chunk_index)` impede duplicidade
  no banco;
- o reprocessamento explícito (`--reprocessar`) sempre regera;
- a gravação ocorre em **uma única transação**, apenas depois da geração e da
  validação completas (dimensão correta, sem `NaN`, sem infinito, sem vetor
  nulo). Se qualquer etapa falhar, a transação é desfeita e **o vetor anterior
  é preservado**.

## Comandos

Todos executados dentro de `conversao_interacoes_vetores/`.

```cmd
python main_embeddings.py --limite 1
python main_embeddings.py --limite 10
python main_embeddings.py --todos
python main_embeddings.py --nome-normalizado "a saude da mulher"
python main_embeddings.py --reprocessar --nome-normalizado "a saude da mulher"
```

Opções adicionais:

```text
--inicio N            posição inicial da seleção
--batch-size N        sobrescreve EMBEDDING_BATCH_SIZE
--modelo ID           sobrescreve EMBEDDING_MODEL_ID
--device cpu|cuda     sobrescreve EMBEDDING_DEVICE
--chunk-size N        sobrescreve EMBEDDING_CHUNK_SIZE
--chunk-overlap N     sobrescreve EMBEDDING_CHUNK_OVERLAP
--reprocessar         regera e substitui vetores existentes
--criar-indice        cria o índice HNSW de cosseno
--relatorio CAMINHO   destino do CSV
```

**O padrão processa apenas um registro.** `--todos` precisa ser pedido
explicitamente.

### Regra de seleção

| Situação | Comportamento |
| --- | --- |
| Lote (`--limite`, `--todos`) | Pula quem já tem vetor, para não reprocessar a base inteira. |
| `--nome-normalizado X` | Sempre devolve a linha. A decisão passa para o controle de hash: `IGNORADO_JA_EXISTENTE` se o texto e o modelo não mudaram, `REPROCESSADO` se o texto mudou. |
| `--reprocessar` | Regera e substitui, mesmo com hash idêntico. |

### Execução validada

Resultados reais obtidos nesta máquina, com pgvector 0.8.6:

| Comando | Resultado |
| --- | --- |
| `--limite 1 --criar-indice` | `CONCLUIDO` — tabela e índice HNSW criados, 1 chunk de 48 tokens gravado |
| `--nome-normalizado "a saude da mulher"` (2ª vez) | `IGNORADO_JA_EXISTENTE` — nenhuma inferência executada |
| `--reprocessar --nome-normalizado "a saude da mulher"` | `REPROCESSADO` — 1 chunk, sem duplicar a linha |
| `--nome-normalizado "apixabana"` (142.137 caracteres) | `CONCLUIDO` — 71 chunks, índices 0 a 70, 31.877 tokens |
| `--limite 3` | `CONCLUIDO: 3` — os já vetorizados foram pulados pela seleção |

Conferência no banco após as execuções: 72 chunks gravados e 72 combinações
`(nome_normalizado, chunk_index)` distintas — **nenhuma duplicidade**;
norma L2 de 1,000000 em todos os vetores; nenhum `NaN` ou infinito; e
`bulas_interacoes.trecho_interacoes` de apixabana intacto com os mesmos 142.137
caracteres. Tempos por registro no lote: 0,25 s a 0,58 s de inferência e 0,003 s
a 0,017 s de banco, com pico de memória entre 766 MB e 783 MB.

### Consultar o vetor gravado

```sql
SELECT
    e.nome_normalizado,
    e.chunk_index,
    e.dimensao,
    e.quantidade_tokens,
    LEFT(e.texto_chunk, 120) AS inicio_do_chunk,
    e.texto_hash,
    LEFT(e.embedding::text, 80) || '...' AS inicio_do_vetor,
    e.atualizado_em
FROM bulas_interacoes_embeddings AS e
WHERE e.nome_normalizado = 'a saude da mulher'
ORDER BY e.chunk_index;
```

Mais consultas prontas em [`consultar_vetores.sql`](consultar_vetores.sql).

## Relatório

Cada execução grava um CSV local em `relatorios/`, ignorado pelo Git, com as
colunas: `nome_normalizado`, `quantidade_chunks`, `modelo`, `dimensao`,
`quantidade_tokens`, `texto_hash`, `status`, `tempo_chunking_segundos`,
`tempo_inferencia_segundos`, `tempo_banco_segundos`, `tempo_total_segundos`,
`memoria_antes_mb`, `pico_memoria_mb`, `detalhe_erro`.

Status possíveis: `CONCLUIDO`, `IGNORADO_JA_EXISTENTE`, `REPROCESSADO`,
`SEM_TEXTO`, `MODELO_INVALIDO`, `DIMENSAO_INVALIDA`, `ERRO_CHUNKING`,
`ERRO_INFERENCIA`, `ERRO_BANCO`, `LIMITE_MEMORIA`.

## Arquitetura

```text
main_embeddings.py          CLI: .env, argumentos, log, relatório
conversao_interacoes_vetores/
  provedor_embeddings.py    Protocol ProvedorEmbeddings (contrato injetável)
  modelo_embeddings.py      ProvedorEmbeddingsTransformers: carrega uma vez
  chunking.py               divisão por tokens, com sobreposição
  hashing.py                hash estável de chunk e de documento
  servico.py                ServicoEmbeddings: caso de uso, sem CLI e sem banco
  repositorio.py            pgvector, extensão, tabela, índice e gravação
  pipeline.py               laço sequencial do lote
  relatorio.py              CSV
  memoria.py                MedidorMemoria (mesmo padrão de extracao_interacoes)
  modelos.py                dataclasses e StatusEmbedding
  erros.py                  exceções tratáveis
```

`ServicoEmbeddings` recebe o provedor já carregado, **não instancia o modelo
dentro de nenhum método** e não conhece o banco de dados. Ele mantém um
`threading.Lock` global (uma requisição por vez) e um conjunto de nomes em
andamento que impede o processamento duplicado do mesmo medicamento. É essa
classe que a aplicação web futura deve reaproveitar, mantendo uma única
instância do provedor viva no processo.

## Testes

```cmd
cd conversao_interacoes_vetores
python -m unittest discover -s tests -v
```

Os testes **não baixam o modelo real** e não exigem GPU, internet nem
PostgreSQL. Torch, Transformers, tokenizer, modelo, cursor e conexão são
substituídos por duplos em `tests/helpers.py` e `tests/dobros_torch.py`.

Cobrem: carregamento único, processamento em lotes, textos curtos, textos
longos, chunking, sobreposição, preservação da ordem, ausência de truncamento,
cálculo de hash, dimensão correta, normalização, rejeição de dimensão
incorreta, rejeição de `NaN` e de infinito, prevenção de duplicidade, alteração
do texto, mudança do modelo, reprocessamento, rollback em erro, preservação do
vetor anterior, seleção por nome, controle de concorrência, limite de memória,
geração de métricas, criação da extensão pgvector e criação do índice.

## Limitações

- **A busca por similaridade não faz parte desta etapa.** Nenhuma consulta
  vetorial, ranking, API ou resposta ao usuário foi implementada.
- A vetorização exige o pgvector instalado no servidor PostgreSQL; sem ele a
  CLI para com mensagem explícita, sem processar nada.
- A base tem 5.761 trechos com texto. Pelos tempos medidos, vetorizar tudo com
  `--todos` leva na ordem de 1 a 2 horas em CPU nesta máquina; o processo é
  retomável, porque o lote pula o que já foi gravado.
- O consumo de memória informado é o observado nesta máquina, não uma garantia.
- Trocar de modelo para uma dimensão diferente exige uma nova tabela: a coluna
  `VECTOR(384)` não aceita outra dimensão, e o código recusa a operação em vez
  de alterar a tabela existente.
- Sem a coluna `modelo`, a tabela guarda os vetores de **um modelo por vez**.
  Ao trocar `EMBEDDING_MODEL_ID` para outro de mesma dimensão, o lote enxerga as
  linhas antigas como já vetorizadas e as pula: rode `--todos --reprocessar`
  para regerar a base. A troca continua sendo detectada linha a linha, porque o
  identificador do modelo faz parte do `texto_hash`.
- Os chunks intermediários guardam o texto reconstruído pelo tokenizer, não o
  recorte literal do original.
- Não há avaliação de qualidade dos embeddings (recall, MRR): isso depende de um
  conjunto de consultas de referência, que pertence à etapa de busca.
