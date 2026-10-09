"""Acesso somente leitura aos resultados persistidos.

- Conexões com ``default_transaction_read_only`` e ``statement_timeout``.
- SQL sempre parametrizado; termos de LIKE com curingas escapados.
- Relações são lidas de ``interacoes``; a tabela de ligação entra por
  ``LEFT JOIN``/``EXISTS`` e nunca elimina alimentos, classes ou alvos sem
  medicamento associado.
- A vigência vem da view ``processamentos_interacoes_vigentes`` das migrações
  de ``identificacao_interacoes/migracoes`` (a 002 deixa de fora resultados em
  revisão e versões retiradas). A API não cria nem altera esquema.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from collections.abc import Iterable, Sequence

from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from identificacao_interacoes.modelos import chave_bloqueio, partes_chave_bloqueio

from .configuracao import ConfiguracaoApi
from .erros import BancoIndisponivelError, EsquemaAusenteError


LOGGER = logging.getLogger(__name__)

OBJETOS_NECESSARIOS = (
    "bulas",
    "bulas_interacoes",
    "processamentos_interacoes",
    "interacoes",
    "interacoes_medicamentos",
    "processamentos_interacoes_vigentes",
    "processamentos_interacoes_registrados",
)

NOME_EXIBICAO = "COALESCE(NULLIF(BTRIM({t}.nome_anvisa), ''), {t}.nome_normalizado)"
HASH_ATUAL = "ENCODE(SHA256(CONVERT_TO(bi.trecho_interacoes, 'UTF8')), 'hex')"

SQL_SITUACAO = f"""
SELECT
    b.nome_normalizado,
    {NOME_EXIBICAO.format(t="b")} AS nome_exibicao,
    b.status AS status_bula,
    b.principios_ativos,
    b.numero_registro,
    b.expediente,
    b.sha256_pdf,
    b.data_publicacao_anvisa,
    bi.nome_normalizado IS NOT NULL AS tem_extracao,
    bi.status_extracao,
    NULLIF(BTRIM(bi.trecho_interacoes), '') IS NOT NULL AS tem_texto,
    (bi.numero_registro IS NOT DISTINCT FROM b.numero_registro
     AND bi.expediente IS NOT DISTINCT FROM b.expediente) AS extracao_coerente,
    v.id AS processamento_id,
    v.status AS status_processamento,
    v.concluido_em,
    v.versao_processamento,
    v.modelo,
    v.numero_registro AS registro_processado,
    v.expediente AS expediente_processado,
    v.sha256_pdf AS sha256_processado,
    v.data_publicacao_anvisa AS publicacao_processada,
    ultima.status AS ultimo_status_fonte_atual,
    EXISTS (
        SELECT 1 FROM processamentos_interacoes AS h
        WHERE h.medicamento_origem = b.nome_normalizado
    ) AS tem_historico,
    EXISTS (
        SELECT 1 FROM processamentos_interacoes AS r
        WHERE r.medicamento_origem = b.nome_normalizado
          AND r.invalidado_em IS NOT NULL
          AND bi.trecho_interacoes IS NOT NULL
          AND r.hash_trecho = {HASH_ATUAL}
          AND r.numero_registro IS NOT DISTINCT FROM b.numero_registro
          AND r.expediente IS NOT DISTINCT FROM b.expediente
          AND r.sha256_pdf IS NOT DISTINCT FROM b.sha256_pdf
    ) AS tem_retirada_fonte_atual
FROM bulas AS b
LEFT JOIN bulas_interacoes AS bi ON bi.nome_normalizado = b.nome_normalizado
LEFT JOIN processamentos_interacoes_vigentes AS v ON v.medicamento_origem = b.nome_normalizado
    AND v.status IN ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES')
LEFT JOIN LATERAL (
    SELECT p.status
    FROM processamentos_interacoes AS p
    WHERE p.medicamento_origem = b.nome_normalizado
      AND p.invalidado_em IS NULL
      AND bi.trecho_interacoes IS NOT NULL
      AND p.hash_trecho = {HASH_ATUAL}
      AND p.numero_registro IS NOT DISTINCT FROM b.numero_registro
      AND p.expediente IS NOT DISTINCT FROM b.expediente
      AND p.sha256_pdf IS NOT DISTINCT FROM b.sha256_pdf
    ORDER BY p.id DESC
    LIMIT 1
) AS ultima ON TRUE
WHERE b.nome_normalizado = ANY(%s)
"""

COLUNAS_RELACAO = f"""
    i.id,
    i.medicamento_origem,
    i.alvo,
    i.tipo_alvo,
    i.resumo,
    i.evidencia,
    {NOME_EXIBICAO.format(t="bo")} AS origem_exibicao,
    v.id AS processamento_id,
    v.status AS status_processamento,
    v.concluido_em,
    v.versao_processamento,
    v.modelo,
    v.numero_registro,
    v.expediente,
    v.sha256_pdf,
    v.data_publicacao_anvisa
