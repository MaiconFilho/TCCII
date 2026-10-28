import copy
import json
import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from pipelines.inclusao_medicamentos.estado import pronto, vetores_compativeis, resposta, bula_local, mesma_bula
from pipelines.inclusao_medicamentos.orquestrador import Orquestrador, ExigeRevisao, FalhaTemporaria, PossePerdida
from pipelines.inclusao_medicamentos.__main__ import tratar_pedido, versao_codigo
from pipelines.scraping_anvisa.anvisa_scraper.api_anvisa import consultar_opcoes_por_nome
from pipelines.scraping_anvisa.anvisa_scraper.erros import MedicamentoNaoEncontradoError, RespostaAnvisaError, BloqueioAnvisaError
from pipelines.scraping_anvisa.anvisa_scraper.modelos import MedicamentoParaColeta
from pipelines.conversao_interacoes_vetores.hashing import calcular_hash_chunk

MODELO = 'intfloat/multilingual-e5-small'


def fonte_pronta():
    texto = 'A pode aumentar o efeito de B.'
    return dict(nome_normalizado='a', status='CONCLUIDO', numero_registro='1', expediente='2',
        registro_extracao='1', expediente_extracao='2', sha256_pdf='pdf', sha256_pdf_fonte='pdf',
        sha256_cadastro='pdf', principios_ativos='A', composicao='A 5 mg', status_extracao='CONCLUIDO',
        trecho_interacoes=texto, processamento_id=10, status_identificacao='CONCLUIDO_COM_RELACOES',
        resumos_completos=True, vetores=[dict(chunk_index=0, texto_chunk=texto,
            texto_hash=calcular_hash_chunk('a', 0, texto, MODELO))])


class TestEstado(unittest.TestCase):
    def test_trabalhador_detecta_alteracao_de_codigo_entre_pedidos(self):
        with TemporaryDirectory() as pasta, patch('pipelines.inclusao_medicamentos.__main__.BASE', Path(pasta)):
            antes = versao_codigo()
            (Path(pasta) / 'atualizacao.py').write_text('# atualização', encoding='utf-8')
            self.assertNotEqual(antes, versao_codigo())

    def test_pronto_e_fonte_desatualizada(self):
        fonte = fonte_pronta()
        self.assertTrue(pronto(fonte, MODELO))
        for campo, valor in [('status_identificacao', None), ('resumos_completos', False),
                             ('sha256_pdf', 'novo'), ('expediente', 'novo'), ('status_extracao', 'REVISAO_MANUAL')]:
            with self.subTest(campo=campo):
                self.assertFalse(pronto({**fonte, campo: valor}, MODELO))

    def test_vetor_antigo_e_outro_modelo(self):
        fonte = fonte_pronta()
        self.assertFalse(vetores_compativeis(fonte, 'outro'))
        fonte['trecho_interacoes'] += ' Um novo efeito.'
        self.assertFalse(vetores_compativeis(fonte, MODELO))

    def test_bula_confirmada_diferente_nao_reutiliza_pdf_anterior(self):
        fonte = fonte_pronta()
        bula = bula_local(fonte)
        self.assertTrue(mesma_bula(fonte, bula))
        self.assertFalse(mesma_bula(fonte, {**bula, 'expediente': 'nova-versao'}))

    def test_hash_da_fonte_marcada_precisa_ser_atual(self):
        fonte = fonte_pronta()
        fonte['vetores'][0].update(fonte_sha256=hashlib.sha256(fonte['trecho_interacoes'].encode()).hexdigest(), total_chunks_fonte=1)
        self.assertTrue(vetores_compativeis(fonte, MODELO))
        fonte['trecho_interacoes'] += ' '
        self.assertFalse(vetores_compativeis(fonte, MODELO))

    def test_resposta_nao_expoe_erro_nem_caminho(self):
        dados = resposta(dict(id='x', erro='senha e traceback', caminho_pdf='C:/privado', progresso={'interno': 'x'}))
        self.assertNotIn('erro', dados)
        self.assertNotIn('caminho_pdf', dados)
        self.assertNotIn('progresso', dados)

    def test_falha_antiga_expoe_etapa_sem_detalhes_internos(self):
        for etapa, trecho in [('ANVISA', 'buscar a bula no Bulário'), ('DOWNLOAD', 'baixar o documento'),
                              ('EXTRACAO', 'extrair'), ('RESUMOS', 'resumos')]:
            dados = resposta(dict(estado='FALHA', etapa=etapa, mensagem='Mensagem antiga', erro='segredo'))
            self.assertIn(trecho, dados['mensagem'])
            self.assertNotIn('erro', dados)


