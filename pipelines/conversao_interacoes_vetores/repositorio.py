from __future__ import annotations

import logging
import re

import psycopg
from psycopg.rows import dict_row

from .erros import (
    DimensaoInvalidaError,
    ErroBancoError,
    EsquemaBancoIncompativelError,
    ExtensaoVectorIndisponivelError,
)
from .modelos import EmbeddingChunk, InteracaoParaVetorizar


LOGGER = logging.getLogger(__name__)

TABELA = "bulas_interacoes_embeddings"
INDICE_HNSW = "idx_bulas_interacoes_embeddings_hnsw"

SQL_CRIAR_EXTENSAO = "CREATE EXTENSION IF NOT EXISTS vector"

# A dimensão é fixada em tempo de execução a partir do modelo carregado.
SQL_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS bulas_interacoes_embeddings (
    id BIGSERIAL PRIMARY KEY,
    nome_normalizado TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    texto_chunk TEXT NOT NULL,
    texto_hash TEXT NOT NULL,
    dimensao INTEGER NOT NULL,
    quantidade_tokens INTEGER,
    embedding VECTOR({dimensao}) NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_embeddings_bula_interacao
        FOREIGN KEY (nome_normalizado)
        REFERENCES bulas_interacoes (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT uq_embedding_chunk
        UNIQUE (nome_normalizado, chunk_index)
)
"""

# Colunas da versão anterior; se ainda existirem, a gravação falharia.
COLUNAS_LEGADAS = ("modelo", "criado_em")

SQL_CRIAR_INDICE = """
CREATE INDEX IF NOT EXISTS {indice}
ON bulas_interacoes_embeddings
USING hnsw (embedding vector_cosine_ops)
"""


def vetor_para_literal(vetor: tuple[float, ...] | list[float]) -> str:
    """Converte para o literal textual aceito pelo pgvector, sem dependências."""
    return "[" + ",".join(repr(float(valor)) for valor in vetor) + "]"


class RepositorioEmbeddings:
    """Reaproveita o mesmo DATABASE_URL e a mesma conexão psycopg do projeto.

    A tabela textual ``bulas_interacoes`` permanece intacta: ela continua sendo
    a fonte oficial do texto e nunca recebe vetores.
    """

    def __init__(self, dsn: str) -> None:
        try:
            self.conexao = psycopg.connect(
                dsn,
                autocommit=True,
                row_factory=dict_row,
            )
            self._validar_esquema_interacoes()
        except EsquemaBancoIncompativelError:
            self._fechar_apos_falha()
            raise
        except Exception as erro:
            self._fechar_apos_falha()
            raise ErroBancoError(
                f"Falha ao preparar o PostgreSQL: {type(erro).__name__}: {erro}"
            ) from erro

    def _fechar_apos_falha(self) -> None:
        conexao = getattr(self, "conexao", None)
        if conexao is not None:
            conexao.close()

    def fechar(self) -> None:
        self.conexao.close()

    # -------------------------------------------------------------- validação

    def _validar_esquema_interacoes(self) -> None:
        """Confere o tipo de bulas_interacoes.nome_normalizado antes de criar."""
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'bulas_interacoes'
                  AND column_name IN ('nome_normalizado', 'trecho_interacoes')
                """
            )
            colunas = {linha["column_name"]: linha for linha in cursor.fetchall()}

        ausentes = {"nome_normalizado", "trecho_interacoes"} - set(colunas)
        if ausentes:
            raise EsquemaBancoIncompativelError(
                "Colunas ausentes em bulas_interacoes: " + ", ".join(sorted(ausentes))
            )
        tipos_textuais = {"text", "character varying", "character"}
        if colunas["nome_normalizado"]["data_type"] not in tipos_textuais:
            raise EsquemaBancoIncompativelError(
                "bulas_interacoes.nome_normalizado deve ser textual para a "
                "chave estrangeira dos embeddings."
            )
        if colunas["nome_normalizado"]["is_nullable"] != "NO":
            raise EsquemaBancoIncompativelError(
                "bulas_interacoes.nome_normalizado deve ser NOT NULL."
            )

    # ------------------------------------------------------ extensão e tabela

    def garantir_extensao_vector(self) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            if cursor.fetchone():
                return
            cursor.execute(
                "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
            )
            if not cursor.fetchone():
                raise ExtensaoVectorIndisponivelError(
                    "A extensão 'vector' (pgvector) não está disponível neste "
                    "servidor PostgreSQL. Instale o pgvector e execute "
                    "CREATE EXTENSION vector; no banco antes de gerar os vetores."
                )
            try:
                cursor.execute(SQL_CRIAR_EXTENSAO)
            except Exception as erro:
                raise ExtensaoVectorIndisponivelError(
                    f"Falha ao criar a extensão vector: "
                    f"{type(erro).__name__}: {erro}"
                ) from erro

    def dimensao_da_tabela(self) -> int | None:
        """Dimensão declarada na coluna embedding, ou None se a tabela não existe."""
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT format_type(a.atttypid, a.atttypmod) AS tipo
                FROM pg_attribute AS a
                JOIN pg_class AS c ON c.oid = a.attrelid
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = %s
                  AND a.attname = 'embedding'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                """,
                (TABELA,),
            )
            linha = cursor.fetchone()
        if not linha:
            return None
        encontrado = re.search(r"\((\d+)\)", linha["tipo"] or "")
        return int(encontrado.group(1)) if encontrado else None

    def colunas_legadas_presentes(self) -> list[str]:
        """Detecta colunas da versão anterior que já não são gravadas."""
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND column_name = ANY(%s)
                """,
                (TABELA, list(COLUNAS_LEGADAS)),
            )
            return sorted(linha["column_name"] for linha in cursor.fetchall())

    def garantir_tabela(self, dimensao: int) -> None:
        """Cria a tabela quando ausente; nunca executa DROP nem apaga dados."""
        if dimensao <= 0:
            raise DimensaoInvalidaError("A dimensão do modelo deve ser positiva.")
        atual = self.dimensao_da_tabela()
        if atual is not None:
            if atual != dimensao:
                raise DimensaoInvalidaError(
                    f"A tabela {TABELA} já usa VECTOR({atual}) e o modelo "
                    f"carregado produz {dimensao} dimensões. Nada foi alterado; "
                    "use outro modelo de mesma dimensão ou crie uma tabela "
                    "própria para a nova dimensão."
                )
            legadas = self.colunas_legadas_presentes()
            if legadas:
                raise EsquemaBancoIncompativelError(
                    f"A tabela {TABELA} ainda tem as colunas {', '.join(legadas)}, "
                    "removidas nesta versão. Execute os comandos ALTER TABLE do "
                    "final de criar_tabela.sql antes de gerar novos vetores. "
                    "Nada foi alterado."
                )
            return
        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    cursor.execute(SQL_CRIAR_TABELA.format(dimensao=int(dimensao)))
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao criar {TABELA}: {type(erro).__name__}: {erro}"
            ) from erro

    def garantir_indice(self, dimensao: int) -> None:
        """Cria o índice HNSW de cosseno somente se a dimensão for compatível."""
        atual = self.dimensao_da_tabela()
        if atual is None:
            raise ErroBancoError(
                f"A tabela {TABELA} precisa existir antes da criação do índice."
            )
        if atual != dimensao:
            raise DimensaoInvalidaError(
                f"Índice não criado: a tabela usa VECTOR({atual}) e o modelo "
                f"produz {dimensao} dimensões."
            )
        try:
            with self.conexao.cursor() as cursor:
                cursor.execute(SQL_CRIAR_INDICE.format(indice=INDICE_HNSW))
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao criar o índice {INDICE_HNSW}: "
                f"{type(erro).__name__}: {erro}"
            ) from erro

    # ------------------------------------------------------------- consultas

    def selecionar_interacoes(
        self,
        inicio: int = 0,
        limite: int | None = 1,
        reprocessar: bool = False,
        nome_normalizado: str | None = None,
    ) -> list[InteracaoParaVetorizar]:
        """Somente linhas com texto; em lote, ignora quem já tem vetor.

        O filtro por vetor existente serve para o lote não reprocessar a base
        inteira. Quando o chamador pede **um medicamento específico** pelo
        nome, o filtro é dispensado: a linha é devolvida e a decisão passa a ser
        do controle de hash, que responde ``IGNORADO_JA_EXISTENTE`` quando o
        texto e o modelo não mudaram — muito mais informativo do que dizer que
        não há nada elegível.

        A tabela guarda um vetor por chunk, sem coluna de modelo: ao trocar
        ``EMBEDDING_MODEL_ID``, o lote enxerga as linhas antigas como já
        vetorizadas e as pula. Use ``--reprocessar`` para regerar a base com o
        novo modelo.

        O corte acontece no PostgreSQL (``LIMIT``; ``NULL`` significa todos),
        para não trazer milhares de trechos longos para a memória do processo.
        """
        ignorar_existentes = reprocessar or nome_normalizado is not None
        with self.conexao.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    bi.nome_normalizado,
                    bi.trecho_interacoes
                FROM bulas_interacoes AS bi
                WHERE bi.trecho_interacoes IS NOT NULL
                  AND BTRIM(bi.trecho_interacoes) <> ''
                  AND (CAST(%s AS TEXT) IS NULL OR bi.nome_normalizado = %s)
                  AND (
                      %s
                      OR NOT EXISTS (
                          SELECT 1
                          FROM {TABELA} AS e
                          WHERE e.nome_normalizado = bi.nome_normalizado
                      )
                  )
                ORDER BY bi.nome_normalizado
                OFFSET %s
                LIMIT %s
                """,
                (
                    nome_normalizado,
                    nome_normalizado,
                    ignorar_existentes,
                    inicio,
                    limite,
                ),
            )
            linhas = cursor.fetchall()

        selecionadas = [
            InteracaoParaVetorizar(
                nome_normalizado=linha["nome_normalizado"],
                trecho_interacoes=linha["trecho_interacoes"],
            )
            for linha in linhas
        ]
        return selecionadas if limite is None else selecionadas[:limite]

    def hashes_existentes(self, nome_normalizado: str) -> dict[int, str]:
        """Hashes já gravados, usados para decidir se a inferência é necessária.

        O identificador do modelo continua dentro do hash, então uma troca de
        modelo é detectada aqui mesmo sem a coluna ``modelo`` na tabela.
        """
        with self.conexao.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT chunk_index, texto_hash
                FROM {TABELA}
                WHERE nome_normalizado = %s
                """,
                (nome_normalizado,),
            )
            return {
                linha["chunk_index"]: linha["texto_hash"]
                for linha in cursor.fetchall()
            }

    # -------------------------------------------------------------- gravação

    def gravar_embeddings(
        self,
        nome_normalizado: str,
        dimensao: int,
        chunks: list[EmbeddingChunk],
    ) -> int:
        """Grava todos os chunks em uma única transação.

        Chamado apenas depois da geração e da validação completas: se algo
        falhar, a transação é desfeita e os vetores anteriores permanecem.
        """
        if not chunks:
            raise ErroBancoError(
                f"Nenhum chunk para gravar em '{nome_normalizado}'."
            )
        divergentes = [c.chunk_index for c in chunks if c.dimensao != dimensao]
        if divergentes:
            raise DimensaoInvalidaError(
                f"Chunks {divergentes} de '{nome_normalizado}' não possuem "
                f"{dimensao} dimensões."
            )

        comando = f"""
            INSERT INTO {TABELA} (
                nome_normalizado,
                chunk_index,
                texto_chunk,
                texto_hash,
                dimensao,
                quantidade_tokens,
                embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (nome_normalizado, chunk_index) DO UPDATE SET
                texto_chunk = EXCLUDED.texto_chunk,
                texto_hash = EXCLUDED.texto_hash,
                dimensao = EXCLUDED.dimensao,
                quantidade_tokens = EXCLUDED.quantidade_tokens,
                embedding = EXCLUDED.embedding,
                atualizado_em = CURRENT_TIMESTAMP
        """
        remocao = f"""
            DELETE FROM {TABELA}
            WHERE nome_normalizado = %s
              AND chunk_index > %s
        """

        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    for chunk in chunks:
                        cursor.execute(
                            comando,
                            (
                                nome_normalizado,
                                chunk.chunk_index,
                                chunk.texto_chunk,
                                chunk.texto_hash,
                                dimensao,
                                chunk.quantidade_tokens,
                                vetor_para_literal(chunk.vetor),
                            ),
                        )
                    # Um texto menor gera menos chunks: remove as sobras antigas.
                    cursor.execute(
                        remocao,
                        (
                            nome_normalizado,
                            max(chunk.chunk_index for chunk in chunks),
                        ),
                    )
        except DimensaoInvalidaError:
            raise
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao gravar os vetores de '{nome_normalizado}': "
                f"{type(erro).__name__}: {erro}"
            ) from erro
        return len(chunks)
