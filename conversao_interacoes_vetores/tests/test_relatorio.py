import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.relatorio import (
    COLUNAS_RELATORIO,
    RelatorioEmbeddingsCsv,
)


class TestRelatorioCsv(unittest.TestCase):
    def test_colunas_seguem_a_especificacao(self):
        self.assertEqual(
            COLUNAS_RELATORIO,
            [
                "nome_normalizado",
                "quantidade_chunks",
                "modelo",
                "dimensao",
                "quantidade_tokens",
                "texto_hash",
                "status",
                "tempo_chunking_segundos",
                "tempo_inferencia_segundos",
                "tempo_banco_segundos",
                "tempo_total_segundos",
                "memoria_antes_mb",
                "pico_memoria_mb",
                "detalhe_erro",
            ],
        )

    def test_escreve_cabecalho_e_linhas(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "sub" / "embeddings.csv"
            with RelatorioEmbeddingsCsv(caminho) as relatorio:
                relatorio.registrar(
                    {"nome_normalizado": "dipirona", "status": "CONCLUIDO"}
                )

            with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
                linhas = list(csv.DictReader(arquivo, delimiter=";"))

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["nome_normalizado"], "dipirona")
        self.assertEqual(linhas[0]["status"], "CONCLUIDO")
        self.assertEqual(linhas[0]["detalhe_erro"], "")

    def test_registrar_fora_do_contexto_e_recusado(self):
        with tempfile.TemporaryDirectory() as pasta:
            relatorio = RelatorioEmbeddingsCsv(Path(pasta) / "x.csv")

            with self.assertRaises(RuntimeError):
                relatorio.registrar({"nome_normalizado": "dipirona"})


if __name__ == "__main__":
    unittest.main()
