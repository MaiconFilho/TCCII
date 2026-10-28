"""Integração real, sem limpar dados. Somente banco cujo nome contém test."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from pipelines.identificacao_interacoes.tests.banco_teste import exigir_banco_isolado
from pipelines.inclusao_medicamentos.repositorio import RepositorioSolicitacoes, ErroSolicitacao
from pipelines.inclusao_medicamentos.__main__ import LOCK_WORKER
from pipelines.scraping_anvisa.anvisa_scraper.normalizacao import normalizar_nome

DSN = os.getenv('INCLUSAO_TEST_DATABASE_URL', '')


@unittest.skipUnless(DSN, 'Defina INCLUSAO_TEST_DATABASE_URL com um banco isolado já migrado.')
class TestFilaPostgres(unittest.TestCase):
    def setUp(self):
        self.c = psycopg.connect(DSN, autocommit=True, row_factory=dict_row)
        exigir_banco_isolado(self.c)
        self.repo = RepositorioSolicitacoes(DSN)
        self.nome = 'Teste ' + uuid4().hex

    def tearDown(self):
        self.c.close()

    def novo(self):
        return self.repo.solicitar(self.nome)

    def test_solicitacoes_simultaneas_normalizadas(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            valores = list(pool.map(self.repo.solicitar, [self.nome, '  ' + self.nome.upper() + '  '] * 8))
        self.assertEqual(len({v['id'] for v in valores}), 1)

    def test_cadastrado_nao_insere_nem_audita_etapas(self):
        self.c.execute("INSERT INTO bulas(nome_normalizado,status) VALUES (%s,'CONCLUIDO')", (normalizar_nome(self.nome),))
        with patch.object(self.repo, 'fonte', side_effect=AssertionError('Não deve auditar as etapas.')):
            dados = self.repo.solicitar(self.nome)
        self.assertIsNone(dados['id'])
        self.assertEqual(dados['estado'], 'CONCLUIDO')

    def test_cadastro_criado_durante_pedido_nao_antecipa_conclusao(self):
        pedido = self.novo()
        self.c.execute("INSERT INTO bulas(nome_normalizado,status) VALUES (%s,'CONCLUIDO')", (normalizar_nome(self.nome),))
        dados = self.repo.solicitar(self.nome)
        self.assertEqual(dados['id'], pedido['id'])
        self.assertEqual(dados['estado'], 'AGUARDANDO')

    def test_confirma_identidade_ja_ativa(self):
        primeiro = self.novo()
        self.nome += ' alias'
        segundo = self.novo()
        bula = {'nome_normalizado': self.repo.obter(primeiro['id'])['nome_normalizado']}
        self.assertEqual(self.repo.confirmar(segundo['id'], bula), primeiro['id'])
        self.assertEqual(self.repo.obter(segundo['id'])['id'], primeiro['id'])

    def test_selecao_precisa_ser_opcao_retornada(self):
        pedido = self.novo()
        opcao = {'nome_normalizado': 'alternativa ' + uuid4().hex, 'nome_produto': 'Alternativa'}
        self.repo.atualizar(pedido['id'], estado='AGUARDANDO_SELECAO', opcoes=[opcao])
        with self.assertRaises(ErroSolicitacao):
            self.repo.selecionar(pedido['id'], 'outra')
        dados = self.repo.selecionar(pedido['id'], opcao['nome_normalizado'])
        self.assertEqual(dados['estado'], 'AGUARDANDO')
        self.assertEqual(self.repo.obter(pedido['id'])['bula_confirmada'], opcao)

    def test_reserva_atomica_e_reinicio_limitado(self):
        pedido = self.novo()
        def reservar(_):
            with self.repo.conectar() as c:
                return self.repo.reservar(c, 2, pedido['id'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            valores = list(pool.map(reservar, range(4)))
        self.assertEqual(sum(v is not None for v in valores), 1)
        self.repo.recuperar(self.c, 2)
        self.assertEqual(self.repo.obter(pedido['id'])['estado'], 'AGUARDANDO')
        self.repo.reservar(self.c, 2, pedido['id'])
        self.repo.recuperar(self.c, 2)
        self.assertEqual(self.repo.obter(pedido['id'])['estado'], 'FALHA')

    def test_lock_global_so_um_trabalhador(self):
        with self.repo.conectar() as outra:
            self.assertTrue(self.c.execute('SELECT pg_try_advisory_lock(%s) AS ok', (LOCK_WORKER,)).fetchone()['ok'])
            self.assertFalse(outra.execute('SELECT pg_try_advisory_lock(%s) AS ok', (LOCK_WORKER,)).fetchone()['ok'])
            self.c.execute('SELECT pg_advisory_unlock(%s)', (LOCK_WORKER,))

    def test_novo_pedido_apos_falha_preserva_historico(self):
        anterior = self.novo()
        self.repo.atualizar(anterior['id'], estado='FALHA')
        novo = self.novo()
        self.assertNotEqual(novo['id'], anterior['id'])
        self.assertEqual(self.repo.obter(anterior['id'])['estado'], 'FALHA')


if __name__ == '__main__':
    unittest.main()
