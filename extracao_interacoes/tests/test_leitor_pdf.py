import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pymupdf

from extracao_interacoes.erros import PdfInvalidoError, PdfSemTextoError
from extracao_interacoes.leitor_pdf import ler_pdf


def criar_pdf(caminho: Path, paginas: list[list[tuple[tuple[int, int], str]]]) -> None:
    documento = pymupdf.open()
    for elementos in paginas:
        pagina = documento.new_page()
        for posicao, texto in elementos:
            pagina.insert_text(posicao, texto)
    documento.save(caminho)
    documento.close()


class TestLeitorPdf(unittest.TestCase):
    def test_le_todas_paginas_em_ordem_com_ids_globais(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "bula.pdf"
            criar_pdf(
                caminho,
                [
                    [((72, 72), "PRIMEIRA LINHA com texto suficiente."), ((72, 100), "SEGUNDA LINHA.")],
                    [((72, 72), "TERCEIRA LINHA da pagina seguinte.")],
                ],
            )
            resultado = ler_pdf(caminho)

        self.assertEqual(resultado.quantidade_paginas, 2)
        self.assertEqual([linha.identificador for linha in resultado.linhas], ["L000001", "L000002", "L000003"])
        self.assertEqual([linha.pagina for linha in resultado.linhas], [1, 1, 2])
        self.assertIn("PRIMEIRA LINHA", resultado.texto_original)

    def test_sort_true_respeita_posicao_visual(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "ordenado.pdf"
            criar_pdf(
                caminho,
                [[((72, 150), "LINHA DE BAIXO com conteudo."), ((72, 72), "LINHA DE CIMA com conteudo."), ((72, 200), "Complemento suficiente.")]],
            )
            resultado = ler_pdf(caminho)

        self.assertIn("LINHA DE CIMA", resultado.linhas[0].texto_original)

    def test_preserva_texto_original_e_mantem_normalizado_separado(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "espacos.pdf"
            criar_pdf(caminho, [[((72, 72), "Texto   oficial com espacos."), ((72, 100), "Conteudo adicional suficiente.")]])
            resultado = ler_pdf(caminho)

        self.assertIn("Texto  oficial", resultado.linhas[0].texto_original)
        self.assertIn("Texto oficial", resultado.linhas[0].texto_normalizado)

    def test_pdf_sem_camada_textual(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "sem-texto.pdf"
            criar_pdf(caminho, [[]])
            with self.assertRaises(PdfSemTextoError):
                ler_pdf(caminho)

    def test_pdf_invalido(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "invalido.pdf"
            caminho.write_text("não é PDF", encoding="utf-8")
            with self.assertRaises(PdfInvalidoError):
                ler_pdf(caminho)

    def test_pdf_inexistente(self) -> None:
        with self.assertRaises(PdfInvalidoError):
            ler_pdf(Path("arquivo-inexistente.pdf"))


if __name__ == "__main__":
    unittest.main()
