import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from anvisa_scraper.modelos import BulaLocalizada, MedicamentoParaColeta
from anvisa_scraper.erros import (
    BloqueioAnvisaError,
    LimiteRequisicoesAnvisaError,
)
from anvisa_scraper.pipeline import processar_lote


def criar_bula() -> BulaLocalizada:
    return BulaLocalizada(
        nome_normalizado="finasterida",
        nome_produto="finasterida",
        numero_registro="123456789",
        expediente="987654321",
        id_bula_profissional="id-protegido",
        data_publicacao=datetime(2025, 8, 15, tzinfo=timezone.utc),
    )


class ControleFalso:
    def __init__(self) -> None:
        self.finalizacoes: list[dict] = []

    @property
    def finalizacao(self) -> dict | None:
        return self.finalizacoes[-1] if self.finalizacoes else None

    def concluido(self, _nome_normalizado: str) -> bool:
        return False

    def recuperar_pdf_existente(
        self,
        _medicamento: MedicamentoParaColeta,
        _pasta_pdfs: Path,
    ) -> bool:
        return False

    def iniciar(self, _medicamento: MedicamentoParaColeta) -> None:
        return None

    def finalizar(self, _medicamento: MedicamentoParaColeta, **dados) -> None:
        self.finalizacoes.append(dados)


class TestDownloadPdf(unittest.TestCase):
    def test_pipeline_baixa_pdf_e_registra_metricas(self) -> None:
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
        self.assertIn("finasterida - consultando", saida.getvalue())
        self.assertIn("finasterida - concluído", saida.getvalue())
        self.assertNotIn("bula com publicação mais recente", saida.getvalue())
        self.assertNotIn("2026-09-01", saida.getvalue())
        assert controle.finalizacao is not None
        self.assertGreaterEqual(controle.finalizacao["tempo_download_segundos"], 0.0)
        self.assertGreaterEqual(
            controle.finalizacao["tempo_consulta_segundos"],
            0.0,
        )
        self.assertGreaterEqual(controle.finalizacao["tempo_total_segundos"], 0.0)

    def test_429_isolado_aguarda_e_segue_para_proximo_item(self) -> None:
        medicamentos = [
            MedicamentoParaColeta("CLO", "clo"),
            MedicamentoParaColeta("CLEXANE", "clexane"),
        ]
        controle = ControleFalso()
        saida = io.StringIO()
        with tempfile.TemporaryDirectory() as pasta:
            with redirect_stdout(saida), patch(
                "anvisa_scraper.pipeline.consultar_mais_recente_por_nome",
                side_effect=[
                    LimiteRequisicoesAnvisaError(
                        "HTTP 429 durante consulta de 'CLO'.",
                        retry_after_segundos=7,
                    ),
                    criar_bula(),
                ],
            ), patch(
                "anvisa_scraper.pipeline.baixar_pdf",
                return_value=b"%PDF-1.7\nconteudo",
            ), patch("anvisa_scraper.pipeline.time.sleep") as esperar:
                resumo = processar_lote(
                    navegador=object(),
                    medicamentos=medicamentos,
                    controle=controle,
                    pasta_pdfs=Path(pasta),
                    intervalo_segundos=3,
                    espera_limite_segundos=5,
                )

        self.assertEqual(resumo["limitados"], 1)
        self.assertEqual(resumo["concluidos"], 1)
        self.assertEqual(
            [item["status"] for item in controle.finalizacoes],
            ["LIMITE_REQUISICOES", "CONCLUIDO"],
        )
        esperar.assert_called_once_with(7)
        self.assertIn("seguindo para o próximo", saida.getvalue())

    def test_tres_429_consecutivos_interrompem_por_seguranca(self) -> None:
        medicamentos = [
            MedicamentoParaColeta(f"CLO {indice}", f"clo {indice}")
            for indice in range(1, 4)
        ]
        controle = ControleFalso()
        limites = [
            LimiteRequisicoesAnvisaError("HTTP 429", retry_after_segundos=5)
            for _ in medicamentos
        ]
        with tempfile.TemporaryDirectory() as pasta, patch(
            "anvisa_scraper.pipeline.consultar_mais_recente_por_nome",
            side_effect=limites,
        ), patch("anvisa_scraper.pipeline.time.sleep") as esperar:
            with self.assertRaises(BloqueioAnvisaError):
                processar_lote(
                    navegador=object(),
                    medicamentos=medicamentos,
                    controle=controle,
                    pasta_pdfs=Path(pasta),
                    intervalo_segundos=3,
                    espera_limite_segundos=5,
                    max_limites_consecutivos=3,
                )

        self.assertEqual(
            [item["status"] for item in controle.finalizacoes],
            ["LIMITE_REQUISICOES"] * 3,
        )
        self.assertEqual(esperar.call_count, 2)


if __name__ == "__main__":
    unittest.main()
