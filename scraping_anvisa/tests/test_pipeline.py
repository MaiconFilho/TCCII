import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from anvisa_scraper.modelos import BulaLocalizada, MedicamentoParaColeta
from anvisa_scraper.pipeline import processar_lote


def criar_bula() -> BulaLocalizada:
    return BulaLocalizada(
        nome_normalizado="finasterida",
        nome_produto="finasterida",
        numero_registro="123456789",
        expediente="987654321",
        id_bula_profissional="id-protegido",
        data_publicacao=datetime(2025, 8, 15, tzinfo=timezone.utc),
        data_publicacao_original="2025-08-15T10:30:00.000-0300",
    )


class TestDownloadPdf(unittest.TestCase):
    def test_pipeline_baixa_pdf_e_registra_metricas(self) -> None:
        class ControleFalso:
            def __init__(self) -> None:
                self.finalizacao: dict | None = None

            def concluido(self, _nome_normalizado: str) -> bool:
                return False

            def iniciar(self, _medicamento: MedicamentoParaColeta) -> None:
                return None

            def finalizar(self, _medicamento: MedicamentoParaColeta, **dados) -> None:
                self.finalizacao = dados

        medicamento = MedicamentoParaColeta(
            nome_produto="finasterida",
            nome_normalizado="finasterida",
        )
        controle = ControleFalso()
        saida = io.StringIO()
        with tempfile.TemporaryDirectory() as pasta:
            pasta_pdfs = Path(pasta)
            with redirect_stdout(saida):
                with patch(
                    "anvisa_scraper.pipeline.consultar_mais_recente_por_nome",
                    return_value=criar_bula(),
                ), patch(
                    "anvisa_scraper.pipeline.baixar_pdf",
                    return_value=b"%PDF-1.7\nconteudo",
                ) as baixar:
                    resumo = processar_lote(
                        navegador=object(),
                        medicamentos=[medicamento],
                        controle=controle,
                        pasta_pdfs=pasta_pdfs,
                        intervalo_segundos=3,
                    )

            arquivos = list(pasta_pdfs.glob("*.pdf"))

        baixar.assert_called_once()
        self.assertEqual(resumo["concluidos"], 1)
        self.assertEqual(len(arquivos), 1)
        self.assertIn(
            "bula com publicação mais recente: 15/08/2025",
            saida.getvalue(),
        )
        self.assertNotIn("2026-09-01", saida.getvalue())
        assert controle.finalizacao is not None
        self.assertEqual(
            controle.finalizacao["bula"].data_publicacao_original,
            "2025-08-15T10:30:00.000-0300",
        )
        self.assertGreaterEqual(controle.finalizacao["tempo_download_segundos"], 0.0)
        self.assertGreaterEqual(
            controle.finalizacao["tempo_consulta_segundos"],
            0.0,
        )
        self.assertGreaterEqual(controle.finalizacao["tempo_total_segundos"], 0.0)


if __name__ == "__main__":
    unittest.main()