class TestOrquestracao(unittest.TestCase):
    def setUp(self):
        self.repo = Mock(modelo_vetores=MODELO)
        self.coletor = Mock()
        self.executar = Mock()
        self.pedido = dict(id='pedido', nome_normalizado='a', nome_solicitado='A', tentativas=1, progresso={})
        self.repo.confirmar.return_value = 'pedido'
        self.coletor.confirmar.return_value = {'nome_normalizado': 'a'}

    def test_comeca_pelo_scraping_sem_consultar_cadastro(self):
        def confirmar(pedido):
            self.repo.fonte.assert_not_called()
            self.assertEqual(pedido['nome_solicitado'], 'A')
            return None
        self.coletor.confirmar.side_effect = confirmar
        Orquestrador(self.repo, self.coletor, self.executar).processar(self.pedido)
        self.coletor.confirmar.assert_called_once_with(self.pedido)
        self.assertEqual(self.repo.atualizar.call_args.kwargs['etapa'], 'ANVISA')
        self.repo.fonte.assert_not_called()
        self.executar.assert_not_called()

    def test_reserva_perdida_nao_executa_nem_sobrescreve_estado(self):
        guarda = Mock(side_effect=PossePerdida('sessão perdida'))
        with self.assertRaises(PossePerdida):
            tratar_pedido(self.repo, self.pedido, Orquestrador(self.repo, self.coletor, self.executar, guarda).processar)
        self.executar.assert_not_called()
        self.repo.atualizar.assert_not_called()

    def test_ambiguidade_para_antes_download(self):
        self.repo.fonte.return_value = None
        self.coletor.confirmar.return_value = None
        Orquestrador(self.repo, self.coletor, self.executar).processar(self.pedido)
        self.coletor.baixar.assert_not_called()
        self.executar.assert_not_called()

    def test_identidade_reutilizada_nao_processa(self):
        self.repo.fonte.return_value = None
        self.repo.confirmar.return_value = 'principal'
        Orquestrador(self.repo, self.coletor, self.executar).processar(self.pedido)
        self.coletor.baixar.assert_not_called()

    def test_retoma_somente_resumos_e_vinculos(self):
        pronta = fonte_pronta()
        pendente = {**pronta, 'resumos_completos': False}
        self.repo.fonte.side_effect = [pendente, pendente, pendente, pronta]
        Orquestrador(self.repo, self.coletor, self.executar).processar(self.pedido)
        self.assertEqual([c.args[0] for c in self.executar.call_args_list], ['vinculos', 'resumos'])

    def test_revisao_nao_vira_conclusao(self):
        fonte = {**fonte_pronta(), 'status_identificacao': None, 'ultimo_status': 'REVISAO_NECESSARIA'}
        self.repo.fonte.return_value = fonte
        self.executar.side_effect = ExigeRevisao('revisão')
        tratar_pedido(self.repo, self.pedido, Orquestrador(self.repo, self.coletor, self.executar).processar)
        self.assertEqual(self.repo.atualizar.call_args.kwargs['estado'], 'REVISAO_NECESSARIA')

    def test_ausencia_confirmada_nao_executa_etapas(self):
        self.repo.fonte.return_value = None
        self.coletor.confirmar.side_effect = MedicamentoNaoEncontradoError('sem resultado')
        tratar_pedido(self.repo, self.pedido, Orquestrador(self.repo, self.coletor, self.executar).processar)
        self.assertEqual(self.repo.atualizar.call_args.kwargs['estado'], 'NAO_ENCONTRADO')
        self.executar.assert_not_called()

    def test_timeout_tem_limite_de_tentativas(self):
        processar = Mock(side_effect=FalhaTemporaria('timeout'))
        tratar_pedido(self.repo, self.pedido, processar, 3)
        self.repo.reagendar.assert_called_once()
        self.repo.reset_mock()
        tratar_pedido(self.repo, {**self.pedido, 'tentativas': 3}, processar, 3)
        self.repo.reagendar.assert_not_called()
        self.assertEqual(self.repo.atualizar.call_args.kwargs['estado'], 'FALHA')

    def test_bloqueio_nao_insiste_nem_vira_inexistente(self):
        tratar_pedido(self.repo, self.pedido, Mock(side_effect=BloqueioAnvisaError('403')))
        self.repo.reagendar.assert_not_called()
        self.assertEqual(self.repo.atualizar.call_args.kwargs['estado'], 'FALHA')
        self.assertIn('buscar a bula no Bulário Eletrônico', self.repo.atualizar.call_args.kwargs['mensagem'])
        self.assertEqual(self.repo.atualizar.call_args.kwargs['erro'], '403')


class TestConsultaExterna(unittest.TestCase):
    def consultar(self, content, status=200):
        navegador = Mock()
        navegador.execute_async_script.return_value = {'status': status, 'body': json.dumps({'content': content})}
        return consultar_opcoes_por_nome(navegador, MedicamentoParaColeta('fora da planilha', 'fora da planilha'))

    def test_fora_da_planilha_e_mais_recente(self):
        base = dict(nomeProduto='Fora da Planilha', numeroRegistro='100', expediente='200', idBulaProfissionalProtegido='x')
        opcoes = self.consultar([{**base, 'data': '2024-01-01'}, {**base, 'data': '2026-01-01', 'idBulaProfissionalProtegido': 'nova'}])
        self.assertEqual(opcoes[0].id_bula_profissional, 'nova')

    def test_alternativas_nao_sao_escolhidas(self):
        base = dict(numeroRegistro='100', expediente='200', data='2026-01-01', idBulaProfissionalProtegido='x')
        opcoes = self.consultar([{**base, 'nomeProduto': 'Alternativa A'}, {**base, 'nomeProduto': 'Alternativa B'}])
        self.assertEqual(len(opcoes), 2)
        self.assertNotIn('fora da planilha', [o.nome_normalizado for o in opcoes])

    def test_zero_resultados(self):
        with self.assertRaises(MedicamentoNaoEncontradoError):
            self.consultar([])

    def test_erro_nao_e_ausencia(self):
        with self.assertRaises(RespostaAnvisaError):
            self.consultar([], 503)
        with self.assertRaises(RespostaAnvisaError):
            self.consultar(None)


if __name__ == '__main__':
    unittest.main()
