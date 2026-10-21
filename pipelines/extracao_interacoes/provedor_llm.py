from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .modelos import RespostaLLM


Mensagem = dict[str, str]


class ProvedorLLM(Protocol):
    """Contrato injetável; testes não precisam carregar pesos nem acessar a rede."""

    def contar_tokens(self, texto: str) -> int: ...

    def analisar(
        self, mensagens: Sequence[Mensagem], *, esquema: dict | None = None
    ) -> RespostaLLM: ...

    def testar(self) -> None: ...
