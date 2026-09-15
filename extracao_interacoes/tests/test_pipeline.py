import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import ErroBancoError
from extracao_interacoes.modelos import (
    BulaParaExtracao,
    MetodoExtracao,
    ResultadoExtracao,
    StatusExtracao,
)
from extracao_interacoes.pipeline import processar_lote


class ServicoFalso:
    def __init__(self, resultados):
        self.resultados = list(resultados)
        self.chamadas = []

    def extrair(self, nome_normalizado, numero_registro, expediente, caminho_pdf):
        self.chamadas.append(
            (nome_normalizado, numero_registro, expediente, caminho_pdf)
        )
        return self.resultados.pop(0)


class RepositorioFalso:
    def __init__(self, erro=None):
        self.erro = erro
        self.gravacoes = []

    def gravar_resultado(self, bula, trecho, reprocessar=False, **metricas):
        if self.erro:
            raise self.erro
        self.gravacoes.append((bula, trecho, reprocessar, metricas))


class RelatorioFalso:
    def __init__(self):
        self.linhas = []

    def registrar(self, linha):
        self.linhas.append(linha)


def bula():
    return BulaParaExtracao("medicamento", "123", "456", Path("bula.pdf"))


class TestPipeline(unittest.TestCase):
    def test_banco_e_csv_recebem_tempo_de_extracao_sem_persistencia(self):
        resultado = ResultadoExtracao(
            StatusExtracao.CONCLUIDO, metodo=MetodoExtracao.HIBRIDO_LLM,
            trecho_interacoes="trecho", tempo_leitura_segundos=0.125,
            tempo_inferencia_segundos=3.25,
        )
        banco, relatorio = RepositorioFalso(), RelatorioFalso()
        with patch("extracao_interacoes.pipeline.time.perf_counter", side_effect=[10.0, 14.5]) as relogio:
            processar_lote([bula()], ServicoFalso([resultado]), banco, relatorio)
        self.assertEqual(relogio.call_count, 2)
        self.assertEqual(banco.gravacoes[0][3], {
            "status_extracao": "CONCLUIDO",
            "detalhe_revisao": None,
            "tempo_leitura_segundos": 0.125,
            "tempo_inferencia_segundos": 3.25,
            "tempo_total_segundos": 4.5,
        })
        for nome, valor in banco.gravacoes[0][3].items():
            if nome.startswith("tempo_"):
                self.assertEqual(relatorio.linhas[0][nome], valor)

    def test_revisao_manual_grava_pendencia_e_tempos_sem_inventar_texto(self):
        resultado = ResultadoExtracao(
            StatusExtracao.REVISAO_MANUAL,
            metodo=MetodoExtracao.REVISAO_MANUAL,
            detalhe_erro="Limite nao confirmado.",
            tempo_leitura_segundos=0.1,
            tempo_inferencia_segundos=2.0,
        )
        banco, relatorio = RepositorioFalso(), RelatorioFalso()
        resumo = processar_lote([bula()], ServicoFalso([resultado]), banco, relatorio)
        self.assertEqual(resumo, {"REVISAO_MANUAL": 1})
        self.assertEqual(len(banco.gravacoes), 1)
        self.assertIsNone(banco.gravacoes[0][1])
        self.assertEqual(banco.gravacoes[0][3]["status_extracao"], "REVISAO_MANUAL")
        self.assertEqual(banco.gravacoes[0][3]["detalhe_revisao"], "Limite nao confirmado.")

    def test_grava_somente_resultado_completamente_validado(self) -> None:
        resultado = ResultadoExtracao(
            StatusExtracao.CONCLUIDO,
            metodo=MetodoExtracao.LLM,
            titulo_encontrado="5. INTERAÇÕES MEDICAMENTOSAS",
            trecho_interacoes="trecho literal",
            linha_inicio="L000010",
            linha_fim_exclusiva="L000020",
        )
        banco = RepositorioFalso()
        relatorio = RelatorioFalso()

        resumo = processar_lote([bula()], ServicoFalso([resultado]), banco, relatorio)

        self.assertEqual(resumo, {"CONCLUIDO": 1})
        self.assertEqual(banco.gravacoes[0][1], "trecho literal")
        self.assertEqual(relatorio.linhas[0]["metodo_extracao"], "LLM")

    def test_ausencia_confirmada_grava_null(self) -> None:
        resultado = ResultadoExtracao(StatusExtracao.SEM_SECAO_INTERACOES)
        banco = RepositorioFalso()
        processar_lote([bula()], ServicoFalso([resultado]), banco, RelatorioFalso())
        self.assertIsNone(banco.gravacoes[0][1])

    def test_falha_de_validacao_nao_altera_banco(self) -> None:
        resultado = ResultadoExtracao(StatusExtracao.RESPOSTA_INVALIDA)
        banco = RepositorioFalso()
        processar_lote([bula()], ServicoFalso([resultado]), banco, RelatorioFalso(), reprocessar=True)
        self.assertEqual(banco.gravacoes, [])

    def test_reprocessamento_so_e_propagado_apos_sucesso(self) -> None:
        resultado = ResultadoExtracao(
            StatusExtracao.CONCLUIDO,
            trecho_interacoes="novo trecho",
            metodo=MetodoExtracao.LLM,
        )
        banco = RepositorioFalso()
        processar_lote([bula()], ServicoFalso([resultado]), banco, RelatorioFalso(), reprocessar=True)
        self.assertTrue(banco.gravacoes[0][2])

    def test_erro_de_banco_e_registrado_sem_segunda_gravacao(self) -> None:
        resultado = ResultadoExtracao(
            StatusExtracao.CONCLUIDO,
            trecho_interacoes="trecho",
            metodo=MetodoExtracao.LLM,
        )
        banco = RepositorioFalso(ErroBancoError("conexão perdida"))
        relatorio = RelatorioFalso()
        resumo = processar_lote([bula()], ServicoFalso([resultado]), banco, relatorio)
        self.assertEqual(resumo, {"ERRO_BANCO": 1})
        self.assertIn("conexão perdida", relatorio.linhas[0]["detalhe_erro"])

    def test_csv_recebe_todas_as_metricas(self) -> None:
        resultado = ResultadoExtracao(
            StatusExtracao.LIMITE_MEMORIA,
            quantidade_paginas=3,
            quantidade_janelas=5,
            quantidade_chamadas_llm=2,
            tokens_entrada_total=100,
            tokens_saida_total=20,
            memoria_antes_mb=900,
            pico_memoria_mb=1100,
            memoria_depois_mb=950,
        )
        relatorio = RelatorioFalso()
        processar_lote([bula()], ServicoFalso([resultado]), RepositorioFalso(), relatorio)
        linha = relatorio.linhas[0]
        self.assertEqual(linha["quantidade_janelas"], 5)
        self.assertEqual(linha["pico_memoria_mb"], 1100)
        self.assertEqual(linha["memoria_depois_mb"], 950)
        self.assertEqual(linha["status"], "LIMITE_MEMORIA")


if __name__ == "__main__":
    unittest.main()