"""

# Vigência restrita aos candidatos: a condição sobre medicamento_origem é
# empurrada para dentro da view (DISTINCT ON), evitando calcular o hash de
# todas as bulas a cada consulta.
SQL_RELACOES_MEDICAMENTO = f"""
WITH candidatas AS (
    SELECT %(nome)s::text AS medicamento_origem
    UNION
    SELECT i.medicamento_origem
    FROM interacoes_medicamentos AS im
    JOIN interacoes AS i ON i.id = im.interacao_id
    WHERE im.medicamento_relacionado = %(nome)s
      AND im.invalidado_em IS NULL
),
vigentes AS (
    SELECT *
    FROM processamentos_interacoes_vigentes
    WHERE medicamento_origem = ANY(ARRAY(SELECT medicamento_origem FROM candidatas))
      AND status IN ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES')
),
relacoes AS (
    SELECT 0 AS grupo, {COLUNAS_RELACAO}
    FROM interacoes AS i
    JOIN vigentes AS v ON v.id = i.processamento_id
    JOIN bulas AS bo ON bo.nome_normalizado = i.medicamento_origem
    WHERE i.medicamento_origem = %(nome)s
    UNION ALL
    SELECT 1 AS grupo, {COLUNAS_RELACAO}
    FROM interacoes AS i
    JOIN vigentes AS v ON v.id = i.processamento_id
    JOIN bulas AS bo ON bo.nome_normalizado = i.medicamento_origem
    WHERE i.medicamento_origem <> %(nome)s
      AND EXISTS (
          SELECT 1 FROM interacoes_medicamentos AS im
          WHERE im.interacao_id = i.id
            AND im.medicamento_relacionado = %(nome)s
            AND im.invalidado_em IS NULL
      )
)
SELECT r.*, COUNT(*) OVER () AS total
FROM relacoes AS r
ORDER BY r.grupo, r.medicamento_origem, r.id
LIMIT %(limite)s OFFSET %(deslocamento)s
"""

SQL_CONTAR_RELACOES = SQL_RELACOES_MEDICAMENTO.split("SELECT r.*, COUNT(*) OVER () AS total")[0] + (
    "SELECT COUNT(*) AS total FROM relacoes"
)
SQL_RELACOES_PARA_CONTAGEM = SQL_RELACOES_MEDICAMENTO.split(
    "SELECT r.*, COUNT(*) OVER () AS total"
)[0] + "SELECT grupo, medicamento_origem, alvo, evidencia FROM relacoes"

SQL_RELACOES_PAR = f"""
WITH vigentes AS (
    SELECT *
    FROM processamentos_interacoes_vigentes
    WHERE medicamento_origem = ANY(%(nomes)s)
      AND status IN ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES')
)
SELECT
    {COLUNAS_RELACAO},
    im.criterio_vinculo,
    im.fundamento_vinculo,
    CASE WHEN i.medicamento_origem = %(a)s THEN 'BULA_A_CITA_B' ELSE 'BULA_B_CITA_A' END AS sentido_par
FROM interacoes AS i
JOIN vigentes AS v ON v.id = i.processamento_id
JOIN bulas AS bo ON bo.nome_normalizado = i.medicamento_origem
LEFT JOIN interacoes_medicamentos AS im
    ON im.interacao_id = i.id
   AND im.invalidado_em IS NULL
   AND im.medicamento_relacionado = CASE WHEN i.medicamento_origem = %(a)s THEN %(b)s ELSE %(a)s END
WHERE im.interacao_id IS NOT NULL
   OR i.tipo_alvo IN ('CLASSE_FARMACOLOGICA', 'PRINCIPIO_ATIVO', 'MEDICAMENTO')
