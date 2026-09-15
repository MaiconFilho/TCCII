from __future__ import annotations

import hashlib
from collections.abc import Iterable


SEPARADOR = "\x1f"


def calcular_hash_chunk(
    nome_normalizado: str,
    chunk_index: int,
    texto_chunk: str,
    modelo: str,
) -> str:
    """Hash estável de (nome_normalizado, chunk_index, texto_chunk, modelo).

    Qualquer alteração no texto ou troca de modelo produz um hash diferente,
    o que permite detectar reprocessamento necessário sem comparar vetores.
    """
    material = SEPARADOR.join(
        (nome_normalizado, str(chunk_index), texto_chunk, modelo)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def calcular_hash_documento(hashes_dos_chunks: Iterable[str]) -> str:
    """Resume os hashes dos chunks em um único identificador do documento."""
    material = SEPARADOR.join(hashes_dos_chunks)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
