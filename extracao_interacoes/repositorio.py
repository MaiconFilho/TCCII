from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .erros import (
    ErroBancoError,
    EsquemaBancoIncompativelError,
    RegistroJaExisteError,
)
from .modelos import BulaParaExtracao, StatusExtracao


SQL_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS bulas_interacoes (
    nome_normalizado TEXT PRIMARY KEY,
    numero_registro TEXT NOT NULL,
    expediente TEXT,
    trecho_interacoes TEXT,
    status_extracao TEXT NOT NULL DEFAULT 'CONCLUIDO',
    detalhe_revisao TEXT,
    tempo_leitura_segundos NUMERIC(12,3),
    tempo_inferencia_segundos NUMERIC(12,3),
    tempo_total_segundos NUMERIC(12,3),

    CONSTRAINT fk_bulas_interacoes_bula
        FOREIGN KEY (nome_normalizado)
        REFERENCES bulas (nome_normalizado)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
)
"""


class RepositorioInteracoes:
    def __init__(self, dsn: str) -> None:
        try:
            self.conexao = psycopg.connect(
                dsn,
                autocommit=True,
                row_factory=dict_row,
            )
            self._validar_esquema_bulas()
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    cursor.execute(SQL_CRIAR_TABELA)
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

    def _validar_esquema_bulas(self) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'bulas'
                  AND column_name IN (
                      'nome_normalizado',
                      'numero_registro',
                      'expediente'
                  )
                """
            )
            colunas = {linha["column_name"]: linha for linha in cursor.fetchall()}

        esperadas = {"nome_normalizado", "numero_registro", "expediente"}
        ausentes = esperadas - set(colunas)
        if ausentes:
            raise EsquemaBancoIncompativelError(
                "Colunas ausentes em bulas: " + ", ".join(sorted(ausentes))
            )
        tipos_textuais = {"text", "character varying", "character"}
        invalidas = [
            nome
            for nome in sorted(esperadas)
            if colunas[nome]["data_type"] not in tipos_textuais
        ]
        if invalidas:
            raise EsquemaBancoIncompativelError(
                "As colunas devem ser textuais: " + ", ".join(invalidas)
            )
        if colunas["nome_normalizado"]["is_nullable"] != "NO":
            raise EsquemaBancoIncompativelError(
                "bulas.nome_normalizado deve ser NOT NULL."
            )

    def fechar(self) -> None:
        self.conexao.close()

    def selecionar_bulas(
        self,
        inicio: int = 0,
        limite: int | None = 1,
        reprocessar: bool = False,
        nome_normalizado: str | None = None,
    ) -> list[BulaParaExtracao]:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    b.nome_normalizado,
                    b.numero_registro,
                    b.expediente,
                    b.caminho_pdf
                FROM bulas AS b
                WHERE b.status = 'CONCLUIDO'
                  AND b.caminho_pdf IS NOT NULL
                  AND BTRIM(b.caminho_pdf) <> ''
                  AND b.numero_registro IS NOT NULL
                  AND (CAST(%s AS TEXT) IS NULL OR b.nome_normalizado = %s)
                  AND (
                      %s
                      OR NOT EXISTS (
                          SELECT 1
                          FROM bulas_interacoes AS bi
                          WHERE bi.nome_normalizado = b.nome_normalizado
                      )
                  )
                ORDER BY
                    b.nome_normalizado,
                    b.numero_registro,
                    b.expediente NULLS FIRST
                """,
                (nome_normalizado, nome_normalizado, reprocessar),
            )
            linhas = cursor.fetchall()

        candidatas = [
            BulaParaExtracao(
                nome_normalizado=linha["nome_normalizado"],
                numero_registro=linha["numero_registro"],
                expediente=linha["expediente"],
                caminho_pdf=Path(linha["caminho_pdf"]).resolve(),
            )
            for linha in linhas
            if Path(linha["caminho_pdf"]).is_file()
        ]
        fim = None if limite is None else inicio + limite
        return candidatas[inicio:fim]

    def gravar_resultado(
        self,
        bula: BulaParaExtracao,
        trecho_interacoes: str | None,
        reprocessar: bool = False,
        *,
        status_extracao: str | None = None,
        detalhe_revisao: str | None = None,
        tempo_leitura_segundos: float | None = None,
        tempo_inferencia_segundos: float | None = None,
        tempo_total_segundos: float | None = None,
    ) -> None:
        status_extracao = status_extracao or (
            StatusExtracao.CONCLUIDO.value if trecho_interacoes is not None
            else StatusExtracao.SEM_SECAO_INTERACOES.value
        )
        permitidos = {
            StatusExtracao.CONCLUIDO.value,
            StatusExtracao.SEM_SECAO_INTERACOES.value,
            StatusExtracao.REVISAO_MANUAL.value,
        }
        if status_extracao not in permitidos:
            raise ValueError("Status nao persistivel em bulas_interacoes.")
        if reprocessar:
            comando = """
                INSERT INTO bulas_interacoes (
                    nome_normalizado,
                    numero_registro,
                    expediente,
                    trecho_interacoes,
                    status_extracao,
                    detalhe_revisao,
                    tempo_leitura_segundos,
                    tempo_inferencia_segundos,
                    tempo_total_segundos
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (nome_normalizado) DO UPDATE SET
                    numero_registro = EXCLUDED.numero_registro,
                    expediente = EXCLUDED.expediente,
                    trecho_interacoes = EXCLUDED.trecho_interacoes,
                    status_extracao = EXCLUDED.status_extracao,
                    detalhe_revisao = EXCLUDED.detalhe_revisao,
                    tempo_leitura_segundos = EXCLUDED.tempo_leitura_segundos,
                    tempo_inferencia_segundos = EXCLUDED.tempo_inferencia_segundos,
                    tempo_total_segundos = EXCLUDED.tempo_total_segundos
                WHERE EXCLUDED.status_extracao <> 'REVISAO_MANUAL'
                   OR bulas_interacoes.status_extracao = 'REVISAO_MANUAL'
            """
        else:
            comando = """
                INSERT INTO bulas_interacoes (
                    nome_normalizado,
                    numero_registro,
                    expediente,
                    trecho_interacoes,
                    status_extracao,
                    detalhe_revisao,
                    tempo_leitura_segundos,
                    tempo_inferencia_segundos,
                    tempo_total_segundos
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (nome_normalizado) DO NOTHING
            """

        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    cursor.execute(
                        comando,
                        (
                            bula.nome_normalizado,
                            bula.numero_registro,
                            bula.expediente,
                            trecho_interacoes,
                            status_extracao,
                            detalhe_revisao,
                            tempo_leitura_segundos,
                            tempo_inferencia_segundos,
                            tempo_total_segundos,
                        ),
                    )
                    if not reprocessar and cursor.rowcount == 0:
                        raise RegistroJaExisteError(
                            f"'{bula.nome_normalizado}' já existe em bulas_interacoes."
                        )
        except RegistroJaExisteError:
            raise
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao gravar '{bula.nome_normalizado}': "
                f"{type(erro).__name__}: {erro}"
            ) from erro
