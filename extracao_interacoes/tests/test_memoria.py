import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import LimiteMemoriaError
from extracao_interacoes.memoria import MedidorMemoria


class Info:
    def __init__(self, rss):
        self.rss = rss


class ProcessoFalso:
    def __init__(self, valores_mb):
        self.valores = list(valores_mb)

    def memory_info(self):
        valor = self.valores.pop(0) if len(self.valores) > 1 else self.valores[0]
        return Info(valor * 1024 * 1024)


class TestMedidorMemoria(unittest.TestCase):
    def test_nao_inicia_inferencia_acima_do_limite(self) -> None:
        medidor = MedidorMemoria(500, processo=ProcessoFalso([400, 600]))
        executou = False

        def funcao():
            nonlocal executou
            executou = True

        with self.assertRaises(LimiteMemoriaError):
            medidor.executar(funcao)
        self.assertFalse(executou)

    def test_registra_memoria_antes_e_pico(self) -> None:
        medidor = MedidorMemoria(
            1000,
            processo=ProcessoFalso([100, 110, 150]),
            intervalo_amostragem=1,
        )
        retorno, _tempo = medidor.executar(lambda: "ok")
        self.assertEqual(retorno, "ok")
        self.assertEqual(medidor.memoria_antes_mb, 100)
        self.assertGreaterEqual(medidor.pico_mb, 110)


if __name__ == "__main__":
    unittest.main()
