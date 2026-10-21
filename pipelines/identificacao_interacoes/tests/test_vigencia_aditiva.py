"""Integração opt-in sem limpar dados: cada teste usa uma origem exclusiva."""
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from pipelines.identificacao_interacoes.modelos import ResultadoIdentificacao, StatusProcessamento
from pipelines.identificacao_interacoes.repositorio import RepositorioIdentificacao
from . import banco_teste as banco


@unittest.skipUnless(banco.dsn_de_teste(), banco.motivo_para_pular())
class TestVigenciaAditiva(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        banco.preparar(banco.dsn_de_teste())

    def setUp(self):
        self.con = banco.conectar(banco.dsn_de_teste())
        self.addCleanup(self.con.close)
        self.repo = RepositorioIdentificacao.com_conexao(self.con)
        self.nome = 'teste ' + uuid4().hex
        banco.inserir_bula(self.con, self.nome, trecho='Cafeína aumenta a toxicidade.')
        self.fonte = self.repo.selecionar_fontes(nome_normalizado=self.nome)[0]

    def gravar(self, status=StatusProcessamento.CONCLUIDO_SEM_RELACOES):
        r = ResultadoIdentificacao(status, self.nome, quantidade_janelas=1, janelas_concluidas=1)
        comum = dict(versao='teste', modelo='simulado', iniciado_em=datetime.now(timezone.utc), tempo_total_segundos=0)
        if status == StatusProcessamento.FALHA:
            return self.repo.registrar_falha(self.fonte, r, detalhe='falha tecnica', **comum)
        return self.repo.gravar_resultado(self.fonte, r, [], reprocessar=True, **comum)[0]

    def test_revisao_nao_publica_e_preserva_anterior_valido(self):
        anterior = self.gravar()
        revisao = self.gravar(StatusProcessamento.REVISAO_NECESSARIA)
        self.assertEqual(self.repo.processamento_vigente(self.nome)['id'], anterior)
        self.assertIsNotNone(self.con.execute('SELECT id FROM processamentos_interacoes WHERE id=%s', (revisao,)).fetchone())

    def test_apenas_revisao_nao_fica_vigente(self):
        self.gravar(StatusProcessamento.REVISAO_NECESSARIA)
        self.assertIsNone(self.repo.processamento_vigente(self.nome))

    def test_falha_posterior_preserva_versao_valida(self):
        anterior = self.gravar()
        self.gravar(StatusProcessamento.FALHA)
        self.assertEqual(self.repo.processamento_vigente(self.nome)['id'], anterior)

    def test_retirada_explicita_sem_apagar_historico(self):
        pid = self.gravar()
        self.repo.invalidar_processamento(pid, 'Resultado reconhecido como incorreto no teste.')
        self.assertIsNone(self.repo.processamento_vigente(self.nome))
        linha = self.con.execute('SELECT invalidado_em,motivo_invalidacao FROM processamentos_interacoes WHERE id=%s', (pid,)).fetchone()
        self.assertIsNotNone(linha['invalidado_em'])
        self.assertTrue(linha['motivo_invalidacao'])

    def test_mudanca_de_fonte_invalida_compatibilidade(self):
        self.gravar()
        self.con.execute('UPDATE bulas_interacoes SET trecho_interacoes=%s WHERE nome_normalizado=%s',
                         ('Cafeína reduz a absorção.', self.nome))
        self.assertIsNone(self.repo.processamento_vigente(self.nome))

    def test_revisao_registrada_nao_volta_na_retomada_em_lote(self):
        revisao = self.gravar(StatusProcessamento.REVISAO_NECESSARIA)
        self.assertIsNone(self.repo.processamento_vigente(self.nome))
        self.assertEqual(self.repo.processamento_registrado(self.nome)['id'], revisao)
        lote = {f.nome_normalizado for f in self.repo.selecionar_fontes(limite=None)}
        self.assertNotIn(self.nome, lote)
        forcado = {f.nome_normalizado for f in self.repo.selecionar_fontes(limite=None, reprocessar=True)}
        self.assertIn(self.nome, forcado)

    def test_falha_nao_bloqueia_nova_tentativa_em_lote(self):
        self.gravar(StatusProcessamento.FALHA)
        self.assertIsNone(self.repo.processamento_registrado(self.nome))
        self.assertIn(self.nome, {f.nome_normalizado for f in self.repo.selecionar_fontes(limite=None)})

    def test_retirada_libera_versao_anterior_e_retomada(self):
        anterior = self.gravar()
        incorreta = self.gravar()
        self.repo.invalidar_processamento(incorreta, 'Relação incorreta confirmada na revisão.')
        self.assertEqual(self.repo.processamento_vigente(self.nome)['id'], anterior)
        self.assertEqual(self.repo.processamento_registrado(self.nome)['id'], anterior)

    def test_retirada_pede_motivo(self):
        pid = self.gravar()
        with self.assertRaises(ValueError):
            self.repo.invalidar_processamento(pid, ' ')
        self.assertIsNotNone(self.repo.processamento_vigente(self.nome))
