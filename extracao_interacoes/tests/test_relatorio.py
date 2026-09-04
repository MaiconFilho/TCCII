import csv
import tempfile
import unittest
from pathlib import Path

from extracao_interacoes.relatorio import COLUNAS_RELATORIO, RelatorioCsv


class TestRelatorioCsv(unittest.TestCase):
    def test_grava_todas_as_colunas_e_preserva_identificadores_textuais(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "resultado.csv"
            with RelatorioCsv(caminho) as relatorio:
                relatorio.registrar(
                    {
                        "nome_normalizado": "medicamento",
                        "numero_registro": "00123",
                        "expediente": "00045",
                        "status": "CONCLUIDO",
                    }
                )
            with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
                linhas = list(csv.DictReader(arquivo, delimiter=";"))

        self.assertEqual(list(linhas[0]), COLUNAS_RELATORIO)
        self.assertEqual(linhas[0]["numero_registro"], "00123")
        self.assertEqual(linhas[0]["expediente"], "00045")


if __name__ == "__main__":
    unittest.main()
