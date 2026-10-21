from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Protocol

from .erros import DimensaoInvalidaError, ErroBancoError
from .modelos import (
    EmbeddingChunk,
    InteracaoParaVetorizar,
    ResultadoEmbedding,
    StatusEmbedding,
)
from .servico import ServicoEmbeddings


LOGGER = logging.getLogger(__name__)

STATUS_GRAVAVEIS = {
    StatusEmbedding.CONCLUIDO,
    StatusEmbedding.REPROCESSADO,
}


class RepositorioProtocolo(Protocol):
    def hashes_existentes(self, nome_normalizado: str) -> dict[int, str]: ...

    def gravar_embeddings(
        self,
        nome_normalizado: str,
        dimensao: int,
        chunks: list[EmbeddingChunk],
    ) -> int: ...


class RelatorioProtocolo(Protocol):
    def registrar(self, dados: dict) -> None: ...


def _linha_relatorio(
    resultado: ResultadoEmbedding,
    tempo_banco: float,
    tempo_total: float,
) -> dict:
    return {
        "nome_normalizado": resultado.nome_normalizado,
        "quantidade_chunks": resultado.quantidade_chunks,
        "modelo": resultado.modelo,
        "dimensao": resultado.dimensao,
        "quantidade_tokens": resultado.quantidade_tokens,
        "texto_hash": resultado.texto_hash,
        "status": resultado.status.value,
        "tempo_chunking_segundos": round(resultado.tempo_chunking_segundos, 3),
        "tempo_inferencia_segundos": round(resultado.tempo_inferencia_segundos, 3),
        "tempo_banco_segundos": round(tempo_banco, 3),
        "tempo_total_segundos": round(tempo_total, 3),
        "memoria_antes_mb": round(resultado.memoria_antes_mb, 1),
        "pico_memoria_mb": round(resultado.pico_memoria_mb, 1),
        "detalhe_erro": resultado.detalhe_erro,
    }


def processar_lote(
    interacoes: list[InteracaoParaVetorizar],
    servico: ServicoEmbeddings,
    repositorio: RepositorioProtocolo,
    relatorio: RelatorioProtocolo,
    reprocessar: bool = False,
) -> dict[str, int]:
    """Processa sequencialmente, um medicamento por vez, sem paralelismo."""
    contagens: Counter[str] = Counter()

    for indice, interacao in enumerate(interacoes, start=1):
        prefixo = f"[{indice}/{len(interacoes)}] {interacao.nome_normalizado}"
        print(f"{prefixo} — gerando embeddings...")
        inicio_total = time.perf_counter()
        tempo_banco = 0.0

        try:
            ja_gravados = repositorio.hashes_existentes(
                interacao.nome_normalizado
            )
        except Exception as erro:  # noqa: BLE001
            LOGGER.exception(
                "Falha ao consultar hashes de %s: %r",
                interacao.nome_normalizado,
                erro,
            )
            resultado = ResultadoEmbedding(
                status=StatusEmbedding.ERRO_BANCO,
                nome_normalizado=interacao.nome_normalizado,
                modelo=servico.modelo,
                dimensao=servico.dimensao,
                detalhe_erro=repr(erro),
            )
            relatorio.registrar(
                _linha_relatorio(
                    resultado, tempo_banco, time.perf_counter() - inicio_total
                )
            )
            contagens[resultado.status.value] += 1
            print(f"{prefixo} — {resultado.status.value}.")
            continue

        resultado = servico.gerar_para_interacao(
            interacao.nome_normalizado,
            interacao.trecho_interacoes,
            hashes_existentes=ja_gravados,
            reprocessar=reprocessar,
        )

        if resultado.status in STATUS_GRAVAVEIS:
            inicio_banco = time.perf_counter()
            try:
                repositorio.gravar_embeddings(
                    interacao.nome_normalizado,
                    resultado.dimensao,
                    resultado.chunks,
                )
            except DimensaoInvalidaError as erro:
                LOGGER.exception(
                    "Dimensão incompatível em %s: %r",
                    interacao.nome_normalizado,
                    erro,
                )
                resultado.status = StatusEmbedding.DIMENSAO_INVALIDA
                resultado.detalhe_erro = repr(erro)
            except ErroBancoError as erro:
                LOGGER.exception(
                    "Falha de banco em %s: %r", interacao.nome_normalizado, erro
                )
                resultado.status = StatusEmbedding.ERRO_BANCO
                resultado.detalhe_erro = repr(erro)
            except Exception as erro:  # noqa: BLE001
                LOGGER.exception(
                    "Falha inesperada de banco em %s: %r",
                    interacao.nome_normalizado,
                    erro,
                )
                resultado.status = StatusEmbedding.ERRO_BANCO
                resultado.detalhe_erro = repr(erro)
            finally:
                tempo_banco = time.perf_counter() - inicio_banco

        tempo_total = time.perf_counter() - inicio_total
        relatorio.registrar(_linha_relatorio(resultado, tempo_banco, tempo_total))
        contagens[resultado.status.value] += 1

        if resultado.status in STATUS_GRAVAVEIS:
            print(
                f"{prefixo} — {resultado.status.value} "
                f"({resultado.quantidade_chunks} chunk(s), "
                f"{resultado.quantidade_tokens} tokens)."
            )
        else:
            print(f"{prefixo} — {resultado.status.value}.")

    return dict(sorted(contagens.items()))
