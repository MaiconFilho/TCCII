"""Os adaptadores chamam os serviços existentes sem repetir partes concluídas."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipelines.inclusao_medicamentos import etapas
from pipelines.inclusao_medicamentos.tests.test_fluxo import fonte_pronta


class TestExtracaoUnitaria(unittest.TestCase):
    def executar(self, fonte, resultado=None):
        fonte = {**fonte, 'caminho_pdf': 'C:/teste/bula.pdf'}
        with ExitStack() as stack:
            stack.enter_context(patch.object(etapas.RepositorioSolicitacoes, 'fonte', return_value=fonte))
            carregar = stack.enter_context(patch('pipelines.extracao_interacoes.modelo_llm.LlamaCppProvider.carregar'))
            stack.enter_context(patch('pipelines.extracao_interacoes.main.configuracao_do_ambiente', return_value=Mock()))
            repo = stack.enter_context(patch('pipelines.extracao_interacoes.repositorio.RepositorioInteracoes')).return_value
            servico = stack.enter_context(patch('pipelines.extracao_interacoes.servico.ServicoExtracaoInteracoes')).return_value
            cadastro = stack.enter_context(patch('pipelines.extracao_interacoes.pipeline_cadastro.processar_cadastro'))
            cadastro.return_value = SimpleNamespace(status='CONCLUIDO')
            if resultado:
                servico.extrair.return_value = resultado
            etapas.extracao('dsn_simulado', 'a')
            return carregar, repo, servico, cadastro

    def test_concluido_nao_carrega_modelo(self):
        carregar, repo, servico, cadastro = self.executar(fonte_pronta())
        carregar.assert_not_called()
        servico.extrair.assert_not_called()
        cadastro.assert_not_called()

    def test_falta_so_cadastro_preserva_topico(self):
        carregar, repo, servico, cadastro = self.executar({**fonte_pronta(), 'principios_ativos': None})
        carregar.assert_called_once()
        servico.extrair.assert_not_called()
        repo.gravar_resultado.assert_not_called()
        cadastro.assert_called_once()
        self.assertFalse(cadastro.call_args.kwargs['reprocessar'])

    def test_falta_so_topico_preserva_cadastro(self):
        resultado = SimpleNamespace(status=SimpleNamespace(value='CONCLUIDO'), trecho_interacoes='Trecho novo.',
            detalhe_erro='', tempo_leitura_segundos=1, tempo_inferencia_segundos=2)
        carregar, repo, servico, cadastro = self.executar({**fonte_pronta(), 'status_extracao': None}, resultado)
        servico.extrair.assert_called_once()
        cadastro.assert_not_called()
        repo.gravar_resultado.assert_called_once()


if __name__ == '__main__':
    unittest.main()
