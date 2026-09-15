from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class ProvedorEmbeddings(Protocol):
    """Contrato injetável; os testes não carregam pesos nem acessam a rede."""

    @property
    def identificador_modelo(self) -> str: ...

    @property
    def dimensao(self) -> int: ...

    @property
    def limite_tokens(self) -> int: ...

    def contar_tokens(self, texto: str) -> int: ...

    def tokenizar(self, texto: str) -> list[int]: ...

    def destokenizar(self, tokens: Sequence[int]) -> str: ...

    def codificar(self, textos: list[str]) -> list[list[float]]: ...

    def testar(self) -> None: ...
