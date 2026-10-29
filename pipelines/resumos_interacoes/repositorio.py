"""Preenche campos de resumo nas tabelas existentes, sem tabelas auxiliares."""

from contextlib import contextmanager
import time

import psycopg
from psycopg.rows import dict_row

from pipelines.identificacao_interacoes.modelos import chave_bloqueio
from pipelines.identificacao_interacoes.validacao import relacao_nao_positiva
from pipelines.identificacao_interacoes.contexto_evidencia import completar_evidencia
from .validacao import validar_fundamentacao, validar_contexto_geral
from .contexto import recuperar_secao
from .rastreabilidade import entrada_card, entrada_geral, hash_dados, simples_atual, reescrita_salva, VERSOES_INDIVIDUAIS


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
        self.dsn = dsn
        # Após reconectar, a sessão anterior pode manter o advisory lock por instantes.
        self.aguardar_bloqueio = 0.0
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

    def reconectar(self, *, tentativas=3, espera=5.0, dormir=time.sleep, informar=print) -> bool:
        """Nova conexão após uma queda, com espera crescente; o lock é reaberto pela bula."""
        try:
            self.conexao.close()
        except psycopg.Error:
            pass
        for tentativa in range(1, tentativas + 1):
            dormir(espera * 2 ** (tentativa - 1))
            try:
                self.conexao = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
            except psycopg.OperationalError as erro:
                informar(f"  Reconexão {tentativa}/{tentativas} falhou: {type(erro).__name__}.")
                continue
            informar(f"  Reconectado na tentativa {tentativa}/{tentativas}.")
            self.aguardar_bloqueio = espera * 2 ** (tentativas - 1)
            return True
        return False

    @contextmanager
    def bloquear(self, nome):
        chave = chave_bloqueio(nome)
        consultar = lambda: self.conexao.execute("SELECT pg_try_advisory_lock(%s) AS ok", (chave,)).fetchone()["ok"]
        obtido = consultar()
        prazo = time.monotonic() + self.aguardar_bloqueio
        while not obtido and time.monotonic() < prazo:
            time.sleep(1)
            obtido = consultar()
        self.aguardar_bloqueio = 0.0
        try:
            yield obtido
        finally:
            # Com a conexão perdida, o servidor libera o lock ao encerrar a sessão.
            if obtido and not (self.conexao.closed or self.conexao.broken):
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
            # Só recupera a seção nominal nos cards ausentes. Trocar o prompt não
            # torna cards prontos pendentes nem muda suas evidências.
            if not preenchido(item.get("resumo_card")):
                completa = recuperar_secao(p["trecho_interacoes"], completa, item["alvo"])
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

    def gravar_card(self, fonte, item, texto, modelo, tempo, erro=None, *, origem=None):
        # Amplia recorte legado apenas com texto literal da mesma bula; o tópico fica intacto.
        with self.conexao.transaction():
            cursor = self.conexao.execute("""
                UPDATE interacoes i SET
                    resumo_card = CASE WHEN %s::text IS NULL THEN i.resumo_card ELSE %s END,
                    evidencia = CASE WHEN %s::text IS NULL THEN i.evidencia ELSE %s END,
                    resumo_status = %s, resumo_modelo = %s, resumo_tempo_segundos = %s,
                    resumo_erro = %s, resumo_atualizado_em = clock_timestamp()
                    , resumo_origem = CASE WHEN %s::text IS NULL THEN i.resumo_origem ELSE %s END
                WHERE i.id = %s AND i.processamento_id = %s AND i.evidencia = %s
                  AND EXISTS (SELECT 1 FROM processamentos_interacoes_vigentes v WHERE v.id = i.processamento_id)
            """, (texto, texto, texto, item["evidencia"], "FALHA" if erro else "CONCLUIDO", modelo, tempo,
                  erro, origem if texto is not None else None, origem,
                  item["id"], fonte["processamento_id"], item.get("evidencia_original", item["evidencia"])))
            if cursor.rowcount != 1:
                raise ValueError("Fonte mudou antes da gravação do card; resultado não publicado.")

    def iniciar(self, fonte):
        self.conexao.execute("UPDATE processamentos_interacoes SET resumo_status = 'EM_PROCESSAMENTO', resumo_erro = NULL WHERE id = %s",
                             (fonte["processamento_id"],))


