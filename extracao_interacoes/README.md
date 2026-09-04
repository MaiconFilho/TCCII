# Extração de interações medicamentosas com LLM local

Este módulo lê as bulas profissionais já baixadas pelo scraper, identifica a
seção integral de interações medicamentosas com um modelo local e associa o
texto validado ao medicamento correto no PostgreSQL.

Não há análise clínica. O modelo apenas localiza e copia o texto oficial.

## Fluxo

```text
PDF já baixado
→ PyMuPDF extrai todas as páginas em ordem
→ marcadores [[PÁGINA N]] separam as páginas no prompt
→ tokenizer conta o contexto completo e a reserva da resposta
→ Qwen analisa a bula inteira
→ Pydantic e validações de continuidade conferem o JSON
→ PostgreSQL grava os identificadores e o trecho na mesma transação
→ CSV local registra métricas e erros
```

O Python não procura previamente um tópico por expressão regular e não envia
um recorte ao modelo. O texto completo é enviado ou, se não couber no contexto,
o PDF recebe `CONTEXTO_EXCEDIDO` sem resultado parcial. Não há chunking nesta
versão.

## Modelo

O padrão é:

```text
Qwen/Qwen3-4B-Instruct-2507
```

O modelo possui 4 bilhões de parâmetros e contexto nativo de 262.144 tokens.
Ele é carregado uma única vez por execução com `AutoTokenizer`,
`AutoModelForCausalLM`, `device_map` e `torch_dtype="auto"`. As mensagens são
formatadas com `tokenizer.apply_chat_template`; a geração usa
`torch.inference_mode()` e `do_sample=False`.

Não é usada API paga. No primeiro uso, o Transformers baixa o modelo do
Hugging Face para o cache local. Os pesos e caches não são versionados.

### Memória e desempenho

Os pesos BF16 de um modelo de 4 bilhões de parâmetros ocupam aproximadamente
8 GB antes dos demais custos de execução. Para documentos curtos, uma GPU com
cerca de 12 GB de VRAM pode ser suficiente, mas o consumo cresce com o número
de tokens. O contexto máximo de 262.144 tokens pode exigir dezenas de GB
adicionais para cache e ultrapassar a memória de GPUs comuns.

Em CPU, reserve preferencialmente 16 a 32 GB de RAM para contextos moderados.
O processamento funciona, porém será consideravelmente mais lento. Memória e
tempo reais variam com o sistema, tamanho da bula e versão das bibliotecas. Se
houver falta de memória, reduza `HF_MAX_INPUT_TOKENS`; documentos maiores que o
novo limite serão registrados, nunca truncados silenciosamente.

## Numeração variável da seção

A seção não é tratada como “tópico 6”. Ela pode aparecer como tópico 4, 5, 6,
7, outro número ou sem numeração, além de possuir títulos equivalentes. O
prompt obriga o modelo a usar título, conteúdo, contexto e hierarquia do
documento e a copiar até imediatamente antes do próximo título de mesmo nível.

Uma ocorrência no sumário é apenas uma referência de navegação. Uma menção
isolada é uma frase dentro de outra seção. A seção verdadeira tem título e
conteúdo próprio no corpo da bula. O modelo recebe regras explícitas para não
confundir esses três casos. O título gravado e exibido é sempre o título
dinâmico retornado e validado, nunca um número preenchido pelo Python.

## Leitura e validação

Cada página é extraída com:

```python
pagina.get_text("text", sort=True)
```

As páginas são enviadas na forma `[[PÁGINA 1]]`, `[[PÁGINA 2]]` e assim por
diante. Esses marcadores são removidos antes da gravação. PDFs inválidos ou sem
camada textual suficiente são registrados no relatório e não passam pelo
modelo. OCR não está implementado.

A resposta deve ser somente JSON:

```json
{
  "encontrado": true,
  "titulo_encontrado": "5. INTERAÇÕES MEDICAMENTOSAS",
  "trecho_interacoes": "5. INTERAÇÕES MEDICAMENTOSAS\nTexto integral..."
}
```

Quando não existe seção específica, os dois campos textuais devem ser `null`.
O Python remove somente uma cerca Markdown externa indevida, interpreta o
JSON, valida tipos e coerência com Pydantic, confere o título e exige que o
trecho normalizado esteja contido continuamente no texto extraído. Respostas
inventadas, não contínuas, sem conteúdo ou que atinjam o limite de saída são
rejeitadas.

Há uma única nova tentativa quando a validação falha. O motivo é informado ao
modelo junto com o documento completo. Se a segunda resposta também falhar,
nada é inserido no banco.

## PostgreSQL

O módulo usa o mesmo banco `TCC` e a mesma variável `DATABASE_URL` do scraper.
Antes de criar a tabela, a aplicação confirma que `nome_normalizado`,
`numero_registro` e `expediente` existem em `bulas` e possuem tipo textual.

