import csv
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .modelos import BulaLocalizada, MedicamentoParaColeta


class ControleColeta:
    def __init__(self, dsn: str) -> None:
        self.conexao = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bulas (
                    nome_normalizado TEXT PRIMARY KEY,
                    nome_pesquisado TEXT NOT NULL,
                    quantidade_registros_planilha INTEGER NOT NULL DEFAULT 1,
                    nome_anvisa TEXT,
                    numero_registro TEXT,
                    expediente TEXT,
                    id_produto TEXT,
                    id_bula_profissional TEXT,
                    data_atualizacao_anvisa TIMESTAMPTZ,
                    data_atualizacao_original TEXT,
                    status TEXT NOT NULL,
                    tentativas INTEGER NOT NULL DEFAULT 0,
                    caminho_pdf TEXT,
                    sha256_pdf TEXT,
                    tamanho_pdf BIGINT,
                    tempo_consulta_segundos NUMERIC(12, 3),
                    tempo_download_segundos NUMERIC(12, 3),
                    tempo_total_segundos NUMERIC(12, 3),
                    mensagem TEXT,
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_bulas_status ON bulas (status)"
            )
            for comentario in (
                "COMMENT ON TABLE bulas IS "
                "'Controle da coleta da bula profissional mais recente por nome de medicamento.'",
                "COMMENT ON COLUMN bulas.nome_normalizado IS "
                "'Chave normalizada usada para impedir mais de uma bula por nome.'",
                "COMMENT ON COLUMN bulas.tempo_consulta_segundos IS "
                "'Tempo em segundos da consulta paginada e seleção da bula mais recente, medido com relógio monotônico.'",
                "COMMENT ON COLUMN bulas.tempo_download_segundos IS "
                "'Tempo em segundos da transferência e recebimento do PDF.'",
                "COMMENT ON COLUMN bulas.tempo_total_segundos IS "
                "'Tempo em segundos desde o início da consulta até a validação e preparação do PDF ou registro do erro.'",
            ):
                cursor.execute(comentario)

    def fechar(self) -> None:
        self.conexao.close()

    def concluido(self, nome_normalizado: str) -> bool:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, caminho_pdf
                FROM bulas
                WHERE nome_normalizado = %s
                """,
                (nome_normalizado,),
            )
            linha = cursor.fetchone()
        if not linha or linha["status"] != "CONCLUIDO":
            return False
        return bool(linha["caminho_pdf"] and Path(linha["caminho_pdf"]).exists())

    def iniciar(self, medicamento: MedicamentoParaColeta) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bulas (
                    nome_normalizado,
                    nome_pesquisado,
                    quantidade_registros_planilha,
                    status,
                    tentativas,
                    atualizado_em
                )
                VALUES (%s, %s, %s, 'PROCESSANDO', 1, NOW())
                ON CONFLICT (nome_normalizado) DO UPDATE SET
                    nome_pesquisado = EXCLUDED.nome_pesquisado,
                    quantidade_registros_planilha =
                        EXCLUDED.quantidade_registros_planilha,
                    status = 'PROCESSANDO',
                    tentativas = bulas.tentativas + 1,
                    tempo_consulta_segundos = NULL,
                    tempo_download_segundos = NULL,
                    tempo_total_segundos = NULL,
                    mensagem = NULL,
                    atualizado_em = NOW()
                """,
                (
                    medicamento.nome_normalizado,
                    medicamento.nome_produto,
                    medicamento.quantidade_registros_planilha,
                ),
            )

    def finalizar(
        self,
        medicamento: MedicamentoParaColeta,
        status: str,
        mensagem: str = "",
        bula: BulaLocalizada | None = None,
        caminho_pdf: str = "",
        sha256_pdf: str = "",
        tamanho_pdf: int | None = None,
        tempo_consulta_segundos: float | None = None,
        tempo_download_segundos: float | None = None,
        tempo_total_segundos: float | None = None,
    ) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                UPDATE bulas SET
                    nome_anvisa = %s,
                    numero_registro = %s,
                    expediente = %s,
                    id_produto = %s,
                    id_bula_profissional = %s,
                    data_atualizacao_anvisa = %s,
                    data_atualizacao_original = %s,
                    status = %s,
                    mensagem = %s,
                    caminho_pdf = %s,
                    sha256_pdf = %s,
                    tamanho_pdf = %s,
                    tempo_consulta_segundos = %s,
                    tempo_download_segundos = %s,
                    tempo_total_segundos = %s,
                    atualizado_em = NOW()
                WHERE nome_normalizado = %s
                """,
                (
                    bula.nome_produto if bula else None,
                    bula.numero_registro if bula else None,
                    bula.expediente if bula else None,
                    bula.id_produto if bula else None,
                    bula.id_bula_profissional if bula else None,
                    bula.data_atualizacao if bula else None,
                    bula.data_atualizacao_original if bula else None,
                    status,
                    mensagem[:2000],
                    caminho_pdf or None,
                    sha256_pdf or None,
                    tamanho_pdf,
                    tempo_consulta_segundos,
                    tempo_download_segundos,
                    tempo_total_segundos,
                    medicamento.nome_normalizado,
                ),
            )

    def exportar_csv(self, caminho: Path) -> Path:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM bulas
                ORDER BY nome_normalizado
                """
            )
            linhas = cursor.fetchall()
            colunas = [descricao.name for descricao in cursor.description or []]

        def gravar(destino: Path) -> None:
            with destino.open("w", newline="", encoding="utf-8-sig") as arquivo:
                escritor = csv.DictWriter(
                    arquivo,
                    fieldnames=colunas,
                    delimiter=";",
                )
                escritor.writeheader()
                escritor.writerows(linhas)

        try:
            gravar(caminho)
            return caminho
        except PermissionError:
            sufixo_data = datetime.now().strftime("%Y%m%d_%H%M%S")
            alternativo = caminho.with_name(
                f"{caminho.stem}_{sufixo_data}{caminho.suffix}"
            )
            gravar(alternativo)
            print(
                f"Aviso: '{caminho.name}' estava aberto. "
                f"Relatório salvo em '{alternativo.name}'."
            )
            return alternativo
