import time
import unittest
from pathlib import Path

from pipelines.identificacao_interacoes.modelos import ConfiguracaoIdentificacao
from pipelines.resumos_interacoes.provedor import ProvedorIsolado


def worker_lento(canal, config, cache):
    canal.send(("ok", {"caminho": "simulado.gguf", "tempo_carga": 0.0}))
    canal.recv()
    time.sleep(60)


def worker_com_erro(canal, config, cache):
    canal.send(("ok", {"caminho": "simulado.gguf", "tempo_carga": 0.0}))
    canal.recv()
    canal.send(("erro", "RuntimeError: erro simulado no worker"))


class TestWorker(unittest.TestCase):
    def test_timeout_encerra_processo_real(self):
        p = ProvedorIsolado(ConfiguracaoIdentificacao(), Path("."), timeout=0.3,
                           timeout_carga=10, informar=lambda s: None, alvo_worker=worker_lento)
        try:
            p.iniciar()
            with self.assertRaises(TimeoutError):
                p.analisar([], esquema={})
            self.assertIsNone(p.processo)
            self.assertIsNone(p.canal)
        finally:
            p.fechar()

    def test_erro_do_worker_e_propagado(self):
        p = ProvedorIsolado(ConfiguracaoIdentificacao(), Path("."), timeout_carga=10,
                           informar=lambda s: None, alvo_worker=worker_com_erro)
        try:
            p.iniciar()
            with self.assertRaisesRegex(RuntimeError, "erro simulado"):
                p.analisar([], esquema={})
        finally:
            p.fechar()
