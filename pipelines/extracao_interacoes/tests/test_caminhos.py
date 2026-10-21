import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pipelines.caminhos import resolver_caminho


class TestCaminhosMonorepo(unittest.TestCase):
    def test_pdf_antigo_absoluto_e_relativo_encontra_arquivo_movido(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            pdf = raiz / 'pipelines/scraping_anvisa/pdfs/bula.pdf'
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b'pdf')
            with patch('pipelines.caminhos.RAIZ', raiz):
                for antigo in (raiz / 'scraping_anvisa/pdfs/bula.pdf',
                               Path('scraping_anvisa/pdfs/bula.pdf')):
                    self.assertEqual(resolver_caminho(antigo), pdf.resolve())

    def test_arquivo_existente_tem_prioridade_e_ausente_nao_e_inventado(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            existente = raiz / 'scraping_anvisa/pdfs/bula.pdf'
            existente.parent.mkdir(parents=True)
            existente.write_bytes(b'original')
            with patch('pipelines.caminhos.RAIZ', raiz):
                self.assertEqual(resolver_caminho(existente), existente.resolve())
                ausente = raiz / 'scraping_anvisa/pdfs/ausente.pdf'
                self.assertEqual(resolver_caminho(ausente), ausente.resolve())

    def test_nao_redireciona_caminho_de_outro_projeto(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            outro = raiz / 'outro/scraping_anvisa/pdfs/bula.pdf'
            with patch('pipelines.caminhos.RAIZ', raiz):
                self.assertEqual(resolver_caminho(outro), outro.resolve())
