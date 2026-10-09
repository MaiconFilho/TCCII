"""Persistência da identificação no PostgreSQL (psycopg 3).

- O esquema é criado somente pelas migrações (``migracoes/``); este módulo
  apenas confere que ele existe.
- Processos independentes são coordenados por medicamento com advisory locks
  do PostgreSQL: um bloqueio de sessão durante a inferência e um bloqueio de
  transação na gravação.
- A gravação ocorre em uma única transação curta, depois da inferência, com
  a fonte revalidada (texto, registro, expediente e PDF). Nada parcial é
  gravado; uma falha registra apenas a linha de controle.
- Nenhum método executa DELETE: vínculos que deixam de valer recebem
  ``invalidado_em``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .erros import (
    ErroBancoError,
    EsquemaBancoIncompativelError,
    FonteAlteradaError,
    MedicamentoEmAndamentoError,
    ProcessamentoJaExisteError,
)
from .modelos import (
    FonteInteracoes,
    ResultadoIdentificacao,
    StatusProcessamento,
    Vinculo,
    chave_bloqueio,
)
from .resolucao import MedicamentoCatalogo


LOGGER = logging.getLogger(__name__)

OBJETOS_MIGRACAO = (
    "processamentos_interacoes",
    "interacoes",
    "interacoes_medicamentos",
    "processamentos_interacoes_vigentes",
    "processamentos_interacoes_registrados",
)
COLUNAS_BULAS = (
    "nome_normalizado", "nome_anvisa", "principios_ativos", "composicao",
    "numero_registro", "expediente", "sha256_pdf", "id_produto",
    "data_publicacao_anvisa", "status",
)
COLUNAS_BULAS_INTERACOES = (
    "nome_normalizado", "numero_registro", "expediente", "trecho_interacoes", "status_extracao",
)
TEMPO_ESPERA_BLOQUEIO = "10s"

SQL_HASH_TRECHO = "ENCODE(SHA256(CONVERT_TO(bi.trecho_interacoes, 'UTF8')), 'hex')"

SQL_FONTES = f"""
    SELECT
        b.nome_normalizado,
        COALESCE(NULLIF(BTRIM(b.nome_anvisa), ''), b.nome_normalizado) AS nome_exibicao,
        b.principios_ativos,
        bi.trecho_interacoes,
        b.numero_registro,
        b.expediente,
        b.sha256_pdf,
        b.id_produto,
        b.data_publicacao_anvisa,
        {SQL_HASH_TRECHO} AS hash_trecho_banco,
        (bi.numero_registro IS NOT DISTINCT FROM b.numero_registro
         AND bi.expediente IS NOT DISTINCT FROM b.expediente) AS identificadores_coerentes
    FROM bulas_interacoes AS bi
    JOIN bulas AS b ON b.nome_normalizado = bi.nome_normalizado
    WHERE bi.status_extracao = 'CONCLUIDO'
      AND NULLIF(BTRIM(bi.trecho_interacoes), '') IS NOT NULL
