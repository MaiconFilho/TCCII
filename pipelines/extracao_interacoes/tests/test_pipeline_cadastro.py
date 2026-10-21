import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pipelines.extracao_interacoes.cadastro import ServicoExtracaoCadastro
from pipelines.extracao_interacoes.servico import ServicoExtracaoInteracoes
from pipelines.extracao_interacoes.pipeline import processar_lote
from pipelines.extracao_interacoes.pipeline_cadastro import processar_cadastro
from pipelines.extracao_interacoes.erros import PdfInvalidoError
from helpers import configuracao, ProvedorFalso, MedidorFalso
from test_pipeline import bula, RepositorioFalso, RelatorioFalso
from test_cadastro import documento_padrao, resposta


class TestPipelineCadastro(unittest.TestCase):
    def montar(self, respostas):
        p = ProvedorFalso(respostas)
        cfg = configuracao(modo_extracao="rapido")
        leitor = Mock(return_value=documento_padrao())
        interacoes = ServicoExtracaoInteracoes(p, cfg, leitor, MedidorFalso)
        cadastro = ServicoExtracaoCadastro(p, cfg, MedidorFalso)
        return interacoes, cadastro, leitor

    def test_um_pdf_um_modelo_e_gravacoes_independentes(self):
        interacoes, cadastro, leitor = self.montar([resposta("dipirona monoidratada")])
        repo_cadastro = Mock()
        banco, relatorio = RepositorioFalso(), RelatorioFalso()
        resumo = processar_lote([bula()], interacoes, banco, relatorio,
            servico_cadastro=cadastro, repositorio_cadastro=repo_cadastro)
        leitor.assert_called_once_with(bula().caminho_pdf)
        self.assertIs(interacoes.provedor, cadastro.provedor)
        self.assertEqual(resumo, {"CADASTRO_CONCLUIDO": 1, "SEM_SECAO_INTERACOES": 1})
        repo_cadastro.gravar.assert_called_once()
        self.assertEqual(len(banco.gravacoes), 1)
        self.assertEqual(relatorio.linhas[0]["principios_ativos"], "dipirona monoidratada")
        self.assertIn("tempo_cadastro_segundos", relatorio.linhas[0])

    def test_principio_pendente_ainda_grava_composicao_e_preserva_interacoes(self):
        interacoes, cadastro, _ = self.montar([resposta(), resposta()])
        repo_cadastro, banco = Mock(), RepositorioFalso()
        resumo = processar_lote([bula()], interacoes, banco, RelatorioFalso(),
            servico_cadastro=cadastro, repositorio_cadastro=repo_cadastro)
        repo_cadastro.gravar.assert_called_once()
        self.assertIsNone(repo_cadastro.gravar.call_args.args[1].principios_ativos)
        self.assertIn("dipirona monoidratada ... 500 mg",
                      repo_cadastro.gravar.call_args.args[1].composicao)
        self.assertEqual(len(banco.gravacoes), 1)
        self.assertEqual(resumo["CADASTRO_REVISAO_MANUAL"], 1)

    def test_cadastro_pendente_nao_reextrai_interacoes_existentes(self):
        interacoes, cadastro, leitor = self.montar([resposta("dipirona monoidratada")])
        interacoes.extrair = Mock(side_effect=AssertionError("interações não deveriam ser extraídas"))
        banco, repo_cadastro, relatorio = RepositorioFalso(), Mock(), RelatorioFalso()
        pendente = replace(bula(), precisa_interacoes=False)

        resumo = processar_lote([pendente], interacoes, banco, relatorio,
            servico_cadastro=cadastro, repositorio_cadastro=repo_cadastro)

        leitor.assert_called_once_with(pendente.caminho_pdf)
        interacoes.extrair.assert_not_called()
        self.assertEqual(banco.gravacoes, [])
        repo_cadastro.gravar.assert_called_once()
        self.assertEqual(resumo, {"CADASTRO_CONCLUIDO": 1, "IGNORADO_JA_EXISTENTE": 1})
        self.assertEqual(relatorio.linhas[0]["status"], "IGNORADO_JA_EXISTENTE")

    def test_interacoes_pendentes_nao_reextraem_cadastro_existente(self):
        interacoes, cadastro, leitor = self.montar([])
        cadastro.extrair = Mock(side_effect=AssertionError("cadastro não deveria ser extraído"))
        banco, repo_cadastro, relatorio = RepositorioFalso(), Mock(), RelatorioFalso()
        pendente = replace(bula(), precisa_cadastro=False)

        resumo = processar_lote([pendente], interacoes, banco, relatorio,
            servico_cadastro=cadastro, repositorio_cadastro=repo_cadastro)

        leitor.assert_called_once_with(pendente.caminho_pdf)
        cadastro.extrair.assert_not_called()
        repo_cadastro.gravar.assert_not_called()
        self.assertEqual(len(banco.gravacoes), 1)
        self.assertEqual(resumo, {"CADASTRO_IGNORADO_JA_EXISTENTE": 1, "SEM_SECAO_INTERACOES": 1})
        self.assertEqual(relatorio.linhas[0]["status_cadastro"], "IGNORADO_JA_EXISTENTE")

    def test_falha_leitura_cadastro_pendente_preserva_interacoes(self):
        interacoes, cadastro, leitor = self.montar([])
        leitor.side_effect = PdfInvalidoError("arquivo inválido")
        banco, repo_cadastro, relatorio = RepositorioFalso(), Mock(), RelatorioFalso()

        resumo = processar_lote([replace(bula(), precisa_interacoes=False)],
            interacoes, banco, relatorio,
            servico_cadastro=cadastro, repositorio_cadastro=repo_cadastro)

        self.assertEqual(banco.gravacoes, [])
        repo_cadastro.gravar.assert_not_called()
        self.assertEqual(resumo, {"CADASTRO_ERRO_LEITURA": 1, "IGNORADO_JA_EXISTENTE": 1})
        self.assertIn("arquivo inválido", relatorio.linhas[0]["detalhe_cadastro"])

    def test_pdf_invalido_nao_e_relido_nem_grava_cadastro(self):
        interacoes, cadastro, leitor = self.montar([])
        leitor.side_effect = PdfInvalidoError("arquivo inválido")
        repo_cadastro = Mock()
        resumo = processar_lote([bula()], interacoes, RepositorioFalso(), RelatorioFalso(),
            servico_cadastro=cadastro, repositorio_cadastro=repo_cadastro)
        leitor.assert_called_once()
        repo_cadastro.gravar.assert_not_called()
        self.assertEqual(resumo, {"CADASTRO_ERRO_LEITURA": 1, "PDF_INVALIDO": 1})

    def test_simulacao_nunca_grava_no_banco(self):
        _, cadastro, _ = self.montar([resposta("dipirona monoidratada")])
        repo_cadastro = Mock()
        r = processar_cadastro(bula(), documento_padrao(), cadastro, repo_cadastro, executar=False)
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertFalse(r.gravado)
        repo_cadastro.gravar.assert_not_called()

    def test_erro_de_principio_grava_apenas_composicao(self):
        _, cadastro, _ = self.montar([resposta(), resposta()])
        repo_cadastro = Mock()
        r = processar_cadastro(bula(), documento_padrao(), cadastro, repo_cadastro)
        self.assertEqual(r.status, "REVISAO_MANUAL")
        self.assertTrue(r.gravado)
        repo_cadastro.gravar.assert_called_once()

    def test_somente_composicao_dispensa_modelo_e_grava_trecho(self):
        repo_cadastro = Mock()
        servico = ServicoExtracaoCadastro(None, None)
        r = processar_cadastro(bula(), documento_padrao(), servico, repo_cadastro,
                              somente_composicao=True)
        self.assertEqual(r.status, "COMPOSICAO_EXTRAIDA")
        self.assertTrue(r.gravado)
        self.assertIsNone(r.principios_ativos)
        repo_cadastro.gravar.assert_called_once()

    def test_erro_banco_de_cadastro_nao_afeta_as_interacoes(self):
        interacoes, cadastro, _ = self.montar([resposta("dipirona monoidratada")])
        repo_cadastro = Mock()
        repo_cadastro.gravar.side_effect = RuntimeError("conexão perdida")
        banco = RepositorioFalso()
        resumo = processar_lote([bula()], interacoes, banco, RelatorioFalso(),
            servico_cadastro=cadastro, repositorio_cadastro=repo_cadastro)
        self.assertEqual(len(banco.gravacoes), 1)
        self.assertEqual(resumo["CADASTRO_ERRO_BANCO"], 1)

    def test_exige_par_servico_repositorio(self):
        with self.assertRaises(ValueError):
            processar_lote([], Mock(), Mock(), Mock(), servico_cadastro=Mock())
