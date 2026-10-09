"""Carrega o provedor GGUF já existente em extracao_interacoes.

O mesmo ``LlamaCppProvider`` (uma instância, CPU, inferência serial, semáforo
de concorrência 1) é reaproveitado com limites próprios desta etapa: contexto,
orçamento de saída e lote não herdam os 256 tokens da localização de seções.

O import é tardio: apenas a CLI de identificação carrega o llama.cpp.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .modelos import ConfiguracaoIdentificacao

if TYPE_CHECKING:
    from extracao_interacoes.modelo_llm import LlamaCppProvider


def configuracao_llm(configuracao: ConfiguracaoIdentificacao):
    """Converte para a ConfiguracaoLLM aceita pelo provedor da extração."""
    from extracao_interacoes.modelos import ConfiguracaoLLM

    return ConfiguracaoLLM(
        backend="llama_cpp",
        repositorio_modelo=configuracao.repositorio_modelo,
        arquivo_modelo=configuracao.arquivo_modelo,
        tamanho_contexto=configuracao.tamanho_contexto,
        tokens_janela=configuracao.tokens_janela,
        sobreposicao_tokens=configuracao.sobreposicao_tokens,
        max_tokens_saida=configuracao.max_tokens_saida,
        threads=configuracao.threads,
        tamanho_lote=configuracao.tamanho_lote,
        camadas_gpu=configuracao.camadas_gpu,
        temperatura=configuracao.temperatura,
        limite_memoria_mb=configuracao.limite_memoria_mb,
        concorrencia=configuracao.concorrencia,
        modo_extracao="completo",
    )


def carregar_provedor(
    configuracao: ConfiguracaoIdentificacao, diretorio_cache: Path | None
) -> "LlamaCppProvider":
    from extracao_interacoes.modelo_llm import LlamaCppProvider

    return LlamaCppProvider.carregar(configuracao_llm(configuracao), diretorio_cache=diretorio_cache)