"""


class RepositorioIdentificacao:
    def __init__(self, dsn: str) -> None:
        try:
            self.conexao = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
            self.validar_esquema()
        except EsquemaBancoIncompativelError:
            self._fechar_apos_falha()
            raise
        except Exception as erro:
            self._fechar_apos_falha()
            raise ErroBancoError(
                f"Falha ao preparar o PostgreSQL: {type(erro).__name__}: {erro}"
            ) from erro

    @classmethod
    def com_conexao(cls, conexao: Any) -> "RepositorioIdentificacao":
        """Usa uma conexão existente (testes e integrações)."""
        repositorio = cls.__new__(cls)
        repositorio.conexao = conexao
        return repositorio

    def _fechar_apos_falha(self) -> None:
        conexao = getattr(self, "conexao", None)
        if conexao is not None:
            conexao.close()

    def fechar(self) -> None:
        self.conexao.close()

    # ------------------------------------------------------------- esquema

    def validar_esquema(self) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN ('bulas', 'bulas_interacoes')
                """
            )
            colunas: dict[str, set[str]] = {}
            for linha in cursor.fetchall():
                colunas.setdefault(linha["table_name"], set()).add(linha["column_name"])
            for tabela, esperadas in (
                ("bulas", COLUNAS_BULAS),
                ("bulas_interacoes", COLUNAS_BULAS_INTERACOES),
            ):
                ausentes = set(esperadas) - colunas.get(tabela, set())
                if ausentes:
                    raise EsquemaBancoIncompativelError(
                        f"Colunas ausentes em {tabela}: {', '.join(sorted(ausentes))}."
                    )
            cursor.execute(
                "SELECT nome FROM UNNEST(%s::text[]) AS nome WHERE TO_REGCLASS('public.' || nome) IS NULL",
                (list(OBJETOS_MIGRACAO),),
            )
            faltantes = [linha["nome"] for linha in cursor.fetchall()]
        if faltantes:
            raise EsquemaBancoIncompativelError(
                "Objetos da identificação ausentes: " + ", ".join(faltantes)
                + ". Execute antes: python -m identificacao_interacoes.aplicar_migracoes"
            )

    # ------------------------------------------------------------ seleção

    def selecionar_fontes(
        self,
        inicio: int = 0,
        limite: int | None = 1,
        nome_normalizado: str | None = None,
        reprocessar: bool = False,
        versao: str | None = None,
    ) -> list[FonteInteracoes]:
        """Extrações CONCLUIDO com texto.

        Em lote, ignora quem já tem resultado registrado para a fonte atual,
        concluído ou em revisão (retomada sem duplicação); com ``versao``,
        ignora só quem já foi registrado nessa versão. Pelo nome, a linha
        sempre volta e a decisão fica com ``processamento_registrado``. Nunca
        processados vêm primeiro.
        """
        filtro_vigente = nome_normalizado is None and not reprocessar
        with self.conexao.cursor() as cursor:
            cursor.execute(
                SQL_FONTES
                + """
                  AND (CAST(%s AS TEXT) IS NULL OR b.nome_normalizado = %s)
                  AND (
                      NOT %s
                      OR NOT EXISTS (
                          SELECT 1 FROM processamentos_interacoes_registrados AS v
                          WHERE v.medicamento_origem = b.nome_normalizado
                            AND (CAST(%s AS TEXT) IS NULL OR v.versao_processamento = %s)
                      )
                  )
                ORDER BY
                    EXISTS (
                        SELECT 1 FROM processamentos_interacoes AS p
                        WHERE p.medicamento_origem = b.nome_normalizado
                    ),
                    b.nome_normalizado
                OFFSET %s
                LIMIT %s
                """,
                (nome_normalizado, nome_normalizado, filtro_vigente, versao, versao, inicio, limite),
            )
            return [FonteInteracoes(**linha) for linha in cursor.fetchall()]

    def carregar_catalogo(self) -> list[MedicamentoCatalogo]:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    nome_normalizado,
                    COALESCE(NULLIF(BTRIM(nome_anvisa), ''), nome_normalizado) AS nome_exibicao,
                    principios_ativos,
                    composicao
                FROM bulas
                ORDER BY nome_normalizado
                """
            )
            return [MedicamentoCatalogo(**linha) for linha in cursor.fetchall()]

    def processamento_vigente(self, nome_normalizado: str) -> dict | None:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, versao_processamento, concluido_em
                FROM processamentos_interacoes_vigentes
                WHERE medicamento_origem = %s
                """,
                (nome_normalizado,),
            )
            return cursor.fetchone()

    def processamento_registrado(self, nome_normalizado: str) -> dict | None:
        """Último resultado não retirado da fonte atual, inclusive em revisão.

        Decide só se é preciso processar de novo; a publicação usa a vigência.
        """
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, versao_processamento, concluido_em
                FROM processamentos_interacoes_registrados
                WHERE medicamento_origem = %s
                """,
                (nome_normalizado,),
            )
            return cursor.fetchone()

    def invalidar_processamento(self, processamento_id: int, motivo: str) -> None:
        """Retira somente a versão explicitamente reconhecida como incorreta.

        Não é chamado por falhas de inferência nem por resultados em revisão.
        A fonte e as relações históricas permanecem intactas.
        """
        if not (motivo or "").strip():
            raise ValueError("A retirada precisa de um motivo explícito.")
        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    cursor.execute("SELECT medicamento_origem FROM processamentos_interacoes WHERE id = %s",
                                   (processamento_id,))
                    linha = cursor.fetchone()
                    if linha is None:
                        raise ValueError("Processamento não encontrado.")
                    self._bloquear_transacao(cursor, linha['medicamento_origem'], esperar=True)
                    cursor.execute("""
                        UPDATE processamentos_interacoes
                        SET invalidado_em = COALESCE(invalidado_em, NOW()),
                            motivo_invalidacao = COALESCE(motivo_invalidacao, %s)
                        WHERE id = %s
                    """, (motivo.strip(), processamento_id))
        except ValueError:
            raise
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao retirar o processamento {processamento_id}: {type(erro).__name__}: {erro}"
            ) from erro

    # ---------------------------------------------------------- bloqueios

    def bloquear_medicamento(self, nome_normalizado: str) -> bool:
        """Bloqueio de sessão; liberado também se o processo terminar."""
        with self.conexao.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s) AS obtido", (chave_bloqueio(nome_normalizado),)
            )
            return bool(cursor.fetchone()["obtido"])

    def liberar_medicamento(self, nome_normalizado: str) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s) AS liberado", (chave_bloqueio(nome_normalizado),)
            )

    # ----------------------------------------------------------- gravação

    @staticmethod
    def _bloquear_transacao(cursor, nome_normalizado: str, *, esperar: bool) -> None:
        cursor.execute(f"SET LOCAL lock_timeout = '{TEMPO_ESPERA_BLOQUEIO}'")
        if esperar:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (chave_bloqueio(nome_normalizado),))
            return
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS obtido", (chave_bloqueio(nome_normalizado),)
        )
        if not cursor.fetchone()["obtido"]:
            raise MedicamentoEmAndamentoError(
                f"'{nome_normalizado}' está sendo processado por outro processo."
            )

    @staticmethod
    def _revalidar_fonte(cursor, fonte: FonteInteracoes) -> None:
        cursor.execute(
            f"""
            SELECT
                bi.status_extracao,
                {SQL_HASH_TRECHO} AS hash_trecho,
                bi.numero_registro AS registro_extracao,
                bi.expediente AS expediente_extracao,
                b.numero_registro,
                b.expediente,
                b.sha256_pdf
            FROM bulas AS b
            JOIN bulas_interacoes AS bi ON bi.nome_normalizado = b.nome_normalizado
            WHERE b.nome_normalizado = %s
            FOR SHARE OF b, bi
            """,
            (fonte.nome_normalizado,),
        )
        atual = cursor.fetchone()
        if atual is None:
            raise FonteAlteradaError("A bula ou a extração não existe mais.")
        divergencias = []
        if atual["status_extracao"] != "CONCLUIDO":
            divergencias.append(f"status_extracao={atual['status_extracao']}")
        if atual["hash_trecho"] != fonte.hash_trecho:
            divergencias.append("trecho_interacoes alterado")
        for campo, valor in (
            ("numero_registro", fonte.numero_registro),
            ("expediente", fonte.expediente),
            ("sha256_pdf", fonte.sha256_pdf),
        ):
            if atual[campo] != valor:
                divergencias.append(f"{campo} alterado")
        if (atual["registro_extracao"], atual["expediente_extracao"]) != (
            atual["numero_registro"], atual["expediente"]
        ):
            divergencias.append("extração incoerente com a bula")
        if divergencias:
            raise FonteAlteradaError(
                "A fonte mudou desde a seleção: " + "; ".join(divergencias) + "."
            )

    @staticmethod
    def _inserir_processamento(
        cursor,
        fonte: FonteInteracoes,
        status: StatusProcessamento,
        resultado: ResultadoIdentificacao | None,
        *,
        versao: str,
        modelo: str,
        iniciado_em: datetime,
        tempo_total_segundos: float,
        detalhe: str,
    ) -> int:
        relacoes = 0 if status == StatusProcessamento.FALHA or resultado is None else len(resultado.relacoes)
        cursor.execute(
            """
            INSERT INTO processamentos_interacoes (
                medicamento_origem, status, versao_processamento, modelo,
                hash_trecho, caracteres_trecho, numero_registro, expediente,
                sha256_pdf, id_produto, data_publicacao_anvisa,
                quantidade_janelas, janelas_concluidas, quantidade_relacoes,
                quantidade_rejeicoes, chamadas_llm, tempo_inferencia_segundos,
                tempo_total_segundos, pico_memoria_mb, detalhe, iniciado_em
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                fonte.nome_normalizado,
                status.value,
                versao,
                modelo,
                fonte.hash_trecho,
                len(fonte.trecho_interacoes or ""),
                fonte.numero_registro,
                fonte.expediente,
                fonte.sha256_pdf,
                fonte.id_produto,
                fonte.data_publicacao_anvisa,
                resultado.quantidade_janelas if resultado else 0,
                resultado.janelas_concluidas if resultado else 0,
                relacoes,
                len(resultado.rejeicoes) if resultado else 0,
                resultado.chamadas_llm if resultado else 0,
                round(resultado.tempo_inferencia_segundos, 3) if resultado else None,
                round(tempo_total_segundos, 3),
                round(resultado.pico_memoria_mb, 1) if resultado else None,
                (detalhe or None) and detalhe[:2000],
                iniciado_em,
            ),
        )
        return int(cursor.fetchone()["id"])

    def gravar_resultado(
        self,
        fonte: FonteInteracoes,
        resultado: ResultadoIdentificacao,
        vinculos: Sequence[Sequence[Vinculo]],
        *,
        versao: str,
        modelo: str,
        iniciado_em: datetime,
        tempo_total_segundos: float,
        reprocessar: bool = False,
    ) -> tuple[int, int]:
        """Grava controle, relações e vínculos atomicamente. Devolve (id, vínculos)."""
        if resultado.status == StatusProcessamento.FALHA:
            raise ValueError("Use registrar_falha para resultados com FALHA.")
        if len(vinculos) != len(resultado.relacoes):
            raise ValueError("Cada relação precisa da sua lista de vínculos (mesmo vazia).")
        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    self._bloquear_transacao(cursor, fonte.nome_normalizado, esperar=True)
                    self._revalidar_fonte(cursor, fonte)
                    if not reprocessar:
                        cursor.execute(
                            """
                            SELECT id FROM processamentos_interacoes_registrados
                            WHERE medicamento_origem = %s AND versao_processamento = %s
                            """,
                            (fonte.nome_normalizado, versao),
                        )
                        existente = cursor.fetchone()
                        if existente:
                            raise ProcessamentoJaExisteError(
                                f"Processamento {existente['id']} já cobre esta fonte e versão."
                            )
                    processamento_id = self._inserir_processamento(
                        cursor, fonte, resultado.status, resultado,
                        versao=versao, modelo=modelo, iniciado_em=iniciado_em,
                        tempo_total_segundos=tempo_total_segundos,
                        detalhe=resultado.detalhe_erro,
                    )
                    total_vinculos = 0
                    for relacao, lista in zip(resultado.relacoes, vinculos, strict=True):
                        cursor.execute(
                            """
                            INSERT INTO interacoes (
                                medicamento_origem, alvo, tipo_alvo, resumo, evidencia, processamento_id
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            RETURNING id
                            """,
                            (
                                fonte.nome_normalizado, relacao.alvo, relacao.tipo_alvo.value,
                                relacao.resumo, relacao.evidencia, processamento_id,
                            ),
                        )
                        interacao_id = int(cursor.fetchone()["id"])
                        # A interação é nova: só repetições da própria lista conflitam.
                        unicos = {v.medicamento_relacionado: v for v in reversed(lista)}
                        if unicos:
                            cursor.executemany(
                                """
                                INSERT INTO interacoes_medicamentos (
                                    interacao_id, medicamento_relacionado,
                                    criterio_vinculo, fundamento_vinculo
                                )
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (interacao_id, medicamento_relacionado) DO NOTHING
                                """,
                                [
                                    (interacao_id, nome, vinculo.criterio.value, vinculo.fundamento[:2000])
                                    for nome, vinculo in unicos.items()
                                ],
                            )
                            total_vinculos += len(unicos)
                    cursor.execute(
                        "UPDATE processamentos_interacoes SET quantidade_vinculos = %s WHERE id = %s",
                        (total_vinculos, processamento_id),
                    )
        except (FonteAlteradaError, ProcessamentoJaExisteError):
            raise
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao gravar as interações de '{fonte.nome_normalizado}': "
                f"{type(erro).__name__}: {erro}"
            ) from erro
        return processamento_id, total_vinculos

    def registrar_falha(
        self,
        fonte: FonteInteracoes,
        resultado: ResultadoIdentificacao | None,
        *,
        versao: str,
        modelo: str,
        iniciado_em: datetime,
        tempo_total_segundos: float,
        detalhe: str,
    ) -> int:
        """Grava só a linha de controle FALHA; resultados anteriores ficam intactos."""
        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    return self._inserir_processamento(
                        cursor, fonte, StatusProcessamento.FALHA, resultado,
                        versao=versao, modelo=modelo, iniciado_em=iniciado_em,
                        tempo_total_segundos=tempo_total_segundos, detalhe=detalhe,
                    )
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao registrar a falha de '{fonte.nome_normalizado}': "
                f"{type(erro).__name__}: {erro}"
            ) from erro

    # ---------------------------------------------- atualização de vínculos

    def processamentos_para_vinculos(
        self, inicio: int = 0, limite: int | None = 1, nome_normalizado: str | None = None
    ) -> list[dict]:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, medicamento_origem
                FROM processamentos_interacoes_vigentes
                WHERE (CAST(%s AS TEXT) IS NULL OR medicamento_origem = %s)
                ORDER BY medicamento_origem
                OFFSET %s
                LIMIT %s
                """,
                (nome_normalizado, nome_normalizado, inicio, limite),
            )
            return list(cursor.fetchall())

    def interacoes_do_processamento(self, processamento_id: int) -> list[dict]:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, alvo, tipo_alvo
                FROM interacoes
                WHERE processamento_id = %s
                ORDER BY id
                """,
                (processamento_id,),
            )
            return list(cursor.fetchall())

    def atualizar_vinculos(
        self,
        processamento_id: int,
        medicamento_origem: str,
        vinculos_por_interacao: dict[int, list[Vinculo]],
    ) -> dict[str, int]:
        """Sincroniza vínculos com o cadastro atual, sem inferência e sem DELETE."""
        contagem = {"criados": 0, "reativados": 0, "atualizados": 0, "invalidados": 0, "mantidos": 0}
        if not vinculos_por_interacao:
            return contagem
        try:
            with self.conexao.transaction():
                with self.conexao.cursor() as cursor:
                    self._bloquear_transacao(cursor, medicamento_origem, esperar=False)
                    cursor.execute(
                        """
                        SELECT 1 FROM processamentos_interacoes_vigentes
                        WHERE id = %s AND medicamento_origem = %s
                        """,
                        (processamento_id, medicamento_origem),
                    )
                    if cursor.fetchone() is None:
                        raise FonteAlteradaError(
                            f"O processamento {processamento_id} deixou de ser vigente."
                        )
                    cursor.execute(
                        """
                        SELECT interacao_id, medicamento_relacionado, criterio_vinculo,
                               fundamento_vinculo, invalidado_em
                        FROM interacoes_medicamentos
                        WHERE interacao_id = ANY(%s)
                        FOR UPDATE
                        """,
                        (list(vinculos_por_interacao),),
                    )
                    existentes = {
                        (linha["interacao_id"], linha["medicamento_relacionado"]): linha
                        for linha in cursor.fetchall()
                    }
                    desejados = {
                        (interacao_id, vinculo.medicamento_relacionado): vinculo
                        for interacao_id, lista in vinculos_por_interacao.items()
                        for vinculo in lista
                    }
                    for chave, vinculo in desejados.items():
                        atual = existentes.get(chave)
                        if atual is None:
                            cursor.execute(
                                """
                                INSERT INTO interacoes_medicamentos (
                                    interacao_id, medicamento_relacionado,
                                    criterio_vinculo, fundamento_vinculo
                                )
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (interacao_id, medicamento_relacionado) DO NOTHING
                                """,
                                (*chave, vinculo.criterio.value, vinculo.fundamento[:2000]),
                            )
                            contagem["criados"] += cursor.rowcount
                            continue
                        mudou = (
                            atual["criterio_vinculo"] != vinculo.criterio.value
                            or atual["fundamento_vinculo"] != vinculo.fundamento[:2000]
                        )
                        if atual["invalidado_em"] is not None or mudou:
                            cursor.execute(
                                """
                                UPDATE interacoes_medicamentos
                                SET criterio_vinculo = %s,
                                    fundamento_vinculo = %s,
                                    invalidado_em = NULL,
                                    vinculado_em = NOW()
                                WHERE interacao_id = %s AND medicamento_relacionado = %s
                                """,
                                (vinculo.criterio.value, vinculo.fundamento[:2000], *chave),
                            )
                            chave_contagem = "reativados" if atual["invalidado_em"] is not None else "atualizados"
                            contagem[chave_contagem] += 1
                        else:
                            contagem["mantidos"] += 1
                    for chave, atual in existentes.items():
                        if chave not in desejados and atual["invalidado_em"] is None:
                            cursor.execute(
                                """
                                UPDATE interacoes_medicamentos
                                SET invalidado_em = NOW()
                                WHERE interacao_id = %s AND medicamento_relacionado = %s
                                """,
                                chave,
                            )
                            contagem["invalidados"] += 1
        except (FonteAlteradaError, MedicamentoEmAndamentoError):
            raise
        except Exception as erro:
            raise ErroBancoError(
                f"Falha ao atualizar vínculos de '{medicamento_origem}': "
                f"{type(erro).__name__}: {erro}"
            ) from erro
        return contagem
