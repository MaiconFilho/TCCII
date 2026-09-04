import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

from extracao_interacoes.erros import PdfInvalidoError, PdfSemTextoError
from extracao_interacoes.leitor_pdf import ler_pdf


def criar_pdf(caminho: Path, paginas: list[str]) -> None:
    documento = fitz.open()
    for texto in paginas:
        pagina = documento.new_page()
        if texto:
            pagina.insert_text((72, 72), texto)
    documento.save(caminho)
    documento.close()


class TestLeitorPdf(unittest.TestCase):
    def test_le_pdf_e_preserva_ordem_das_paginas(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "bula.pdf"
            criar_pdf(
                caminho,
                [
                    "PRIMEIRA PAGINA com texto suficiente para a leitura integral.",
                    "SEGUNDA PAGINA continua o documento na ordem correta.",
                ],
            )

            resultado = ler_pdf(caminho)

        self.assertEqual(resultado.quantidade_paginas, 2)
        self.assertLess(
            resultado.texto_com_marcadores.index("PRIMEIRA PAGINA"),
            resultado.texto_com_marcadores.index("SEGUNDA PAGINA"),
        )
        self.assertIn("[[PÁGINA 1]]", resultado.texto_com_marcadores)
        self.assertIn("[[PÁGINA 2]]", resultado.texto_com_marcadores)
        self.assertNotIn("[[PÁGINA", resultado.texto_sem_marcadores)

    def test_pdf_sem_camada_textual(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "sem-texto.pdf"
            criar_pdf(caminho, [""])

            with self.assertRaises(PdfSemTextoError):
                ler_pdf(caminho)

    def test_pdf_invalido(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "invalido.pdf"
            caminho.write_text("não é PDF", encoding="utf-8")

            with self.assertRaises(PdfInvalidoError):
                ler_pdf(caminho)


if __name__ == "__main__":
    unittest.main()
