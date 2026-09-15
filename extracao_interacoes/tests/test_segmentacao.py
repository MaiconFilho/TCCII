import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.segmentacao import criar_janelas

from helpers import criar_documento


class TestSegmentacao(unittest.TestCase):
    def test_janelas_cobrem_todas_as_linhas_em_ordem(self) -> None:
        documento = criar_documento([f"linha {i} palavra" for i in range(1, 21)])
        janelas = criar_janelas(documento, lambda texto: len(texto.split()), 15, 3)

        ids = {linha.identificador for janela in janelas for linha in janela.linhas}
        self.assertEqual(ids, {linha.identificador for linha in documento.linhas})
        self.assertEqual(janelas[0].linha_inicio, "L000001")
        self.assertEqual(janelas[-1].linha_fim_inclusiva, "L000020")

    def test_sobreposicao_repete_fronteira_sem_travar(self) -> None:
        documento = criar_documento([f"linha {i}" for i in range(1, 10)])
        janelas = criar_janelas(documento, lambda _texto: 2, 6, 2)

        self.assertGreater(len(janelas), 1)
        for anterior, seguinte in zip(janelas, janelas[1:]):
            self.assertLess(
                int(seguinte.linha_inicio[1:]),
                int(anterior.linha_fim_inclusiva[1:]) + 1,
            )

    def test_linha_maior_que_limite_fica_sozinha(self) -> None:
        documento = criar_documento(["enorme", "curta"])
        janelas = criar_janelas(
            documento,
            lambda texto: 100 if "enorme" in texto else 1,
            10,
            2,
        )

        self.assertEqual(len(janelas[0].linhas), 1)
        self.assertEqual(janelas[-1].linha_fim_inclusiva, "L000002")

    def test_configuracao_de_sobreposicao_invalida(self) -> None:
        documento = criar_documento(["linha"])
        with self.assertRaises(ValueError):
            criar_janelas(documento, lambda _texto: 1, 10, 10)


if __name__ == "__main__":
    unittest.main()
