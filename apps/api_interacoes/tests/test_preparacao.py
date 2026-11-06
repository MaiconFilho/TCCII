import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from fastapi.testclient import TestClient
from apps.api_interacoes.app import criar_app, TrabalhadorAutomatico
from apps.api_interacoes.configuracao import ConfiguracaoApi
from apps.api_interacoes.tests.test_api import RepositorioFalso
from pipelines.inclusao_medicamentos.repositorio import ErroSolicitacao


class TestPreparacaoApi(unittest.TestCase):
    def setUp(self):
        self.fila = Mock()
        self.trabalhador = Mock()
        self.id = str(uuid4())
        self.dados = dict(id=self.id, nome_solicitado='Novo', nome_confirmado=None,
                          estado='AGUARDANDO', etapa='AGUARDANDO', mensagem='Aguardando.', opcoes=[])
        self.cliente = TestClient(criar_app(ConfiguracaoApi(dsn='nao_conectar', inclusao_habilitada=True), RepositorioFalso(), self.fila, self.trabalhador))

    def test_aceita_e_devolve_identificador(self):
        self.fila.solicitar.return_value = self.dados
        r = self.cliente.post('/api/processamentos', json={'nome': 'Novo'})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()['id'], self.id)
        self.trabalhador.garantir.assert_called_once()

    def test_pronto_retorna_200(self):
        self.fila.solicitar.return_value = {**self.dados, 'estado': 'CONCLUIDO', 'id': None}
        self.assertEqual(self.cliente.post('/api/processamentos', json={'nome': 'Novo'}).status_code, 200)
        self.trabalhador.garantir.assert_not_called()

    def test_acompanha_e_seleciona(self):
        self.fila.obter.return_value = {**self.dados, 'erro': 'segredo', 'caminho_pdf': '/interno'}
        r = self.cliente.get('/api/processamentos/' + self.id)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('erro', r.json())
        self.fila.selecionar.return_value = self.dados
        r = self.cliente.post(f'/api/processamentos/{self.id}/selecao', json={'nome_normalizado': 'alternativa'})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.trabalhador.garantir.call_count, 2)

    def test_retomada_na_inicializacao_da_api(self):
        with self.cliente:
            self.trabalhador.garantir.assert_called_once()

    def test_situacoes_finais_nao_relancam_trabalhador(self):
        for estado in ('CONCLUIDO', 'FALHA', 'NAO_ENCONTRADO', 'REVISAO_NECESSARIA', 'AGUARDANDO_SELECAO'):
            self.fila.obter.return_value = {**self.dados, 'estado': estado}
            self.assertEqual(self.cliente.get('/api/processamentos/' + self.id).status_code, 200)
        self.trabalhador.garantir.assert_not_called()

    def test_falha_anvisa_antiga_explica_etapa_sem_expor_erro(self):
        self.fila.obter.return_value = {**self.dados, 'estado': 'FALHA', 'etapa': 'ANVISA',
            'mensagem': 'Mensagem genérica antiga', 'erro': 'credenciais e detalhes internos'}
        dados = self.cliente.get('/api/processamentos/' + self.id).json()
        self.assertIn('buscar a bula no Bulário Eletrônico da Anvisa', dados['mensagem'])
        self.assertNotIn('erro', dados)

    def test_erro_publico_e_validacao(self):
        self.fila.solicitar.side_effect = ErroSolicitacao('Serviço indisponível.')
        self.assertEqual(self.cliente.post('/api/processamentos', json={'nome': 'Novo'}).status_code, 503)
        self.assertEqual(self.cliente.post('/api/processamentos', json={'nome': ''}).status_code, 422)
        self.assertEqual(self.cliente.get('/api/processamentos/invalido').status_code, 422)

    def test_cors_permite_post(self):
        r = self.cliente.options('/api/processamentos', headers={'Origin': 'http://localhost:3000',
            'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
        self.assertEqual(r.status_code, 200)


class TestInclusaoSuspensa(unittest.TestCase):
    def test_padrao_bloqueia_fila_e_trabalhador_inclusive_na_retomada(self):
        fila, trabalhador = Mock(), Mock()
        identificador = str(uuid4())
        with TestClient(criar_app(ConfiguracaoApi(dsn='nao_conectar'), RepositorioFalso(), fila, trabalhador)) as cliente:
            respostas = [
                cliente.post('/api/processamentos', json={'nome': 'Novo'}),
                cliente.get(f'/api/processamentos/{identificador}'),
                cliente.post(f'/api/processamentos/{identificador}/selecao', json={'nome_normalizado': 'novo'}),
            ]
            for resposta in respostas:
                self.assertEqual(resposta.status_code, 503)
                self.assertIn('desativada', resposta.json()['mensagem'])
            self.assertEqual(cliente.get('/api/interacoes', params={'medicamento': 'inexistente'}).status_code, 404)
        self.assertEqual(fila.mock_calls, [])
        trabalhador.garantir.assert_not_called()

    def test_configuracao_exige_reativacao_explicita(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(ConfiguracaoApi.do_ambiente().inclusao_habilitada)
        with patch.dict('os.environ', {'API_INCLUSAO_MEDICAMENTOS': 'true'}, clear=True):
            self.assertTrue(ConfiguracaoApi.do_ambiente().inclusao_habilitada)
        with patch.dict('os.environ', {'API_INCLUSAO_MEDICAMENTOS': 'talvez'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'API_INCLUSAO_MEDICAMENTOS'):
                ConfiguracaoApi.do_ambiente()

    @patch('apps.api_interacoes.app.subprocess.Popen')
    def test_nao_inicia_processo_mesmo_com_autostart_ativo(self, iniciar):
        TrabalhadorAutomatico(ConfiguracaoApi()).garantir()
        iniciar.assert_not_called()


class TestTrabalhadorAutomatico(unittest.TestCase):
    @patch('apps.api_interacoes.app.subprocess.Popen')
    def test_inicia_processo_existente_sem_duplicar(self, iniciar):
        iniciar.return_value.poll.return_value = None
        trabalhador = TrabalhadorAutomatico(ConfiguracaoApi(dsn='fila', dsn_trabalhador='pipelines', python_trabalhador='python-pipelines', inclusao_habilitada=True))
        trabalhador.garantir()
        trabalhador.garantir()
        iniciar.assert_called_once()
        args, kwargs = iniciar.call_args
        self.assertEqual(args[0], ['python-pipelines', '-u', '-m', 'pipelines.inclusao_medicamentos'])
        self.assertEqual(kwargs['env']['WORKER_DATABASE_URL'], 'pipelines')
        self.assertFalse(kwargs['shell'])
        self.assertTrue((kwargs['cwd'] / 'pipelines/inclusao_medicamentos/__main__.py').is_file())

    @patch('apps.api_interacoes.app.subprocess.Popen')
    @patch('apps.api_interacoes.app.time.monotonic')
    def test_relanca_encerrado_com_intervalo(self, relogio, iniciar):
        relogio.return_value = 100
        iniciar.return_value.poll.return_value = 1
        trabalhador = TrabalhadorAutomatico(ConfiguracaoApi(dsn='fila', inclusao_habilitada=True))
        trabalhador.garantir()
        trabalhador.garantir()
        self.assertEqual(iniciar.call_count, 1)
        relogio.return_value = 131
        trabalhador.garantir()
        self.assertEqual(iniciar.call_count, 2)

    @patch('apps.api_interacoes.app.subprocess.Popen')
    def test_trabalhador_externo_desliga_partida_local(self, iniciar):
        TrabalhadorAutomatico(ConfiguracaoApi(trabalhador_automatico=False, inclusao_habilitada=True)).garantir()
        iniciar.assert_not_called()

    @patch('apps.api_interacoes.app.subprocess.Popen', side_effect=OSError('ambiente ausente'))
    def test_falha_na_partida_nao_derruba_consulta(self, iniciar):
        with self.assertLogs('api_interacoes', 'ERROR'):
            TrabalhadorAutomatico(ConfiguracaoApi(inclusao_habilitada=True)).garantir()
        iniciar.assert_called_once()


if __name__ == '__main__':
    unittest.main()
