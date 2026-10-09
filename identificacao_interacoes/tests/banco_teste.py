"""Banco PostgreSQL isolado para testes de integração.

Os testes só rodam com ``IDENTIFICACAO_TEST_DATABASE_URL`` apontando para um
banco cujo nome contenha "test" e que não seja o TCC. A limpeza (TRUNCATE)
acontece somente nesse banco, depois da conferência do nome.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from identificacao_interacoes.aplicar_migracoes import aplicar, listar_migracoes


RAIZ = Path(__file__).resolve().parents[2]
VARIAVEL = "IDENTIFICACAO_TEST_DATABASE_URL"
ESQUEMA_BASE = RAIZ / "scraping_anvisa" / "criar_banco.sql"
TABELAS_LIMPAS = (
    "interacoes_medicamentos",
    "interacoes",
    "processamentos_interacoes",
    "bulas_interacoes",
    "bulas",
)


def dsn_de_teste() -> str | None:
    return os.getenv(VARIAVEL, "").strip() or None


def motivo_para_pular() -> str:
    return f"Defina {VARIAVEL} com um banco PostgreSQL isolado para os testes reais."


def exigir_banco_isolado(conexao) -> str:
    nome = conexao.execute("SELECT current_database() AS nome").fetchone()
    nome = nome["nome"] if isinstance(nome, dict) else nome[0]
    if nome.casefold() == "tcc" or "test" not in nome.casefold():
        raise RuntimeError(f"Recusado: '{nome}' não é um banco isolado de testes.")
    return nome


def _sql_esquema_base() -> str:
    """Etapa 2 de criar_banco.sql: as tabelas bulas e bulas_interacoes."""
    conteudo = ESQUEMA_BASE.read_text(encoding="utf-8")
    return conteudo.split("-- ETAPA 2", 1)[1].split("\n", 1)[1]


def preparar(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        exigir_banco_isolado(conexao)
        conexao.execute(_sql_esquema_base())
    aplicar(dsn, listar_migracoes())


def conectar(dsn: str):
    conexao = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    exigir_banco_isolado(conexao)
    return conexao


def limpar(conexao) -> None:
    exigir_banco_isolado(conexao)
    conexao.execute(
        "TRUNCATE " + ", ".join(TABELAS_LIMPAS) + " RESTART IDENTITY CASCADE"
    )


def inserir_bula(
    conexao,
    nome: str,
    *,
    nome_anvisa: str | None = None,
    principios: str | None = None,
    composicao: str | None = None,
    trecho: str | None = None,
    registro: str = "100",
    expediente: str = "200",
    sha256_pdf: str = "pdf",
    status_extracao: str = "CONCLUIDO",
    data_publicacao: str | None = "2026-06-30 12:00:00-03",
) -> None:
    conexao.execute(
        """
        INSERT INTO bulas (
            nome_normalizado, nome_anvisa, principios_ativos, composicao,
            numero_registro, expediente, sha256_pdf, data_publicacao_anvisa, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'CONCLUIDO')
        """,
        (nome, nome_anvisa or nome.upper(), principios, composicao, registro, expediente,
         sha256_pdf, data_publicacao),
    )
    if trecho is not None or status_extracao != "CONCLUIDO":
        conexao.execute(
            """
            INSERT INTO bulas_interacoes (
                nome_normalizado, numero_registro, expediente, trecho_interacoes, status_extracao
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (nome, registro, expediente, trecho, status_extracao),
        )