```sql
CREATE TABLE IF NOT EXISTS bulas_interacoes (
    nome_normalizado TEXT PRIMARY KEY,
    numero_registro TEXT NOT NULL,
    expediente TEXT,
    trecho_interacoes TEXT,

    CONSTRAINT fk_bulas_interacoes_bula
        FOREIGN KEY (nome_normalizado)
        REFERENCES bulas (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);
```

`nome_normalizado`, `numero_registro`, `expediente` e `caminho_pdf` são lidos
conjuntamente da mesma linha de `bulas`. Registro e expediente não são lidos do
PDF nem produzidos pelo LLM. A inserção dos três identificadores e do trecho
ocorre em uma única transação parametrizada.

Por padrão, medicamentos já presentes em `bulas_interacoes` não são
selecionados. `--reprocessar` faz um `upsert` explícito e atualiza também
`numero_registro` e `expediente`. Um `trecho_interacoes` nulo só é gravado após
uma resposta válida com `encontrado=false`; falhas técnicas não criam linha.

## Instalação

Pré-requisitos:

- Python 3.11 ou superior;
- PostgreSQL com a tabela `bulas` preenchida pelo scraper;
- acesso aos caminhos de PDF gravados em `bulas`;
- espaço para baixar o modelo no primeiro uso;
- GPU CUDA opcional.

No CMD, dentro de `extracao_interacoes`:

```cmd
py -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
```

## Configuração

Exemplo seguro de `.env`:

```text
DATABASE_URL=postgresql://postgres:SUA_SENHA@localhost:5432/TCC
HF_MODEL_ID=Qwen/Qwen3-4B-Instruct-2507
HF_MAX_INPUT_TOKENS=262144
HF_MAX_NEW_TOKENS=16384
HF_DEVICE=auto
```

Valores de `HF_DEVICE`:

- `auto`: usa a distribuição automática e aproveita CUDA quando disponível;
- `cuda`: exige uma GPU CUDA disponível;
- `cpu`: força o processamento em CPU.

O limite de entrada considera o prompt completo, o texto integral e o espaço
reservado por `HF_MAX_NEW_TOKENS`.

## Execução

Primeiro teste, com apenas uma bula:

```cmd
python main.py --limite 1
```

Outros exemplos:

```cmd
python main.py --inicio 0 --limite 10
python main.py --todos
python main.py --reprocessar --limite 1
python main.py --limite 1 --modelo Qwen/Qwen3-4B-Instruct-2507
python main.py --limite 1 --max-input-tokens 131072 --max-new-tokens 8192
```

O padrão processa somente um PDF. A seleção considera apenas linhas
`CONCLUIDO`, com registro e caminho preenchidos, cujo arquivo exista. A ordem é
determinística por nome, registro e expediente.

## Relatório e status

O relatório padrão é `relatorios/resultado.csv`, ignorado pelo Git. Ele contém
identificadores, caminho, título dinâmico, páginas, caracteres, tokens, tempos
de leitura/inferência/total e detalhes de erro.

Status possíveis:

- `CONCLUIDO`: seção encontrada, validada e gravada;
- `SEM_SECAO_INTERACOES`: ausência confirmada por JSON válido e `NULL` gravado;
- `PDF_SEM_TEXTO`: camada textual ausente ou insuficiente;
- `PDF_INVALIDO`: arquivo ilegível ou fora do formato esperado;
- `CONTEXTO_EXCEDIDO`: documento completo mais reserva não cabe no limite;
- `RESPOSTA_INVALIDA`: JSON ou conteúdo reprovado após a segunda tentativa;
- `RESPOSTA_TRUNCADA`: duas respostas atingiram o limite sem encerrar;
- `ERRO_MODELO`: falha ao carregar, tokenizar ou executar o modelo;
- `ERRO_BANCO`: falha transacional ou duplicidade concorrente.

O processamento continua com o próximo PDF após erros por item. A execução
posterior retoma pelos medicamentos ainda ausentes em `bulas_interacoes`.

## Testes

Os testes usam mocks e PDFs sintéticos; não baixam o modelo, não exigem GPU,
internet ou PostgreSQL real:

```cmd
python -m unittest discover -s tests -v
```

Para executar também a suíte existente do scraper:

```cmd
cd ..\scraping_anvisa
python -m unittest discover -s tests -v
```

## Limitações desta versão

- não há OCR para PDFs baseados somente em imagem;
- não há chunking: o documento precisa caber integralmente no contexto;
- a qualidade final depende da camada textual e da leitura semântica do modelo;
- `sort=True` melhora a ordem por posição, mas layouts complexos podem produzir
  uma ordem textual imperfeita;
- não há embeddings, pgvector, busca semântica, API, interface ou análise
  clínica.