def unidades_reescrita(fonte):
    """Unidades pequenas de retomada, preservando originais e evidências literais."""
    unidades = []
    grupos = {}
    for item in fonte["interacoes"]:
        if preenchido(item.get("resumo_card")):
            grupos.setdefault((item["alvo"], item["tipo_alvo"]), []).append(item)
    for grupo in grupos.values():
        grupo = sorted(grupo, key=lambda i: i["id"])
        item = grupo[0]
        entrada = entrada_card({**item, "resumos_mesmo_alvo": grupo})
        individuais = all(((i.get("reescrita_controle") or {}).get("publicado") or {}).get("prompt")
                          in VERSOES_INDIVIDUAIS for i in grupo)
        completos = all(simples_atual(i.get("resumo_card_simples"), i.get("reescrita_controle"), entrada)
                        and (individuais or i.get("resumo_card_simples") == item.get("resumo_card_simples")) for i in grupo)
        unidades.append({"tipo": "card", "id": item["id"], "ids": [i["id"] for i in grupo], "entrada": entrada,
                         "concluido": all(reescrita_salva(i.get("resumo_card_simples"), i.get("reescrita_controle")) for i in grupo),
                         "texto_simples": item.get("resumo_card_simples") if completos else None,
                         "controle": item.get("reescrita_controle") or {}, "original": item})
    if preenchido(fonte.get("resumo_geral")):
        unidades.append({"tipo": "geral", "id": fonte["processamento_id"],
                         "concluido": reescrita_salva(fonte.get("resumo_geral_simples"), fonte.get("reescrita_controle")),
                         "entrada": entrada_geral(fonte, fonte["interacoes"]),
                         "texto_simples": fonte.get("resumo_geral_simples"),
                         "controle": fonte.get("reescrita_controle") or {}, "original": fonte})
    return unidades


def _status_publicado(linha):
    # Só o status publicado decide a retomada (reescrita_salva); o controle inteiro não é lido.
    return {"publicado": {"status": linha["status_publicado"]}} if linha.get("status_publicado") is not None else {}


def _pendente(fonte, reprocessar):
    return any(reprocessar or not u["concluido"] for u in unidades_reescrita(fonte))


def _fonte_da_selecao(p, linhas=()):
    extras = {"hash_trecho": p["hash_trecho"], "sha256_pdf": p["sha256_pdf"], "principios_ativos": p["principios_ativos"]}
    linhas = [{**i, **extras, "reescrita_controle": _status_publicado(i)} for i in sorted(linhas, key=lambda i: i["id"])]
    return {**p, "processamento_id": p["id"], "reescrita_controle": _status_publicado(p),
            "interacoes": [i for i in linhas if not relacao_nao_positiva(i)]}


def pendente_pelo_geral(p, reprocessar=False):
    """O geral não depende dos cards para decidir: pendente aqui já decide a bula."""
    return _pendente(_fonte_da_selecao(p), reprocessar)


def bulas_pendentes(processamentos, interacoes, *, reprocessar=False, limite=None):
    """Bulas com alguma unidade pendente, pelo mesmo critério de unidades_reescrita.

    Recebe as linhas lidas em lote, na ordem das bulas, e monta em memória a
    mesma fonte que `fonte()` montaria para cada uma. Os cards só são filtrados
    quando o resumo geral da bula não a torna pendente.
    """
    por_processamento = {}
    for linha in interacoes:
        por_processamento.setdefault(linha["processamento_id"], []).append(linha)
    nomes = []
    for p in processamentos:
        if pendente_pelo_geral(p, reprocessar) or _pendente(
                _fonte_da_selecao(p, por_processamento.get(p["id"], [])), reprocessar):
            nomes.append(p["medicamento_origem"])
            if limite and len(nomes) >= limite:
                break
    return nomes


