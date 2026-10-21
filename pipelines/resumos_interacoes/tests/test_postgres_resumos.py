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
