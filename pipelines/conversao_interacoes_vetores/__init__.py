"""Conversão dos trechos de interações medicamentosas em vetores (pgvector)."""

from .modelos import (
    ChunkTexto,
    ConfiguracaoEmbeddings,
    EmbeddingChunk,
    InteracaoParaVetorizar,
    ResultadoEmbedding,
    StatusEmbedding,
)
from .provedor_embeddings import ProvedorEmbeddings
from .servico import ServicoEmbeddings

__all__ = [
    "ChunkTexto",
    "ConfiguracaoEmbeddings",
    "EmbeddingChunk",
    "InteracaoParaVetorizar",
    "ProvedorEmbeddings",
    "ResultadoEmbedding",
    "ServicoEmbeddings",
    "StatusEmbedding",
]
