"""A API da base fixa não depende de fila nem inicia escrita/processamento."""
import unittest
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from apps.api_interacoes.app import criar_app
from apps.api_interacoes.configuracao import ConfiguracaoApi
from apps.api_interacoes.tests.test_api import RepositorioFalso

class TestApiSomenteLeitura(unittest.TestCase):
    def test_manutencao_nao_abre_nem_consulta_banco_e_preserva_cors(self):
        repo=Mock()
        with TestClient(criar_app(ConfiguracaoApi(manutencao=True),repositorio=repo)) as client:
            response=client.get('/api/interacoes',params={'medicamento':'aerolin'},headers={'Origin':'http://localhost:3000'})
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json()['erro'],'manutencao')
            self.assertEqual(response.headers['retry-after'],'60')
            self.assertEqual(response.headers['access-control-allow-origin'],'http://localhost:3000')
        self.assertEqual(repo.mock_calls,[])
    def test_flag_manutencao_do_ambiente(self):
        with patch.dict('os.environ',{'API_MANUTENCAO':'true','API_INCLUSAO_MEDICAMENTOS':'false'}):
            self.assertTrue(ConfiguracaoApi.do_ambiente().manutencao)
        with patch.dict('os.environ',{'API_MANUTENCAO':'incorreto'}):
            with self.assertRaisesRegex(ValueError,'API_MANUTENCAO'):
                ConfiguracaoApi.do_ambiente()
    def test_sem_rotas_de_preparacao_nem_importacao_de_trabalhador(self):
        app=criar_app(ConfiguracaoApi(),repositorio=RepositorioFalso())
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/processamentos',json={'nome':'novo'}).status_code,404)
            self.assertNotIn('/api/processamentos',client.get('/openapi.json').json()['paths'])
            self.assertFalse(hasattr(app.state,'solicitacoes'))
    def test_configuracao_legada_nao_ativa_worker_silenciosamente(self):
        with self.assertRaisesRegex(ValueError,'fila legada'):
            criar_app(ConfiguracaoApi(inclusao_habilitada=True),repositorio=RepositorioFalso())
    def test_cors_nao_autoriza_post(self):
        with TestClient(criar_app(ConfiguracaoApi(),repositorio=RepositorioFalso())) as client:
            result=client.options('/api/interacoes',headers={'Origin':'http://localhost:3000','Access-Control-Request-Method':'POST'})
            self.assertEqual(result.status_code,400)