ORDER BY sentido_par, i.medicamento_origem, i.id
"""

SQL_RELACIONADOS = f"""
SELECT interacao_id, medicamento_relacionado, nome_exibicao, criterio_vinculo, total
FROM (
    SELECT
        im.interacao_id,
        im.medicamento_relacionado,
        {NOME_EXIBICAO.format(t="b")} AS nome_exibicao,
        im.criterio_vinculo,
        COUNT(*) OVER (PARTITION BY im.interacao_id) AS total,
        ROW_NUMBER() OVER (PARTITION BY im.interacao_id ORDER BY im.medicamento_relacionado) AS posicao
    FROM interacoes_medicamentos AS im
    JOIN bulas AS b ON b.nome_normalizado = im.medicamento_relacionado
    WHERE im.interacao_id = ANY(%s)
      AND im.invalidado_em IS NULL
) AS ordenados
WHERE posicao <= %s
ORDER BY interacao_id, medicamento_relacionado
"""

SQL_COBERTURA = """
SELECT
    (SELECT COUNT(*)
     FROM bulas_interacoes AS bi
     JOIN bulas AS b ON b.nome_normalizado = bi.nome_normalizado
     WHERE bi.status_extracao = 'CONCLUIDO'
       AND NULLIF(BTRIM(bi.trecho_interacoes), '') IS NOT NULL) AS elegiveis,
    (SELECT COUNT(*)
     FROM processamentos_interacoes_vigentes AS v
     WHERE v.status IN ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES')) AS concluidas,
    -- Em revisão: o último resultado da fonte atual pede revisão e não há
    -- versão concluída vigente (resultados em revisão não são vigentes).
    (SELECT COUNT(*)
     FROM processamentos_interacoes_registrados AS r
     WHERE r.status = 'REVISAO_NECESSARIA'
       AND NOT EXISTS (
           SELECT 1 FROM processamentos_interacoes_vigentes AS v
           WHERE v.medicamento_origem = r.medicamento_origem
       )) AS em_revisao
"""


def escapar_like(termo: str) -> str:
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class RepositorioConsulta:
    def __init__(self, configuracao: ConfiguracaoApi) -> None:
        opcoes = (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={configuracao.tempo_limite_consulta_ms} "
            "-c application_name=api_interacoes"
        )
        self.pool = ConnectionPool(
            configuracao.dsn,
            min_size=configuracao.pool_minimo,
            max_size=configuracao.pool_maximo,
            kwargs={"row_factory": dict_row, "options": opcoes, "autocommit": True},
            open=False,
            timeout=5,
            name="api_interacoes",
        )

    def abrir(self) -> None:
        # Não espera o banco: a API sobe e informa a indisponibilidade em /saude.
        self.pool.open(wait=False)

    def fechar(self) -> None:
        self.pool.close()

    def _consultar(self, sql: str, parametros: Sequence | dict | None = None) -> list[dict]:
        try:
            with self.pool.connection() as conexao:
                return list(conexao.execute(sql, parametros).fetchall())
        except (errors.UndefinedTable, errors.UndefinedColumn) as erro:
            LOGGER.exception("Esquema ausente: %r", erro)
            raise EsquemaAusenteError(
                "O serviço de consulta ainda não está preparado; aplique as migrações."
            ) from erro
        except Exception as erro:
            LOGGER.exception("Falha de consulta: %r", erro)
            raise BancoIndisponivelError("O serviço de consulta está indisponível no momento.") from erro

    # ------------------------------------------------------------ consultas

    def verificar(self) -> tuple[bool, bool]:
        """(banco disponível, esquema completo)."""
        try:
            linhas = self._consultar(
                "SELECT nome FROM UNNEST(%s::text[]) AS nome "
                "WHERE TO_REGCLASS('public.' || nome) IS NULL",
                (list(OBJETOS_NECESSARIOS),),
            )
        except (BancoIndisponivelError, EsquemaAusenteError):
            return False, False
        return True, not linhas

    def sugerir(self, termo_normalizado: str, limite: int) -> list[dict]:
        termo = escapar_like(termo_normalizado)
        resultados = self._consultar(
            f"""
            SELECT nome_normalizado, {NOME_EXIBICAO.format(t="b")} AS nome_exibicao
            FROM bulas AS b
            WHERE b.status = 'CONCLUIDO'
              AND b.nome_normalizado LIKE %(contem)s ESCAPE '\\'
            ORDER BY
                CASE
                    WHEN b.nome_normalizado LIKE %(prefixo)s ESCAPE '\\' THEN 0
                    WHEN b.nome_normalizado LIKE %(palavra)s ESCAPE '\\' THEN 1
                    ELSE 2
                END,
                b.nome_normalizado
            LIMIT %(limite)s
            """,
            {
                "contem": f"%{termo}%",
                "prefixo": f"{termo}%",
                "palavra": f"% {termo}%",
                "limite": limite,
            },
        )
        if resultados or len(termo_normalizado) < 4:
            return resultados

        # Busca aproximada só quando não há correspondência literal. A consulta
        # principal continua rápida e a pontuação não depende de extensões SQL.
        from scraping_anvisa.anvisa_scraper.normalizacao import normalizar_nome

        candidatos = self._consultar(
            f"""SELECT nome_normalizado, {NOME_EXIBICAO.format(t="b")} AS nome_exibicao
                FROM bulas AS b WHERE b.status = 'CONCLUIDO'"""
        )
        def distancia_edicao(a: str, b: str) -> int:
            anterior = list(range(len(b) + 1))
            for indice, caractere in enumerate(a, 1):
                atual = [indice]
                for coluna, outro in enumerate(b, 1):
                    atual.append(min(atual[-1] + 1, anterior[coluna] + 1,
                                     anterior[coluna - 1] + (caractere != outro)))
                anterior = atual
            return anterior[-1]

        def distancia_prefixo(nome: str) -> int:
            melhor = len(termo_normalizado) + 1
            for palavra in (nome.split() or [nome]):
                for tamanho in range(max(1, len(termo_normalizado) - 2), min(len(palavra), len(termo_normalizado) + 2) + 1):
                    prefixo = palavra[:tamanho]
                    melhor = min(melhor, distancia_edicao(termo_normalizado, prefixo))
            return melhor
        maximo = 1 if len(termo_normalizado) < 7 else 2
        classificados = []
        for candidato in candidatos:
            chaves = {candidato["nome_normalizado"], normalizar_nome(candidato["nome_exibicao"])}
            distancia = min(distancia_prefixo(chave) for chave in chaves)
            if distancia <= maximo:
                palavras = [p for chave in chaves for p in chave.split() if p]
                afinidade = max(SequenceMatcher(None, termo_normalizado, p).ratio() for p in palavras)
                classificados.append((distancia, -afinidade,
                                      len(candidato["nome_normalizado"].split()),
                                      candidato["nome_normalizado"], candidato))
        classificados.sort(key=lambda item: item[:-1])
        return [item[-1] for item in classificados[:limite]]

    def situacoes(self, nomes: Iterable[str]) -> dict[str, dict]:
        linhas = self._consultar(SQL_SITUACAO, (list(nomes),))
        return {linha["nome_normalizado"]: linha for linha in linhas}

    def em_processamento(self, nomes: Iterable[str]) -> set[str]:
        """Medicamentos com advisory lock da identificação ativo agora."""
        por_chave = {partes_chave_bloqueio(chave_bloqueio(nome)): nome for nome in nomes}
        linhas = self._consultar(
            """
            SELECT classid::bigint AS classid, objid::bigint AS objid
            FROM pg_locks
            WHERE locktype = 'advisory' AND objsubid = 1 AND granted
              -- Advisory locks valem por banco: só os deste banco contam.
              AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
            """
        )
        return {
            por_chave[(linha["classid"], linha["objid"])]
            for linha in linhas
            if (linha["classid"], linha["objid"]) in por_chave
        }

    def relacoes_do_medicamento(self, nome: str, limite: int, deslocamento: int) -> tuple[list[dict], int]:
        parametros = {"nome": nome, "limite": limite, "deslocamento": deslocamento}
        linhas = self._consultar(SQL_RELACOES_MEDICAMENTO, parametros)
        if linhas:
            return linhas, int(linhas[0]["total"])
        total = self._consultar(SQL_CONTAR_RELACOES, parametros)[0]["total"]
        return [], int(total)

    def relacoes_para_contagem(self, nome: str) -> list[dict]:
        """Campos mínimos de todas as relações para contar apenas as exibíveis."""
        return self._consultar(SQL_RELACOES_PARA_CONTAGEM, {"nome": nome})

    def relacoes_do_par(self, a: str, b: str) -> list[dict]:
        return self._consultar(SQL_RELACOES_PAR, {"a": a, "b": b, "nomes": [a, b]})

    def relacionados(self, interacoes: Sequence[int], limite: int) -> dict[int, tuple[list[dict], int]]:
        if not interacoes:
            return {}
        saida: dict[int, tuple[list[dict], int]] = {}
        for linha in self._consultar(SQL_RELACIONADOS, (list(interacoes), limite)):
            lista, _ = saida.get(linha["interacao_id"], ([], 0))
            lista.append(linha)
            saida[linha["interacao_id"]] = (lista, int(linha["total"]))
        return saida

    def cobertura(self) -> dict:
        return self._consultar(SQL_COBERTURA)[0]
