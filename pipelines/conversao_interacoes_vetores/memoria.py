from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

import psutil

from .erros import LimiteMemoriaError


TRetorno = TypeVar("TRetorno")


class MedidorMemoria:
    """Mesma estratégia adotada em extracao_interacoes: RSS antes, pico e depois."""

    def __init__(
        self,
        limite_mb: int = 5500,
        processo=None,
        intervalo_amostragem: float = 0.05,
    ) -> None:
        self.limite_mb = limite_mb
        self.processo = processo or psutil.Process()
        self.intervalo_amostragem = intervalo_amostragem
        self.memoria_antes_mb = self._ler_mb()
        self.pico_mb = self.memoria_antes_mb

    def _ler_mb(self) -> float:
        return self.processo.memory_info().rss / (1024 * 1024)

    def ler_atual_mb(self) -> float:
        atual = self._ler_mb()
        self.pico_mb = max(self.pico_mb, atual)
        return atual

    def verificar_antes_da_inferencia(self) -> float:
        atual = self.ler_atual_mb()
        if self.limite_mb > 0 and atual > self.limite_mb:
            raise LimiteMemoriaError(
                f"Processo com {atual:.1f} MB; limite configurado: "
                f"{self.limite_mb} MB. Nova inferência não iniciada."
            )
        return atual

    def executar(self, funcao: Callable[[], TRetorno]) -> tuple[TRetorno, float]:
        self.verificar_antes_da_inferencia()
        parar = threading.Event()

        def amostrar() -> None:
            while not parar.wait(self.intervalo_amostragem):
                self.pico_mb = max(self.pico_mb, self._ler_mb())

        monitor = threading.Thread(target=amostrar, daemon=True)
        monitor.start()
        inicio = time.perf_counter()
        try:
            retorno = funcao()
        finally:
            tempo = time.perf_counter() - inicio
            parar.set()
            monitor.join(timeout=1)
            self.ler_atual_mb()
        return retorno, tempo
