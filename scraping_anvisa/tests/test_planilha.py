import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from anvisa_scraper.planilha import carregar_medicamentos, normalizar_nome


class TestNormalizarNome(unittest.TestCase):
    def test_remove_diferencas_de_caixa_acento_e_espaco(self) -> None:
        self.assertEqual(
            normalizar_nome("  Ácido   Fólico  "),
            "acido folico",
        )

    def test_planilha_agrupa_nomes_e_preserva_ordem(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "medicamentos.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Medicamentos monitorados"
            sheet.append(["Nome do medicamento", "Registro Anvisa"])
            sheet.append(["Finasterida", "111"])
            sheet.append(["FINASTERIDA", "222"])
            sheet.append(["Ácido Fólico", "333"])
            sheet.append(["acido   folico", "444"])
            workbook.save(caminho)
            workbook.close()

            medicamentos = carregar_medicamentos(caminho)

        self.assertEqual(len(medicamentos), 2)
        self.assertEqual(medicamentos[0].nome_normalizado, "finasterida")
        self.assertEqual(medicamentos[0].quantidade_registros_planilha, 2)
        self.assertEqual(medicamentos[1].nome_normalizado, "acido folico")
        self.assertEqual(medicamentos[1].quantidade_registros_planilha, 2)


if __name__ == "__main__":
    unittest.main()
