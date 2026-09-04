import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from extracao_interacoes.erros import (
    ContextoExcedidoError,
    ErroBancoError,
    ErroModeloError,
    PdfSemTextoError,
)
from extracao_interacoes.modelos import (
    BulaParaExtracao,
    DocumentoPdf,
    GeracaoModelo,
)
from extracao_interacoes.pipeline import processar_lote


TITULO = "5. INTERAÇÕES MEDICAMENTOSAS"
TRECHO = f"{TITULO}\nO medicamento A pode reduzir o efeito do medicamento B."
DOCUMENTO = DocumentoPdf(
    texto_com_marcadores=f"[[PÁGINA 1]]\nAPRESENTAÇÃO\n[[PÁGINA 2]]\n{TRECHO}\n7. CUIDADOS",
    texto_sem_marcadores=f"APRESENTAÇÃO\n{TRECHO}\n7. CUIDADOS",
    quantidade_paginas=2,
    quantidade_caracteres=100,
)


def json_encontrado(trecho: str = TRECHO) -> str:
    return json.dumps(
        {
            "encontrado": True,
            "titulo_encontrado": TITULO,
            "trecho_interacoes": trecho,
        },
        ensure_ascii=False,
    )


class ModeloFalso:
    def __init__(self, respostas) -> None:
        self.respostas = list(respostas)
        self.chamadas = []

    def gerar(self, texto_pdf, erro_validacao=None):
        self.chamadas.append((texto_pdf, erro_validacao))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


class RepositorioFalso:
    def __init__(self, erro: Exception | None = None) -> None:
        self.gravacoes = []
        self.erro = erro

    def gravar_resultado(self, bula, trecho_interacoes, reprocessar=False):
        if self.erro:
            raise self.erro
        self.gravacoes.append((bula, trecho_interacoes, reprocessar))


class RelatorioFalso:
    def __init__(self) -> None:
        self.linhas = []

    def registrar(self, dados):
        self.linhas.append(dict(dados))


def criar_bula(caminho: Path) -> BulaParaExtracao:
    return BulaParaExtracao(
        nome_normalizado="finasterida",
        numero_registro="0012345",
        expediente="0009876",
        caminho_pdf=caminho,
    )


def geracao(conteudo: str, truncada: bool = False) -> GeracaoModelo:
    return GeracaoModelo(conteudo, 100, 30, truncada)


class TestPipeline(unittest.TestCase):
    def executar(self, respostas, repositorio=None, erro_leitura=None, reprocessar=False):
        with tempfile.TemporaryDirectory() as pasta:
            bula = criar_bula(Path(pasta) / "bula.pdf")
            modelo = ModeloFalso(respostas)
            banco = repositorio or RepositorioFalso()
            relatorio = RelatorioFalso()
            efeito = erro_leitura if erro_leitura else DOCUMENTO
            with patch("extracao_interacoes.pipeline.ler_pdf", side_effect=[efeito]):
                resumo = processar_lote(
                    [bula],
                    modelo,
                    banco,
                    relatorio,
                    reprocessar=reprocessar,
                )
        return resumo, modelo, banco, relatorio

    def test_grava_trecho_com_os_tres_identificadores_da_mesma_bula(self) -> None:
        resumo, _modelo, banco, relatorio = self.executar([geracao(json_encontrado())])

        self.assertEqual(resumo, {"CONCLUIDO": 1})
        bula, trecho, reprocessar = banco.gravacoes[0]
        self.assertEqual(bula.nome_normalizado, "finasterida")
        self.assertEqual(bula.numero_registro, "0012345")
        self.assertEqual(bula.expediente, "0009876")
        self.assertEqual(trecho, TRECHO)
        self.assertFalse(reprocessar)
        self.assertEqual(relatorio.linhas[0]["titulo_encontrado"], TITULO)

    def test_resposta_invalida_recebe_uma_unica_nova_tentativa(self) -> None:
        respostas = [geracao("não é JSON"), geracao(json_encontrado())]

        resumo, modelo, banco, _relatorio = self.executar(respostas)

        self.assertEqual(resumo, {"CONCLUIDO": 1})
        self.assertEqual(len(modelo.chamadas), 2)
        self.assertIn("JSON inválido", modelo.chamadas[1][1])
        self.assertEqual(len(banco.gravacoes), 1)

    def test_duas_respostas_invalidas_nao_gravam_no_banco(self) -> None:
        resumo, _modelo, banco, relatorio = self.executar(
            [geracao("inválida"), geracao("ainda inválida")]
        )

        self.assertEqual(resumo, {"RESPOSTA_INVALIDA": 1})
        self.assertEqual(banco.gravacoes, [])
        self.assertIn("JSON inválido", relatorio.linhas[0]["detalhe_erro"])

    def test_resposta_truncada_duas_vezes_nao_grava(self) -> None:
        resumo, _modelo, banco, _relatorio = self.executar(
            [geracao("{}", True), geracao("{}", True)]
        )

        self.assertEqual(resumo, {"RESPOSTA_TRUNCADA": 1})
        self.assertEqual(banco.gravacoes, [])

    def test_sem_secao_grava_null(self) -> None:
        resposta = '{"encontrado": false, "titulo_encontrado": null, "trecho_interacoes": null}'

        resumo, _modelo, banco, _relatorio = self.executar([geracao(resposta)])

        self.assertEqual(resumo, {"SEM_SECAO_INTERACOES": 1})
        self.assertIsNone(banco.gravacoes[0][1])

    def test_contexto_excedido_nao_grava(self) -> None:
        erro = ContextoExcedidoError(250000, 262144, 16384)

        resumo, _modelo, banco, relatorio = self.executar([erro])

        self.assertEqual(resumo, {"CONTEXTO_EXCEDIDO": 1})
        self.assertEqual(banco.gravacoes, [])
        self.assertEqual(relatorio.linhas[0]["quantidade_tokens_entrada"], 250000)

    def test_pdf_sem_texto_nao_chama_modelo_nem_banco(self) -> None:
        resumo, modelo, banco, _relatorio = self.executar(
            [],
            erro_leitura=PdfSemTextoError("sem camada textual"),
        )

        self.assertEqual(resumo, {"PDF_SEM_TEXTO": 1})
        self.assertEqual(modelo.chamadas, [])
        self.assertEqual(banco.gravacoes, [])

    def test_erro_modelo_nao_insere_linha(self) -> None:
        resumo, _modelo, banco, _relatorio = self.executar(
            [ErroModeloError("falha técnica")]
        )

        self.assertEqual(resumo, {"ERRO_MODELO": 1})
        self.assertEqual(banco.gravacoes, [])

    def test_erro_banco_e_registrado_sem_nova_insercao(self) -> None:
        banco = RepositorioFalso(ErroBancoError("conexão perdida"))

        resumo, _modelo, banco, relatorio = self.executar(
            [geracao(json_encontrado())],
            repositorio=banco,
        )

        self.assertEqual(resumo, {"ERRO_BANCO": 1})
        self.assertEqual(banco.gravacoes, [])
        self.assertIn("conexão perdida", relatorio.linhas[0]["detalhe_erro"])

    def test_reprocessamento_explicito_e_propagado_ao_banco(self) -> None:
        _resumo, _modelo, banco, _relatorio = self.executar(
            [geracao(json_encontrado())],
            reprocessar=True,
        )

        self.assertTrue(banco.gravacoes[0][2])


if __name__ == "__main__":
    unittest.main()
