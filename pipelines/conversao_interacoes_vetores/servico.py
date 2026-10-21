from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping

from .chunking import dividir_em_chunks
from .erros import (
    DimensaoInvalidaError,
    ErroChunkingError,
    ErroInferenciaError,
    LimiteMemoriaError,
    ModeloInvalidoError,
    SemTextoError,
)
from .hashing import calcular_hash_chunk, calcular_hash_documento
from .memoria import MedidorMemoria
from .modelo_embeddings import validar_vetores
from .modelos import (
    ChunkTexto,
    ConfiguracaoEmbeddings,
    EmbeddingChunk,
    ResultadoEmbedding,
    StatusEmbedding,
)
from .provedor_embeddings import ProvedorEmbeddings


LOGGER = logging.getLogger(__name__)


class ServicoEmbeddings:
    """Caso de uso independente da CLI e do banco, com provedor injetado.

    O modelo já chega carregado pelo chamador: nenhum método desta classe
    instancia pesos, de modo que a aplicação web futura possa manter uma única
    instância viva e atendê-la sob demanda.
    """

    def __init__(
        self,
        provedor: ProvedorEmbeddings,
        configuracao: ConfiguracaoEmbeddings,
        fabrica_medidor: Callable[[int], MedidorMemoria] = MedidorMemoria,
    ) -> None:
        self.provedor = provedor
        self.configuracao = configuracao
        self.fabrica_medidor = fabrica_medidor
        # Uma requisição por vez: protege máquinas com pouca RAM.
        self._bloqueio = threading.Lock()
        self._em_andamento: set[str] = set()

    # ------------------------------------------------------------ utilidades

    @property
    def modelo(self) -> str:
        return self.provedor.identificador_modelo

    @property
    def dimensao(self) -> int:
        return self.provedor.dimensao

    def esta_processando(self, nome_normalizado: str) -> bool:
        return nome_normalizado in self._em_andamento

    def _tamanho_chunk_efetivo(self) -> int:
        """Nunca ultrapassa o limite real do modelo, evitando truncamento."""
        limite = int(getattr(self.provedor, "limite_tokens", 0) or 0)
        if limite <= 0:
            return self.configuracao.tamanho_chunk
        return min(self.configuracao.tamanho_chunk, limite)

    def preparar_chunks(self, texto: str) -> list[ChunkTexto]:
        """Chunking isolado: barato o suficiente para decidir idempotência."""
        return dividir_em_chunks(
            texto,
            self.provedor,
            self._tamanho_chunk_efetivo(),
            self.configuracao.sobreposicao_chunk,
        )

    def calcular_hashes(
        self, nome_normalizado: str, chunks: list[ChunkTexto]
    ) -> dict[int, str]:
        return {
            chunk.indice: calcular_hash_chunk(
                nome_normalizado, chunk.indice, chunk.texto, self.modelo
            )
            for chunk in chunks
        }

    # ----------------------------------------------------------- caso de uso

    def gerar_para_interacao(
        self,
        nome_normalizado: str,
        trecho_interacoes: str | None,
        *,
        hashes_existentes: Mapping[int, str] | None = None,
        reprocessar: bool = False,
    ) -> ResultadoEmbedding:
        """Gera os vetores de um medicamento sem tocar no banco de dados.

        ``hashes_existentes`` permite que o chamador informe o que já está
        gravado: se os hashes coincidirem e ``reprocessar`` for falso, a
        inferência é totalmente ignorada (idempotência).
        """
        with self._bloqueio:
            if nome_normalizado in self._em_andamento:
                # Não deve ocorrer com o bloqueio global, mas mantém o invariante
                # explícito para o uso futuro sob demanda.
                return ResultadoEmbedding(
                    status=StatusEmbedding.IGNORADO_JA_EXISTENTE,
                    nome_normalizado=nome_normalizado,
                    modelo=self.modelo,
                    dimensao=self.dimensao,
                    detalhe_erro="Processamento já em andamento para este nome.",
                )
            self._em_andamento.add(nome_normalizado)
            try:
                return self._executar(
                    nome_normalizado,
                    trecho_interacoes,
                    hashes_existentes or {},
                    reprocessar,
                )
            finally:
                self._em_andamento.discard(nome_normalizado)

    def _executar(
        self,
        nome_normalizado: str,
        trecho_interacoes: str | None,
        hashes_existentes: Mapping[int, str],
        reprocessar: bool,
    ) -> ResultadoEmbedding:
        resultado = ResultadoEmbedding(
            status=StatusEmbedding.CONCLUIDO,
            nome_normalizado=nome_normalizado,
            modelo=self.modelo,
            dimensao=self.dimensao,
        )

        if not trecho_interacoes or not trecho_interacoes.strip():
            resultado.status = StatusEmbedding.SEM_TEXTO
            resultado.detalhe_erro = "trecho_interacoes vazio ou ausente."
            return resultado

        medidor = self.fabrica_medidor(self.configuracao.limite_memoria_mb)
        resultado.memoria_antes_mb = medidor.memoria_antes_mb

        inicio_chunking = time.perf_counter()
        try:
            chunks = self.preparar_chunks(trecho_interacoes)
        except ErroChunkingError as erro:
            resultado.status = StatusEmbedding.ERRO_CHUNKING
            resultado.detalhe_erro = repr(erro)
            resultado.tempo_chunking_segundos = time.perf_counter() - inicio_chunking
            return resultado
        resultado.tempo_chunking_segundos = time.perf_counter() - inicio_chunking
        resultado.quantidade_tokens = sum(chunk.quantidade_tokens for chunk in chunks)

        hashes = self.calcular_hashes(nome_normalizado, chunks)
        resultado.texto_hash = calcular_hash_documento(
            hashes[indice] for indice in sorted(hashes)
        )

        ja_gravado = dict(hashes_existentes)
        identico = ja_gravado == hashes
        if identico and not reprocessar:
            resultado.status = StatusEmbedding.IGNORADO_JA_EXISTENTE
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            resultado.pico_memoria_mb = medidor.pico_mb
            return resultado

        try:
            vetores, tempo = medidor.executar(
                lambda: self.provedor.codificar([chunk.texto for chunk in chunks])
            )
        except LimiteMemoriaError as erro:
            resultado.status = StatusEmbedding.LIMITE_MEMORIA
            resultado.detalhe_erro = repr(erro)
            resultado.pico_memoria_mb = medidor.pico_mb
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            return resultado
        except ModeloInvalidoError as erro:
            resultado.status = StatusEmbedding.MODELO_INVALIDO
            resultado.detalhe_erro = repr(erro)
            resultado.pico_memoria_mb = medidor.pico_mb
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            return resultado
        except Exception as erro:  # noqa: BLE001 - qualquer falha vira ERRO_INFERENCIA
            LOGGER.exception(
                "Falha de inferência para %s: %r", nome_normalizado, erro
            )
            resultado.status = StatusEmbedding.ERRO_INFERENCIA
            resultado.detalhe_erro = repr(
                erro if isinstance(erro, ErroInferenciaError)
                else ErroInferenciaError(f"{type(erro).__name__}: {erro}")
            )
            resultado.pico_memoria_mb = medidor.pico_mb
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            return resultado

        resultado.tempo_inferencia_segundos = tempo
        resultado.pico_memoria_mb = medidor.pico_mb
        resultado.memoria_depois_mb = medidor.ler_atual_mb()

        if len(vetores) != len(chunks):
            resultado.status = StatusEmbedding.DIMENSAO_INVALIDA
            resultado.detalhe_erro = (
                f"O provedor devolveu {len(vetores)} vetores para "
                f"{len(chunks)} chunks."
            )
            return resultado

        try:
            validar_vetores(vetores, self.dimensao)
        except DimensaoInvalidaError as erro:
            resultado.status = StatusEmbedding.DIMENSAO_INVALIDA
            resultado.detalhe_erro = repr(erro)
            return resultado

        resultado.chunks = [
            EmbeddingChunk(
                chunk_index=chunk.indice,
                texto_chunk=chunk.texto,
                texto_hash=hashes[chunk.indice],
                quantidade_tokens=chunk.quantidade_tokens,
                vetor=tuple(float(valor) for valor in vetor),
            )
            for chunk, vetor in zip(chunks, vetores, strict=True)
        ]
        resultado.status = (
            StatusEmbedding.REPROCESSADO if ja_gravado else StatusEmbedding.CONCLUIDO
        )
        return resultado
