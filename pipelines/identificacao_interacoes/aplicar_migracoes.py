"""Entrada compatível da instalação: baseline modelo60/v1.

Os arquivos 001–007 permanecem como histórico. O executor nunca os reaplica sobre
a baseline final. A criação completa fica em banco/modelo.sql.
As funções de inspeção da aditividade servem à auditoria dos arquivos históricos.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


BASE_MODULO = Path(__file__).resolve().parent
PASTA_MIGRACOES = BASE_MODULO / "migracoes"
SQL_MODELO = BASE_MODULO.parents[1] / "banco" / "modelo.sql"


def ler_modelo() -> dict[str, dict[str, str]]:
    """Lê os campos do SQL canônico sem executar DDL na importação/API."""
    texto = SQL_MODELO.read_text(encoding="utf-8")
    tabelas = re.findall(r"CREATE TABLE public\.(\w+) \((.*?)^\);", texto, re.M | re.S)
    modelo = {
        tabela: dict(re.findall(
            r"^\s+(\w+)\s+(TEXT|BIGINT|INTEGER|NUMERIC|FLOAT8|TIMESTAMPTZ|JSONB)\b",
            corpo, re.M,
        ))
        for tabela, corpo in tabelas
    }
    contagens = {"bulas": 9, "bulas_interacoes": 7, "processamentos_interacoes": 24,
                 "interacoes": 16, "interacoes_medicamentos": 4}
    if {t: len(campos) for t, campos in modelo.items()} != contagens:
        raise ValueError("banco/modelo.sql deve definir as cinco tabelas e os 60 campos aprovados.")
    return modelo


MODELO = ler_modelo()
FKS = (
    ("bulas_interacoes", "nome_normalizado", "bulas", "nome_normalizado"),
    ("processamentos_interacoes", "medicamento_origem", "bulas", "nome_normalizado"),
    ("interacoes", "medicamento_origem", "bulas", "nome_normalizado"),
    ("interacoes", "processamento_id", "processamentos_interacoes", "id"),
    ("interacoes_medicamentos", "interacao_id", "interacoes", "id"),
    ("interacoes_medicamentos", "medicamento_relacionado", "bulas", "nome_normalizado"),
)


def colunas(conexao):
    return conexao.execute("""SELECT table_name,column_name,udt_name,is_nullable
        FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s)
        ORDER BY table_name,ordinal_position""", (list(MODELO),)).fetchall()


def validar_esquema(conexao) -> None:
    """Confere a instalação existente sem recriar colunas ou modificar dados."""
    campos = colunas(conexao)
    tipos = {"TEXT": "text", "BIGINT": "int8", "INTEGER": "int4", "NUMERIC": "numeric",
             "FLOAT8": "float8", "TIMESTAMPTZ": "timestamptz", "JSONB": "jsonb"}
    esperado = {(t, f): tipos[tipo] for t, campos_tabela in MODELO.items()
                for f, tipo in campos_tabela.items()}
    atual = {(c["table_name"], c["column_name"]): c["udt_name"] for c in campos}
    if atual != esperado:
        raise ValueError("Schema incompatível com as cinco tabelas/60 campos aprovados.")
    for tabela, campo, _, _ in FKS:
        if not any(c["table_name"] == tabela and c["column_name"] == campo
                   and c["is_nullable"] == "NO" for c in campos):
            raise ValueError(f"FK aceita NULL: {tabela}.{campo}.")
    fks = conexao.execute("""SELECT r.relname AS tabela,a.attname AS campo,
        p.relname AS pai,b.attname AS campo_pai,c.confdeltype,c.confupdtype
        FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
        JOIN pg_class p ON p.oid=c.confrelid
        JOIN pg_attribute a ON a.attrelid=r.oid AND a.attnum=c.conkey[1]
        JOIN pg_attribute b ON b.attrelid=p.oid AND b.attnum=c.confkey[1]
        WHERE c.contype='f' AND c.conrelid IN
        (SELECT to_regclass('public.'||t) FROM unnest(%s::text[]) t)""", (list(MODELO),)).fetchall()
    if (len(fks) != 6 or {(f['tabela'], f['campo'], f['pai'], f['campo_pai']) for f in fks} != set(FKS)
            or any(f['confdeltype'] not in ('a', 'r') or f['confupdtype'] not in ('a', 'r') for f in fks)):
        raise ValueError("As seis FKs protetoras não conferem.")
    for nome in ("processamentos_interacoes_vigentes", "processamentos_interacoes_registrados"):
        view = conexao.execute("""SELECT obj_description(oid,'pg_class') AS comentario,reloptions
            FROM pg_class WHERE oid=to_regclass(%s)""", ('public.'+nome,)).fetchone()
        if (not view or not (view['comentario'] or '').lower().startswith('modelo60/v1:')
                or 'security_invoker=true' not in (view['reloptions'] or [])):
            raise ValueError("View/versão/segurança incompatível: "+nome)
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
    # Arquivos hist?ricos ficam dispon?veis para auditoria/teste da atualiza??o.
    # Nunca s?o reaplicados no fluxo final (recriariam colunas e a fila legada).
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conexao:
        with conexao.transaction():
            conexao.execute('SELECT pg_advisory_xact_lock(670060001)')
            nova = not colunas(conexao)
            if nova:
                conexao.execute(SQL_MODELO.read_text(encoding="utf-8"))
            try:
                validar_esquema(conexao)
            except ValueError as exc:
                raise ValueError("Base existente incompatível: faça backup e uma migração explícita validada. O SQL de criação não deve ser aplicado sobre dados existentes.") from exc
            if nova:
                return ["modelo60/v1 (baseline canônica)"]
    return []


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
        print("DATABASE_URL não configurada. Ajuste pipelines/identificacao_interacoes/.env.")
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
