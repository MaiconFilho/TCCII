from __future__ import annotations

from .erros import ErroChunkingError
from .modelos import ChunkTexto
from .provedor_embeddings import ProvedorEmbeddings


def dividir_em_chunks(
    texto: str,
    provedor: ProvedorEmbeddings,
    tamanho_chunk: int,
    sobreposicao: int,
) -> list[ChunkTexto]:
    """Divide o texto em janelas de tokens contíguas, com sobreposição.

    Estratégia:

    1. o texto é tokenizado com o tokenizer do próprio modelo, sem tokens
       especiais, de modo que a contagem corresponda ao que será codificado;
    2. se o total couber em ``tamanho_chunk``, devolve-se um único chunk com o
       ``chunk_index`` 0 e o **texto original intacto**, sem passar por
       destokenização;
    3. textos maiores são cortados em janelas de ``tamanho_chunk`` tokens que
       avançam ``tamanho_chunk - sobreposicao`` tokens por vez;
    4. a última janela sempre alcança o último token, logo nenhum conteúdo é
       descartado nem truncado silenciosamente;
    5. a ordem de leitura é preservada pelo ``indice`` crescente.
    """
    if texto is None or not texto.strip():
        raise ErroChunkingError("Texto vazio não pode ser dividido em chunks.")
    if tamanho_chunk <= 0:
        raise ErroChunkingError("O tamanho do chunk deve ser maior que zero.")
    if sobreposicao < 0:
        raise ErroChunkingError("A sobreposição não pode ser negativa.")
    if sobreposicao >= tamanho_chunk:
        raise ErroChunkingError(
            "A sobreposição deve ser menor que o tamanho do chunk."
        )

    try:
        tokens = list(provedor.tokenizar(texto))
    except Exception as erro:  # noqa: BLE001 - encapsula falhas do tokenizer
        raise ErroChunkingError(
            f"Falha ao tokenizar o trecho: {type(erro).__name__}: {erro}"
        ) from erro

    total = len(tokens)
    if total == 0:
        raise ErroChunkingError(
            "O tokenizer não produziu tokens para um texto não vazio."
        )

    if total <= tamanho_chunk:
        # Texto curto: chunk_index = 0 e texto_chunk = trecho completo.
        return [ChunkTexto(indice=0, texto=texto, quantidade_tokens=total)]

    passo = tamanho_chunk - sobreposicao
    chunks: list[ChunkTexto] = []
    inicio = 0
    indice = 0
    while inicio < total:
        fim = min(inicio + tamanho_chunk, total)
        fatia = tokens[inicio:fim]
        try:
            texto_chunk = provedor.destokenizar(fatia)
        except Exception as erro:  # noqa: BLE001
            raise ErroChunkingError(
                f"Falha ao reconstruir o chunk {indice}: "
                f"{type(erro).__name__}: {erro}"
            ) from erro
        if not texto_chunk.strip():
            raise ErroChunkingError(
                f"O chunk {indice} ficou vazio após a destokenização."
            )
        chunks.append(
            ChunkTexto(
                indice=indice,
                texto=texto_chunk,
                quantidade_tokens=len(fatia),
            )
        )
        indice += 1
        if fim >= total:
            break
        inicio += passo

    return chunks


def contar_tokens_do_texto(texto: str, provedor: ProvedorEmbeddings) -> int:
    """Contagem usada para decidir entre chunk único e janelas múltiplas."""
    try:
        return provedor.contar_tokens(texto)
    except Exception as erro:  # noqa: BLE001
        raise ErroChunkingError(
            f"Falha ao contar tokens: {type(erro).__name__}: {erro}"
        ) from erro
