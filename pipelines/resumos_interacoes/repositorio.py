"""Preenche campos de resumo nas tabelas existentes, sem tabelas auxiliares."""

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from pipelines.identificacao_interacoes.modelos import chave_bloqueio
from pipelines.identificacao_interacoes.validacao import relacao_nao_positiva
from pipelines.identificacao_interacoes.contexto_evidencia import completar_evidencia
from .validacao import validar_fundamentacao, validar_contexto_geral


def preenchido(texto):
    return bool(texto and texto.strip())


def completo(fonte):
    return preenchido(fonte.get("resumo_geral")) and all(
        preenchido(i.get("resumo_card")) for i in fonte["interacoes"]
    )


def necessita_correcao(item):
    if item.get("evidencia_original"):
        return True
    if not preenchido(item.get("resumo_card")):
        return False
    try:
        validar_fundamentacao(item["resumo_card"], item["evidencia"])
    except ValueError:
        return True
    return False


def necessita_correcao_geral(fonte, nome=None):
    texto = fonte.get("resumo_geral")
    if not preenchido(texto):
        return False
    try:
        validar_contexto_geral(texto, nome or fonte["nome_normalizado"])
    except ValueError:
        return True
    return False


class RepositorioResumos:
    def __init__(self, dsn):
        self.conexao = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        try:
            self.conexao.execute("SELECT resumo_card, resumo_status, resumo_modelo, resumo_tempo_segundos, resumo_erro, resumo_atualizado_em FROM interacoes LIMIT 0")
            self.conexao.execute("""SELECT resumo_geral, resumo_status, resumo_gerado_em,
                resumo_modelo, resumo_erro, resumo_tempo_inferencia_segundos,
                resumo_tempo_total_segundos FROM processamentos_interacoes LIMIT 0""")
        except Exception:
            self.conexao.close()
            raise

    def fechar(self):
        self.conexao.close()

    @contextmanager
    def bloquear(self, nome):
        chave = chave_bloqueio(nome)
        obtido = self.conexao.execute("SELECT pg_try_advisory_lock(%s) AS ok", (chave,)).fetchone()["ok"]
        try:
            yield obtido
        finally:
            if obtido:
                self.conexao.execute("SELECT pg_advisory_unlock(%s)", (chave,))

    def selecionar(self, nome=None, limite=1, *, reprocessar=False, antes=None, corrigir_inconsistentes=False):
        """Bulas com resumo pendente; com `antes`, também as que têm resumo gerado antes dessa data."""
        linhas = self.conexao.execute("""
            SELECT v.medicamento_origem FROM processamentos_interacoes_vigentes v
            JOIN processamentos_interacoes p ON p.id = v.id
            WHERE v.status = 'CONCLUIDO_COM_RELACOES'
              AND (%(nome)s::text IS NULL OR v.medicamento_origem = %(nome)s)
              AND (%(corrigir)s OR %(reprocessar)s OR COALESCE(p.resumo_geral, '') !~ '[^[:space:]]'
                   OR EXISTS (SELECT 1 FROM interacoes i WHERE i.processamento_id = p.id
                              AND COALESCE(i.resumo_card, '') !~ '[^[:space:]]')
                   OR p.resumo_gerado_em < %(antes)s::timestamptz
                   OR EXISTS (SELECT 1 FROM interacoes i WHERE i.processamento_id = p.id
                              AND i.resumo_atualizado_em < %(antes)s::timestamptz))
            ORDER BY v.medicamento_origem
        """, {"nome": nome, "reprocessar": reprocessar, "antes": antes, "corrigir": corrigir_inconsistentes}).fetchall()
        selecionados = []
        for linha in linhas:
            candidato = linha["medicamento_origem"]
            fonte = self.fonte(candidato)
            if corrigir_inconsistentes:
                if fonte and (necessita_correcao_geral(fonte)
                              or any(necessita_correcao(i) for i in fonte["interacoes"])):
                    selecionados.append(candidato)
                    if limite is not None and len(selecionados) >= limite:
                        break
                continue
            refazer = reprocessar or (antes is not None and self.gerado_antes(candidato, antes))
            if not fonte or not fonte["interacoes"] or (not refazer and completo(fonte)):
                continue
            selecionados.append(candidato)
            if limite is not None and len(selecionados) >= limite:
                break
        return selecionados

    def gerado_antes(self, nome, antes) -> bool:
        """Algum card ou o geral desta bula foi gravado antes de `antes` (versão anterior do código)."""
        return self.conexao.execute("""
            SELECT EXISTS (
                SELECT 1 FROM processamentos_interacoes_vigentes v
                JOIN processamentos_interacoes p ON p.id = v.id
                WHERE v.medicamento_origem = %(nome)s
                  AND (p.resumo_gerado_em < %(antes)s::timestamptz
                       OR EXISTS (SELECT 1 FROM interacoes i WHERE i.processamento_id = p.id
                                  AND i.resumo_atualizado_em < %(antes)s::timestamptz))
            ) AS antigo
        """, {"nome": nome, "antes": antes}).fetchone()["antigo"]

    def fonte(self, nome):
        with self.conexao.transaction():
            self.conexao.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            p = self.conexao.execute("""
                SELECT v.id, v.hash_trecho, p.resumo_geral, b.trecho_interacoes
                FROM processamentos_interacoes_vigentes v
                JOIN processamentos_interacoes p ON p.id = v.id
                JOIN bulas_interacoes b ON b.nome_normalizado = v.medicamento_origem
                WHERE v.medicamento_origem = %s AND v.status = 'CONCLUIDO_COM_RELACOES'
            """, (nome,)).fetchone()
            if not p:
                return None
            linhas = self.conexao.execute("""
                SELECT id, alvo, tipo_alvo, evidencia, resumo_card FROM interacoes
                WHERE processamento_id = %s ORDER BY id
            """, (p["id"],)).fetchall()
        for item in linhas:
            completa = completar_evidencia(p["trecho_interacoes"], item["evidencia"])
            if completa != item["evidencia"]:
                item["evidencia_original"] = item["evidencia"]
                item["evidencia"] = completa
        return {"nome_normalizado": nome, "processamento_id": p["id"],
                "hash_trecho": p["hash_trecho"], "resumo_geral": p["resumo_geral"],
                "interacoes": [r for r in linhas if not relacao_nao_positiva(r)]}

    def gravar(self, fonte, modelo, inicio, resultado, geral, cards):
        with self.conexao.transaction():
            pid = fonte["processamento_id"]
            if resultado["status"] == "CONCLUIDO":
                with self.conexao.cursor() as cursor:
                    cursor.executemany("""
                        UPDATE interacoes SET resumo_card = %s
                        WHERE id = %s AND processamento_id = %s
                    """, [(texto, iid, pid) for iid, texto in cards.items()])
                self.conexao.execute("""
                    UPDATE processamentos_interacoes
                    SET resumo_geral = %s, resumo_gerado_em = clock_timestamp(), resumo_modelo = %s
                    WHERE id = %s
                """, (geral, modelo, pid))
            self.conexao.execute("""
                UPDATE processamentos_interacoes SET resumo_status = %s, resumo_erro = %s, resumo_modelo = %s,
                    resumo_tempo_inferencia_segundos = %s,
                    resumo_tempo_total_segundos = EXTRACT(EPOCH FROM clock_timestamp() - %s)
                WHERE id = %s
            """, (resultado["status"], resultado["erro"] or None, modelo,
                  resultado["tempo_inferencia_segundos"], inicio, pid))

    def gravar_card(self, fonte, item, texto, modelo, tempo, erro=None):
        # Amplia recorte legado apenas com texto literal da mesma bula; o tópico fica intacto.
        with self.conexao.transaction():
            cursor = self.conexao.execute("""
                UPDATE interacoes i SET
                    resumo_card = CASE WHEN %s::text IS NULL THEN i.resumo_card ELSE %s END,
                    evidencia = CASE WHEN %s::text IS NULL THEN i.evidencia ELSE %s END,
                    resumo_status = %s, resumo_modelo = %s, resumo_tempo_segundos = %s,
                    resumo_erro = %s, resumo_atualizado_em = clock_timestamp()
                WHERE i.id = %s AND i.processamento_id = %s AND i.evidencia = %s
                  AND EXISTS (SELECT 1 FROM processamentos_interacoes_vigentes v WHERE v.id = i.processamento_id)
            """, (texto, texto, texto, item["evidencia"], "FALHA" if erro else "CONCLUIDO", modelo, tempo,
                  erro, item["id"], fonte["processamento_id"], item.get("evidencia_original", item["evidencia"])))
            if cursor.rowcount != 1:
                raise ValueError("Fonte mudou antes da gravação do card; resultado não publicado.")

    def iniciar(self, fonte):
        self.conexao.execute("UPDATE processamentos_interacoes SET resumo_status = 'EM_PROCESSAMENTO', resumo_erro = NULL WHERE id = %s",
                             (fonte["processamento_id"],))