def sinonimos_do_cadastro(interacoes, vinculos):
    """Nomes ligados a cada alvo por interacoes_medicamentos, só de bulas com um princípio ativo.

    Uma associação (vários princípios) não é sinônimo de um dos seus componentes.
    """
    from pipelines.identificacao_interacoes.resolucao import substancias

    alvos = {i["id"]: i["alvo"] for i in interacoes}
    saida = {}
    for v in vinculos:
        principios = substancias(v["principios_ativos"])
        if v["interacao_id"] in alvos and len(principios) == 1:
            nomes = saida.setdefault(alvos[v["interacao_id"]], set())
            nomes.update({v["nome_normalizado"], *principios})
    return {alvo: sorted(nomes) for alvo, nomes in saida.items()}


class RepositorioReescrita(RepositorioResumos):
    """Mesma conexão e bloqueio; segunda etapa nunca altera os textos originais."""

    def __init__(self, dsn):
        super().__init__(dsn)
        try:
            self.conexao.execute("SELECT resumo_card_simples, reescrita_controle, resumo_origem FROM interacoes LIMIT 0")
            self.conexao.execute("SELECT resumo_geral_simples, reescrita_controle FROM processamentos_interacoes LIMIT 0")
        except Exception:
            self.fechar()
            raise

    def _fonte_reescrita(self, nome):
        p = self.conexao.execute("""
            SELECT p.*, p.id AS processamento_id, b.principios_ativos
            FROM processamentos_interacoes_vigentes v
            JOIN processamentos_interacoes p ON p.id=v.id
            JOIN bulas b ON b.nome_normalizado=v.medicamento_origem
            WHERE v.medicamento_origem=%s AND v.status='CONCLUIDO_COM_RELACOES'
        """, (nome,)).fetchone()
        if not p:
            return None
        linhas = self.conexao.execute("""
            SELECT i.*, p.hash_trecho, p.sha256_pdf, b.principios_ativos FROM interacoes i
            JOIN processamentos_interacoes p ON p.id=i.processamento_id
            JOIN bulas b ON b.nome_normalizado=p.medicamento_origem
            WHERE i.processamento_id=%s ORDER BY i.id
        """, (p["id"],)).fetchall()
        p["interacoes"] = [i for i in linhas if not relacao_nao_positiva(i)]
        return p

    def fonte(self, nome):
        with self.conexao.transaction():
            self.conexao.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            fonte = self._fonte_reescrita(nome)
            if fonte:
                # Fora de `entrada`: não altera entrada_hash nem o que a API confere.
                trecho = self.conexao.execute(
                    "SELECT trecho_interacoes FROM bulas_interacoes WHERE nome_normalizado=%s", (nome,)).fetchone()
                vinculos = self.conexao.execute("""
                    SELECT im.interacao_id, b.nome_normalizado, b.principios_ativos
                    FROM interacoes_medicamentos im
                    JOIN bulas b ON b.nome_normalizado=im.medicamento_relacionado
                    WHERE im.interacao_id=ANY(%s) AND im.invalidado_em IS NULL
                """, ([i["id"] for i in fonte["interacoes"]],)).fetchall()
                fonte["apoio"] = {"trecho": trecho["trecho_interacoes"] if trecho else None,
                                  "sinonimos": sinonimos_do_cadastro(fonte["interacoes"], vinculos)}
            return fonte

    def selecionar(self, nome=None, limite=1, *, reprocessar=False, modelo=None):
        """Duas consultas no total; a pendência é decidida em memória por bulas_pendentes."""
        with self.conexao.transaction():
            self.conexao.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            processamentos = self.conexao.execute("""
                SELECT p.id, p.medicamento_origem, p.hash_trecho, p.sha256_pdf, p.resumo_geral,
                    p.resumo_geral_simples, p.reescrita_controle->'publicado'->>'status' AS status_publicado,
                    b.principios_ativos
                FROM processamentos_interacoes_vigentes v
                JOIN processamentos_interacoes p ON p.id=v.id
                JOIN bulas b ON b.nome_normalizado=v.medicamento_origem
                WHERE v.status='CONCLUIDO_COM_RELACOES'
                    AND (%(nome)s::text IS NULL OR v.medicamento_origem=%(nome)s)
                ORDER BY v.medicamento_origem
            """, {"nome": nome}).fetchall()
            # Cards só das bulas cujo resumo geral não basta para decidir.
            ids = [p["id"] for p in processamentos if not pendente_pelo_geral(p, reprocessar)]
            interacoes = self.conexao.execute("""
                SELECT id, processamento_id, medicamento_origem, alvo, tipo_alvo, evidencia, resumo_card,
                    resumo_card_simples, reescrita_controle->'publicado'->>'status' AS status_publicado
                FROM interacoes WHERE processamento_id=ANY(%s)
            """, (ids,)).fetchall() if ids else []
        return bulas_pendentes(processamentos, interacoes, reprocessar=reprocessar, limite=limite)

    def gravar_progresso_geral(self, nome, unidade, progresso):
        """Guarda blocos aprovados sem publicar um resumo geral incompleto."""
        from psycopg.types.json import Jsonb

        if unidade["tipo"] != "geral":
            raise ValueError("A retomada por blocos pertence somente ao resumo geral.")
        try:
            with self.conexao.transaction():
                fonte = self._fonte_reescrita(nome)
                atual = next((u for u in unidades_reescrita(fonte or {"interacoes": []})
                              if u["tipo"] == "geral" and u["id"] == unidade["id"]), None)
                if atual is None or hash_dados(atual["entrada"]) != hash_dados(unidade["entrada"]):
                    raise RuntimeError("A fonte mudou; o bloco do resumo geral não foi salvo.")
                cursor = self.conexao.execute("""UPDATE processamentos_interacoes
                    SET reescrita_controle=reescrita_controle || %s WHERE id=%s""",
                    (Jsonb({"progresso_geral": progresso}), unidade["id"]))
                if cursor.rowcount != 1:
                    raise RuntimeError("Não foi possível salvar o progresso do resumo geral.")
        except psycopg.Error as erro:
            if self.conexao.closed or self.conexao.broken:
                # Queda de conexão: o chamador interrompe a bula e reconecta.
                raise
            raise RuntimeError("Erro no banco ao salvar um bloco do resumo geral; o texto completo não foi publicado.") from erro

    def gravar_reescrita(self, nome, unidade, texto, controle):
        from psycopg.types.json import Jsonb

        # Chamado somente depois de inferência/validação. A transação não fica
        # aberta durante o carregamento ou a geração do modelo.
        with self.conexao.transaction():
            fonte = self._fonte_reescrita(nome)
            atual = next((u for u in unidades_reescrita(fonte or {"interacoes": []})
                          if (u["tipo"], u["id"]) == (unidade["tipo"], unidade["id"])), None)
            if atual is None or hash_dados(atual["entrada"]) != hash_dados(unidade["entrada"]):
                raise ValueError("A fonte ou o resumo original mudou; reescrita não publicada.")
            tabela, campo = (("interacoes", "resumo_card_simples") if unidade["tipo"] == "card"
                             else ("processamentos_interacoes", "resumo_geral_simples"))
            ids = unidade.get("ids", [unidade["id"]])
            cursor = self.conexao.execute(f"""
                UPDATE {tabela} SET {campo}=COALESCE(%s, {campo}),
                    reescrita_controle=reescrita_controle || %s WHERE id=ANY(%s)
            """, (texto, Jsonb(controle), ids))
            if cursor.rowcount != len(ids):
                raise ValueError("O grupo de interações mudou; reescrita não publicada.")
            if unidade["tipo"] == "geral" and texto is not None:
                self.conexao.execute("""UPDATE processamentos_interacoes
                    SET reescrita_controle=reescrita_controle - 'progresso_geral' WHERE id=%s""", (unidade["id"],))
