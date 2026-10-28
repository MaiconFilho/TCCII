"""Persistência e vigência reais, somente em banco isolado explicitamente configurado."""

import unittest
from datetime import datetime, timedelta, timezone

from pipelines.identificacao_interacoes.modelos import (
    ConfiguracaoIdentificacao, RelacaoIdentificada, ResultadoIdentificacao,
    StatusProcessamento, TipoAlvo,
)
from pipelines.identificacao_interacoes.repositorio import RepositorioIdentificacao
from pipelines.identificacao_interacoes.tests import banco_teste
from pipelines.resumos_interacoes.repositorio import RepositorioResumos, necessita_correcao
from pipelines.resumos_interacoes.servico import GeradorResumos, processar
from .test_resumos import EVIDENCIA, GERAL, TRECHO, GeradorSemGeral, ProvedorFalso, resposta

DSN = banco_teste.dsn_de_teste()


@unittest.skipUnless(DSN, banco_teste.motivo_para_pular())
class TestResumosPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        banco_teste.preparar(DSN)

    def setUp(self):
        self.conexao = banco_teste.conectar(DSN)
        banco_teste.limpar(self.conexao)
        banco_teste.inserir_bula(self.conexao, "marevan", trecho=TRECHO)
        self.identificacao = RepositorioIdentificacao(DSN)
        fonte = self.identificacao.selecionar_fontes(nome_normalizado="marevan")[0]
        item = RelacaoIdentificada("ácido acetilsalicílico", TipoAlvo.PRINCIPIO_ATIVO,
                                  EVIDENCIA, TRECHO, 0, len(TRECHO), 0)
        resultado = ResultadoIdentificacao(StatusProcessamento.CONCLUIDO_COM_RELACOES,
                                           "marevan", relacoes=[item],
                                           quantidade_janelas=1, janelas_concluidas=1)
        self.identificacao.gravar_resultado(
            fonte, resultado, [[]], versao="teste", modelo="simulado",
            iniciado_em=datetime.now(timezone.utc), tempo_total_segundos=0,
        )
        self.repo = RepositorioResumos(DSN)
        self.config = ConfiguracaoIdentificacao()

    def tearDown(self):
        self.repo.fechar()
        self.identificacao.fechar()
        self.conexao.close()

    def gerar(self, reprocessar=False, provedor=None):
        return processar(self.repo, "marevan", GeradorResumos(provedor or ProvedorFalso(), self.config),
                         reprocessar=reprocessar)

    def test_gravacao_idempotencia_sem_novos_registros(self):
        self.assertEqual(self.gerar()["status"], "CONCLUIDO")
        self.assertEqual(self.gerar()["status"], "IGNORADO_JA_PROCESSADO")
        self.assertEqual(self.gerar(True)["status"], "CONCLUIDO")
        self.assertEqual(self.conexao.execute("SELECT count(*) AS n FROM processamentos_interacoes").fetchone()["n"], 1)
        self.assertEqual(self.conexao.execute("SELECT count(*) AS n FROM interacoes WHERE resumo_card IS NOT NULL").fetchone()["n"], 1)
        self.assertEqual(self.repo.selecionar(), [])

    def test_selecao_reparo_inclui_apenas_geral_inconsistente(self):
        self.gerar()
        self.assertEqual(self.repo.selecionar(corrigir_inconsistentes=True), [])
        self.conexao.execute("UPDATE processamentos_interacoes SET resumo_geral = %s",
                             ("O citrato de fentanil pode interagir com outros medicamentos.",))
        self.assertEqual(self.repo.selecionar(corrigir_inconsistentes=True), ["marevan"])
        antes = self.conexao.execute("SELECT id, resumo_card FROM interacoes ORDER BY id").fetchall()
        resultado = processar(self.repo, "marevan", GeradorResumos(ProvedorFalso([resposta(GERAL)]), self.config),
                              corrigir_inconsistentes=True)
        self.assertEqual(resultado["status"], "CONCLUIDO")
        self.assertEqual(resultado["quantidade_interacoes"], 0)
        self.assertEqual(resultado["chamadas_llm"], 1)
        self.assertEqual(self.conexao.execute("SELECT id, resumo_card FROM interacoes ORDER BY id").fetchall(), antes)
        self.assertEqual(self.repo.selecionar(corrigir_inconsistentes=True), [])

    def test_reparo_de_recorte_amplia_literal_preserva_ids_e_encerra_pendencia(self):
        from pipelines.identificacao_interacoes.tests.test_contexto_evidencia import BLOCO, LISTA
        # Fonte e identificação coerentes, com o recorte legado cortado antes do efeito.
        self.conexao.execute("UPDATE bulas_interacoes SET trecho_interacoes = %s", (BLOCO,))
        fonte = self.identificacao.selecionar_fontes(nome_normalizado="marevan")[0]
        item = RelacaoIdentificada("linezolida", TipoAlvo.PRINCIPIO_ATIVO, "Texto anterior.", LISTA, 0, len(LISTA), 0)
        resultado = ResultadoIdentificacao(StatusProcessamento.CONCLUIDO_COM_RELACOES,
                                           "marevan", relacoes=[item], quantidade_janelas=1, janelas_concluidas=1)
        self.identificacao.gravar_resultado(fonte, resultado, [[]], versao="reparo", modelo="teste",
            iniciado_em=datetime.now(timezone.utc), tempo_total_segundos=0, reprocessar=True)
        original = self.conexao.execute("SELECT id, processamento_id FROM interacoes ORDER BY id DESC LIMIT 1").fetchone()
        self.conexao.execute("UPDATE interacoes SET resumo_card = 'Interação de linezolida desconhecida.' WHERE id=%s", (original["id"],))
        self.assertEqual(self.repo.selecionar(corrigir_inconsistentes=True), ["marevan"])
        atual = self.repo.fonte("marevan")
        linha = atual["interacoes"][0]
        self.assertEqual(linha["evidencia"], BLOCO)
        self.assertEqual(linha["evidencia_original"], LISTA)
        resumo = "Linezolida com nafazolina nasal pode aumentar o risco de efeitos adversos cardiovasculares."
        self.repo.gravar_card(atual, linha, resumo, "teste", 0.1)
        gravada = self.conexao.execute("SELECT * FROM interacoes WHERE id=%s", (original["id"],)).fetchone()
        self.assertEqual(gravada["processamento_id"], original["processamento_id"])
        self.assertEqual(gravada["evidencia"], BLOCO)
        self.assertEqual(gravada["resumo_card"], resumo)
        self.assertFalse(necessita_correcao(self.repo.fonte("marevan")["interacoes"][0]))
        self.assertEqual(self.repo.selecionar(corrigir_inconsistentes=True), [])

    def test_falha_preserva_geracao_valida(self):
        self.gerar()
        provedor = ProvedorFalso([resposta("")])
        self.assertEqual(self.gerar(True, provedor)["status"], "FALHA")
        self.assertTrue(self.conexao.execute("SELECT resumo_geral FROM processamentos_interacoes").fetchone()["resumo_geral"])
        self.assertEqual(self.conexao.execute("SELECT resumo_card FROM interacoes").fetchone()["resumo_card"], EVIDENCIA)

    def test_reescrita_grava_separado_retoma_e_preserva_publicacao_em_falha(self):
        from pipelines.resumos_interacoes.repositorio import RepositorioReescrita
        from pipelines.resumos_interacoes.reescrita import GeradorReescrita, processar_reescrita
        from pipelines.resumos_interacoes.tests.test_reescrita import Provedor, resposta as reescrita

        self.gerar()
        originais = self.conexao.execute("SELECT id,resumo_card,evidencia FROM interacoes").fetchall()
        repo = RepositorioReescrita(DSN)
        modelo = {"repositorio": "teste/segundo", "revisao": "a" * 40, "arquivo": "segundo.gguf"}
        gerador = GeradorReescrita(Provedor([reescrita(EVIDENCIA), reescrita(EVIDENCIA)]), self.config,
                                  modelo, ram_minima_mb=0)
        try:
            resultado = processar_reescrita(repo, "marevan", gerador)
            self.assertTrue(all(r["status"] == "CONCLUIDO" for r in resultado), resultado)
            self.assertEqual(self.conexao.execute("SELECT id,resumo_card,evidencia FROM interacoes").fetchall(), originais)
            self.assertEqual(self.conexao.execute("SELECT resumo_card_simples FROM interacoes").fetchone()["resumo_card_simples"], EVIDENCIA)
            self.assertEqual(processar_reescrita(repo, "marevan", gerador), [])
            self.assertEqual(repo.selecionar(modelo=modelo), [])
            publicado = self.conexao.execute("SELECT reescrita_controle FROM interacoes").fetchone()["reescrita_controle"]["publicado"]
            gerador.provedor = Provedor([TimeoutError("teste"), TimeoutError("teste")])
            processar_reescrita(repo, "marevan", gerador, reprocessar=True)
            atual = self.conexao.execute("SELECT resumo_card_simples,reescrita_controle FROM interacoes").fetchone()
            self.assertEqual(atual["resumo_card_simples"], EVIDENCIA)
            self.assertEqual(atual["reescrita_controle"]["publicado"], publicado)
            self.assertEqual(atual["reescrita_controle"]["ultima_tentativa"]["status"], "FALHA")
        finally:
            repo.fechar()

    def test_reescrita_reconstroi_original_incorreto_e_grava_rastro_separado(self):
        from pipelines.resumos_interacoes.repositorio import RepositorioReescrita
        from pipelines.resumos_interacoes.reescrita import GeradorReescrita, processar_reescrita
        from pipelines.resumos_interacoes.tests.test_reescrita import Provedor, resposta as reescrita

        self.gerar()
        incorreto = EVIDENCIA.replace("aumentar", "diminuir")
        self.conexao.execute("UPDATE interacoes SET resumo_card=%s", (incorreto,))
        antes = self.conexao.execute("SELECT id,resumo_card,evidencia FROM interacoes").fetchall()
        repo = RepositorioReescrita(DSN)
        modelo = {"repositorio": "teste/segundo", "revisao": "a" * 40, "arquivo": "segundo.gguf"}
        gerador = GeradorReescrita(Provedor([reescrita(EVIDENCIA), reescrita(EVIDENCIA)]),
                                  self.config, modelo, ram_minima_mb=0)
        try:
            r = processar_reescrita(repo, "marevan", gerador)
            self.assertTrue(all(i["status"] == "CONCLUIDO" for i in r), r)
            atual = self.conexao.execute("SELECT resumo_card_simples,reescrita_controle FROM interacoes").fetchone()
            self.assertEqual(atual["resumo_card_simples"], EVIDENCIA)
            etapas = atual["reescrita_controle"]["publicado"]["etapas"]
            self.assertEqual(etapas[0]["status"], "INCONSISTENTE")
            self.assertEqual(etapas[-1]["fase"], "reconstrucao_pela_bula")
            self.assertEqual(self.conexao.execute("SELECT id,resumo_card,evidencia FROM interacoes").fetchall(), antes)
            self.assertEqual(processar_reescrita(repo, "marevan", gerador), [])
        finally:
            repo.fechar()

    def test_progresso_geral_nao_publica_parcial_e_recusa_fonte_alterada(self):
        from pipelines.resumos_interacoes.repositorio import RepositorioReescrita, unidades_reescrita
        from psycopg.types.json import Jsonb

        self.gerar()
        publicado = {'status': 'CONCLUIDO', 'sentinela': 'publicação anterior'}
        self.conexao.execute("UPDATE processamentos_interacoes SET resumo_geral_simples=%s, reescrita_controle=%s",
                             ('Texto anterior.', Jsonb({'publicado': publicado})))
        repo = RepositorioReescrita(DSN)
        try:
            unidade = next(u for u in unidades_reescrita(repo.fonte('marevan')) if u['tipo'] == 'geral')
            antes = self.conexao.execute('SELECT id,resumo_card,evidencia FROM interacoes').fetchall()
            progresso = {'blocos': {'hash': {'texto': 'Bloco aprovado.'}}}
            repo.gravar_progresso_geral('marevan', unidade, progresso)
            atual = self.conexao.execute('SELECT resumo_geral,resumo_geral_simples,reescrita_controle FROM processamentos_interacoes').fetchone()
            self.assertEqual(atual['resumo_geral'], unidade['entrada']['resumo_original'])
            self.assertEqual(atual['resumo_geral_simples'], 'Texto anterior.')
            self.assertEqual(atual['reescrita_controle']['publicado'], publicado)
            self.assertEqual(atual['reescrita_controle']['progresso_geral'], progresso)
            self.assertEqual(self.conexao.execute('SELECT id,resumo_card,evidencia FROM interacoes').fetchall(), antes)
            self.conexao.execute("UPDATE processamentos_interacoes SET resumo_geral=resumo_geral || ' Fonte nova.'")
            with self.assertRaisesRegex(RuntimeError, 'fonte mudou'):
                repo.gravar_progresso_geral('marevan', unidade, {'blocos': {}})
            self.assertEqual(self.conexao.execute('SELECT reescrita_controle FROM processamentos_interacoes').fetchone()['reescrita_controle']['progresso_geral'], progresso)
            unidade = next(u for u in unidades_reescrita(repo.fonte('marevan')) if u['tipo'] == 'geral')
            repo.gravar_reescrita('marevan', unidade, 'Resumo completo.', {'publicado': {'status': 'CONCLUIDO'}})
            self.assertNotIn('progresso_geral', self.conexao.execute('SELECT reescrita_controle FROM processamentos_interacoes').fetchone()['reescrita_controle'])
        finally:
            repo.fechar()

    def test_reescrita_recusa_fonte_alterada_antes_de_gravar(self):
        from pipelines.resumos_interacoes.repositorio import RepositorioReescrita, unidades_reescrita

        self.gerar()
        repo = RepositorioReescrita(DSN)
        try:
            unidade = unidades_reescrita(repo.fonte("marevan"))[0]
            self.conexao.execute("UPDATE interacoes SET resumo_card=resumo_card || ' Nova condição.'")
            with self.assertRaisesRegex(ValueError, "mudou"):
                repo.gravar_reescrita("marevan", unidade, "Texto simples.", {})
            self.assertIsNone(self.conexao.execute("SELECT resumo_card_simples FROM interacoes").fetchone()["resumo_card_simples"])
        finally:
            repo.fechar()

    def test_reescrita_mesmo_alvo_grava_grupo_atomico_e_recusa_fonte_alterada(self):
        from pipelines.resumos_interacoes.repositorio import RepositorioReescrita, unidades_reescrita
        from pipelines.resumos_interacoes.rastreabilidade import hash_dados, VERSAO_REESCRITA

        self.gerar()
        self.conexao.execute('''INSERT INTO interacoes
            (medicamento_origem,alvo,tipo_alvo,resumo,evidencia,processamento_id,resumo_card)
            SELECT medicamento_origem,alvo,tipo_alvo,resumo,evidencia || ' Outro trecho.',processamento_id,resumo_card
            FROM interacoes LIMIT 1''')
        antes = self.conexao.execute('SELECT id,resumo_card,evidencia FROM interacoes ORDER BY id').fetchall()
        repo = RepositorioReescrita(DSN)
        try:
            unidade = unidades_reescrita(repo.fonte('marevan'))[0]
            self.assertEqual(len(unidade['ids']), 2)
            controle = {'publicado': {'status': 'CONCLUIDO', 'prompt': VERSAO_REESCRITA,
                        'entrada_hash': hash_dados(unidade['entrada']), 'texto_hash': hash_dados(EVIDENCIA)}}
            repo.gravar_reescrita('marevan', unidade, EVIDENCIA, controle)
            atuais = self.conexao.execute('SELECT resumo_card_simples FROM interacoes').fetchall()
            self.assertEqual([i['resumo_card_simples'] for i in atuais], [EVIDENCIA, EVIDENCIA])
            self.assertEqual(self.conexao.execute('SELECT id,resumo_card,evidencia FROM interacoes ORDER BY id').fetchall(), antes)
            self.conexao.execute('UPDATE interacoes SET resumo_card=resumo_card || %s WHERE id=%s',
                                 (' Fonte alterada.', unidade['ids'][1]))
            with self.assertRaisesRegex(ValueError, 'mudou'):
                repo.gravar_reescrita('marevan', unidade, 'Não deve publicar.', controle)
        finally:
            repo.fechar()

    def test_secao_nominal_so_recupera_card_ausente_e_preserva_ids(self):
        secao = ("sirolimo: fluconazol aumenta a concentração de sirolimo. "
                 "Esta combinação pode ser usada com ajuste da dose de sirolimo.")
        vizinha = "sulfonilureias: o fluconazol prolonga a meia-vida das sulfonilureias."
        completo = secao + "\n" + vizinha
        recorte = "Esta combinação pode ser usada com ajuste da dose de sirolimo.\n" + vizinha
        self.conexao.execute("UPDATE bulas_interacoes SET trecho_interacoes = %s", (completo,))
        fonte = self.identificacao.selecionar_fontes(nome_normalizado="marevan")[0]
        item = RelacaoIdentificada("sirolimo", TipoAlvo.PRINCIPIO_ATIVO, "Texto anterior.", recorte, 0, len(recorte), 0)
        resultado = ResultadoIdentificacao(StatusProcessamento.CONCLUIDO_COM_RELACOES,
                                           "marevan", relacoes=[item], quantidade_janelas=1, janelas_concluidas=1)
        self.identificacao.gravar_resultado(fonte, resultado, [[]], versao="nominal", modelo="teste",
            iniciado_em=datetime.now(timezone.utc), tempo_total_segundos=0, reprocessar=True)
        original = self.conexao.execute("SELECT * FROM interacoes ORDER BY id DESC LIMIT 1").fetchone()
        self.conexao.execute("UPDATE interacoes SET resumo_card=%s WHERE id=%s", ("Resumo já conferido.", original["id"]))
        self.assertEqual(self.repo.fonte("marevan")["interacoes"][0]["evidencia"], recorte)
        self.conexao.execute("UPDATE interacoes SET resumo_card=NULL WHERE id=%s", (original["id"],))
        atual = self.repo.fonte("marevan")
        recuperada = atual["interacoes"][0]
        self.assertEqual(recuperada["evidencia"], secao)
        self.assertEqual(recuperada["evidencia_original"], recorte)
        resumo = "Fluconazol aumenta a concentração de sirolimo."
        self.repo.gravar_card(atual, recuperada, resumo, "teste", 0.1)
        gravada = self.conexao.execute("SELECT * FROM interacoes WHERE id=%s", (original["id"],)).fetchone()
        self.assertEqual(gravada["processamento_id"], original["processamento_id"])
        self.assertEqual(gravada["evidencia"], secao)
        self.assertEqual(gravada["resumo_card"], resumo)
        self.assertNotIn("evidencia_original", self.repo.fonte("marevan")["interacoes"][0])

    def test_card_persistido_antes_de_falha_geral_e_retomada(self):
        resultado = processar(self.repo, "marevan", GeradorSemGeral(ProvedorFalso([resposta(EVIDENCIA)]), self.config))
        self.assertEqual(resultado["status"], "FALHA")
        self.assertTrue(resultado["gravacao_parcial"])
        card = self.conexao.execute("SELECT * FROM interacoes").fetchone()
        self.assertEqual(card["resumo_card"], EVIDENCIA)
        self.assertEqual(card["resumo_status"], "CONCLUIDO")
        self.assertIsNotNone(card["resumo_atualizado_em"])
        self.assertEqual(card["evidencia"], TRECHO)
        self.assertEqual(self.gerar(provedor=ProvedorFalso([resposta(GERAL)]))["chamadas_llm"], 1)

    def test_erro_por_card_persistido_sem_alterar_evidencia(self):
        resultado = self.gerar(provedor=ProvedorFalso([TimeoutError("teste timeout")]))
        self.assertEqual(resultado["status"], "FALHA")
        card = self.conexao.execute("SELECT * FROM interacoes").fetchone()
        self.assertEqual(card["resumo_status"], "FALHA")
        self.assertIn("teste timeout", card["resumo_erro"])
        self.assertIsNone(card["resumo_card"])
        self.assertEqual(card["evidencia"], TRECHO)

    def test_refazer_antes_seleciona_so_resumo_gravado_antes_da_data(self):
        self.gerar()
        self.assertEqual(self.repo.selecionar(), [])
        depois = datetime.now(timezone.utc) + timedelta(minutes=5)
        antes = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertTrue(self.repo.gerado_antes("marevan", depois))
        self.assertEqual(self.repo.selecionar(antes=depois), ["marevan"])
        self.assertFalse(self.repo.gerado_antes("marevan", antes))
        self.assertEqual(self.repo.selecionar(antes=antes), [])

    def test_fonte_desatualizada_nao_expoe_resumo(self):
        self.gerar()
        self.conexao.execute("UPDATE bulas_interacoes SET trecho_interacoes = %s WHERE nome_normalizado = %s",
                             (TRECHO + " Texto novo.", "marevan"))
        self.assertIsNone(self.repo.fonte("marevan"))

    def test_selecao_de_preenchimento_parcial_e_espacos(self):
        self.gerar()
        for vazio in (None, "", " \n\t"):
            self.conexao.execute("UPDATE interacoes SET resumo_card = %s", (vazio,))
            self.assertEqual(self.repo.selecionar(), ["marevan"])
            self.assertEqual(self.gerar()["chamadas_llm"], 1)
            self.conexao.execute("UPDATE processamentos_interacoes SET resumo_geral = %s", (vazio,))
            self.assertEqual(self.repo.selecionar(), ["marevan"])
            self.assertEqual(self.gerar(provedor=ProvedorFalso([resposta(GERAL)]))["chamadas_llm"], 1)

    def test_lock_entre_conexoes(self):
        outro = RepositorioResumos(DSN)
        try:
            with self.repo.bloquear("marevan") as adquirido:
                self.assertTrue(adquirido)
                resultado = processar(outro, "marevan", GeradorResumos(ProvedorFalso(), self.config))
                self.assertEqual(resultado["status"], "EM_ANDAMENTO")
        finally:
            outro.fechar()
