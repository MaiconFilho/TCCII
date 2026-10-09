"""Aplica as migrações SQL deste módulo, em ordem, cada uma em uma transação.

Uso, a partir da raiz do projeto::

    python -m identificacao_interacoes.aplicar_migracoes

As migrações são aditivas e idempotentes (CREATE ... IF NOT EXISTS, CREATE OR
REPLACE VIEW, COMMENT). Nenhum comando destrutivo é aceito: o arquivo é
recusado antes da execução se contiver DROP, TRUNCATE, DELETE ou ALTER ... DROP.
A única exceção é trocar uma regra CHECK por outra de mesmo nome na mesma
instrução (``DROP CONSTRAINT IF EXISTS ck_x, ADD CONSTRAINT ck_x CHECK``), que
não apaga dados. A API nunca executa este script.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv


BASE_MODULO = Path(__file__).resolve().parent
PASTA_MIGRACOES = BASE_MODULO / "migracoes"
COMANDOS_PROIBIDOS = re.compile(r"\b(?:DROP|TRUNCATE|DELETE)\b", re.I)
# Troca de uma regra CHECK pela nova versão, com o mesmo nome, na mesma instrução.
TROCA_DE_CHECK = re.compile(
    r"\bDROP\s+CONSTRAINT\s+IF\s+EXISTS\s+(ck_\w+)\s*,\s*ADD\s+CONSTRAINT\s+\1\s+CHECK\b", re.I
)


def remover_comentarios(sql: str) -> str:
    sem_bloco = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sem_linha = re.sub(r"--[^\n]*", " ", sem_bloco)
    # Literais (comentários SQL em COMMENT ON) e ações de chave estrangeira
    # (ON DELETE RESTRICT) não são comandos.
    sem_literais = re.sub(r"'(?:[^']|'')*'", "''", sem_linha)
    return re.sub(r"\bON\s+(?:DELETE|UPDATE)\b", " ", sem_literais, flags=re.I)


def listar_migracoes(pasta: Path = PASTA_MIGRACOES) -> list[Path]:
    return sorted(pasta.glob("[0-9][0-9][0-9]_*.sql"))


def conferir_aditiva(caminho: Path) -> str:
    sql = caminho.read_text(encoding="utf-8")
    comandos = TROCA_DE_CHECK.sub(r"ADD CONSTRAINT \1 CHECK", remover_comentarios(sql))
    proibido = COMANDOS_PROIBIDOS.search(comandos)
    if proibido:
        raise ValueError(
            f"{caminho.name} contém o comando {proibido.group(0).upper()}; "
            "migrações deste módulo devem ser aditivas."
        )
    return sql


def aplicar(dsn: str, migracoes: list[Path]) -> list[str]:
    aplicadas: list[str] = []
    conteudos = [(caminho, conferir_aditiva(caminho)) for caminho in migracoes]
    with psycopg.connect(dsn, autocommit=True) as conexao:
        for caminho, sql in conteudos:
            with conexao.transaction():
                conexao.execute(sql)
            aplicadas.append(caminho.name)
    return aplicadas


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aplica as migrações de identificacao_interacoes.")
    parser.add_argument(
        "--listar", action="store_true", help="Mostra as migrações sem executá-las."
    )
    args = parser.parse_args(argumentos)
    migracoes = listar_migracoes()
    if args.listar:
        for caminho in migracoes:
            print(caminho.name)
        return 0

    load_dotenv(BASE_MODULO / ".env")
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL não configurada. Ajuste identificacao_interacoes/.env.")
        return 4
    try:
        aplicadas = aplicar(dsn, migracoes)
    except ValueError as erro:
        print(f"Migração recusada: {erro}")
        return 2
    except psycopg.Error as erro:
        print(f"Falha ao aplicar as migrações: {type(erro).__name__}: {erro}")
        return 4
    for nome in aplicadas:
        print(f"Aplicada: {nome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
