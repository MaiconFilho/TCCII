from __future__ import annotations

import gc
import logging
import math
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .erros import (
    ErroCarregamentoModeloError,
    ErroInferenciaError,
    ModeloInvalidoError,
    VetorInvalidoError,
)
from .modelos import ConfiguracaoEmbeddings


LOGGER = logging.getLogger(__name__)


def calcular_threads_seguros(configurado: int, cpus: int | None = None) -> int:
    """Mesma heurística de extracao_interacoes: metade das CPUs, no máximo 4."""
    if configurado > 0:
        return configurado
    disponiveis = cpus if cpus is not None else (os.cpu_count() or 2)
    return max(1, min(4, disponiveis // 2 or 1))


class ProvedorEmbeddingsTransformers:
    """Encoder local carregado uma única vez e usado de forma serial.

    Usa Transformers puro (AutoTokenizer + AutoModel) em vez de
    sentence-transformers: as dependências já existem no projeto, o consumo de
    RAM é menor e o tokenizer fica exposto para o chunking.
    """

    def __init__(
        self,
        configuracao: ConfiguracaoEmbeddings,
        tokenizer: Any,
        modelo: Any,
        torch_modulo: Any,
        dimensao: int,
        limite_tokens: int,
    ) -> None:
        self.configuracao = configuracao
        self.tokenizer = tokenizer
        self.modelo = modelo
        self._torch = torch_modulo
        self._dimensao = dimensao
        self._limite_tokens = limite_tokens
        # Concorrência 1: uma inferência por vez, mesmo com vários chamadores.
        self._semaforo = threading.BoundedSemaphore(configuracao.concorrencia)

    # ----------------------------------------------------------- informações

    @property
    def identificador_modelo(self) -> str:
        return self.configuracao.modelo_id

    @property
    def dimensao(self) -> int:
        return self._dimensao

    @property
    def limite_tokens(self) -> int:
        """Tokens úteis por chunk, já descontando prefixo e tokens especiais."""
        return self._limite_tokens

    # ---------------------------------------------------------- carregamento

    @classmethod
    def carregar(
        cls,
        configuracao: ConfiguracaoEmbeddings,
        diretorio_cache: Path | None = None,
        fabrica_tokenizer: Callable[..., Any] | None = None,
        fabrica_modelo: Callable[..., Any] | None = None,
        torch_modulo: Any | None = None,
    ) -> "ProvedorEmbeddingsTransformers":
        try:
            configuracao.validar()
        except ValueError as erro:
            raise ModeloInvalidoError(str(erro)) from erro

        if torch_modulo is None:
            try:
                import torch
            except ImportError as erro:
                raise ErroCarregamentoModeloError(
                    "torch não está instalado. Instale o requirements.txt."
                ) from erro
            torch_modulo = torch

        if fabrica_tokenizer is None or fabrica_modelo is None:
            try:
                from transformers import AutoModel, AutoTokenizer
            except ImportError as erro:
                raise ErroCarregamentoModeloError(
                    "transformers não está instalado. Instale o requirements.txt."
                ) from erro
            fabrica_tokenizer = fabrica_tokenizer or AutoTokenizer.from_pretrained
            fabrica_modelo = fabrica_modelo or AutoModel.from_pretrained

        # O texto inteiro é tokenizado de propósito para o chunking; o aviso
        # de "sequence length > max" do Transformers seria falso alarme aqui.
        logging.getLogger("transformers.tokenization_utils_base").setLevel(
            logging.ERROR
        )

        threads = calcular_threads_seguros(configuracao.threads)
        try:
            torch_modulo.set_num_threads(threads)
        except Exception:  # noqa: BLE001 - o backend pode não permitir o ajuste
            LOGGER.warning("Não foi possível fixar %d threads no torch.", threads)

        argumentos: dict[str, Any] = {}
        if diretorio_cache is not None:
            argumentos["cache_dir"] = str(Path(diretorio_cache).resolve())

        try:
            tokenizer = fabrica_tokenizer(configuracao.modelo_id, **argumentos)
            modelo = fabrica_modelo(configuracao.modelo_id, **argumentos)
        except Exception as erro:  # noqa: BLE001
            LOGGER.exception("Falha ao carregar o modelo de embeddings: %r", erro)
            raise ErroCarregamentoModeloError(
                f"Não foi possível carregar '{configuracao.modelo_id}': "
                f"{type(erro).__name__}: {erro!r}"
            ) from erro

        try:
            modelo.eval()
            modelo.to(configuracao.dispositivo)
        except Exception as erro:  # noqa: BLE001
            raise ErroCarregamentoModeloError(
                "Não foi possível preparar o modelo em "
                f"'{configuracao.dispositivo}': {type(erro).__name__}: {erro!r}"
            ) from erro

        dimensao = int(getattr(modelo.config, "hidden_size", 0))
        if dimensao <= 0:
            raise ModeloInvalidoError(
                f"O modelo '{configuracao.modelo_id}' não expôs hidden_size."
            )

        limite = cls._calcular_limite_tokens(configuracao, tokenizer, modelo)
        LOGGER.info(
            "Modelo %s carregado: dimensão %d, limite útil de %d tokens, %d threads.",
            configuracao.modelo_id,
            dimensao,
            limite,
            threads,
        )
        return cls(configuracao, tokenizer, modelo, torch_modulo, dimensao, limite)

    @staticmethod
    def _calcular_limite_tokens(
        configuracao: ConfiguracaoEmbeddings,
        tokenizer: Any,
        modelo: Any,
    ) -> int:
        candidatos = [configuracao.max_tokens_entrada]
        do_modelo = getattr(modelo.config, "max_position_embeddings", None)
        if isinstance(do_modelo, int) and do_modelo > 0:
            candidatos.append(do_modelo)
        do_tokenizer = getattr(tokenizer, "model_max_length", None)
        if isinstance(do_tokenizer, int) and 0 < do_tokenizer < 1_000_000:
            candidatos.append(do_tokenizer)
        limite = min(candidatos)

        especiais = getattr(tokenizer, "num_special_tokens_to_add", None)
        reserva = 2
        if callable(especiais):
            try:
                reserva = int(especiais(pair=False))
            except Exception:  # noqa: BLE001
                reserva = 2
        prefixo = configuracao.prefixo_passagem
        if prefixo:
            try:
                reserva += len(tokenizer.encode(prefixo, add_special_tokens=False))
            except Exception:  # noqa: BLE001
                reserva += 4
        return max(1, limite - reserva)

    # -------------------------------------------------------------- tokenizer

    def contar_tokens(self, texto: str) -> int:
        return len(self.tokenizar(texto))

    def tokenizar(self, texto: str) -> list[int]:
        """Tokens do texto puro, sem prefixo e sem tokens especiais."""
        return list(
            self.tokenizer.encode(
                texto,
                add_special_tokens=False,
                truncation=False,
            )
        )

    def destokenizar(self, tokens: Sequence[int]) -> str:
        return self.tokenizer.decode(list(tokens), skip_special_tokens=True)

    # ------------------------------------------------------------- inferência

    def codificar(self, textos: list[str]) -> list[list[float]]:
        """Codifica em lotes pequenos, liberando memória entre eles."""
        if not textos:
            return []

        vetores: list[list[float]] = []
        lote = self.configuracao.tamanho_lote
        with self._semaforo:
            for inicio in range(0, len(textos), lote):
                fatia = textos[inicio : inicio + lote]
                vetores.extend(self._codificar_lote(fatia))
                # Libera os tensores intermediários antes do próximo lote.
                gc.collect()
        return vetores

    def _codificar_lote(self, textos: Sequence[str]) -> list[list[float]]:
        prefixo = self.configuracao.prefixo_passagem
        if prefixo:
            entradas = [prefixo + texto for texto in textos]
        else:
            entradas = list(textos)
        torch = self._torch
        codificado = None
        saida = None
        try:
            with torch.inference_mode():
                codificado = self.tokenizer(
                    entradas,
                    padding=True,
                    truncation=True,
                    max_length=self.configuracao.max_tokens_entrada,
                    return_tensors="pt",
                )
                codificado = {
                    chave: valor.to(self.configuracao.dispositivo)
                    for chave, valor in codificado.items()
                }
                saida = self.modelo(**codificado)
                ocultos = saida.last_hidden_state
                mascara = codificado["attention_mask"].unsqueeze(-1).to(ocultos.dtype)
                somatorio = (ocultos * mascara).sum(dim=1)
                divisor = mascara.sum(dim=1).clamp(min=1e-9)
                media = somatorio / divisor
                if self.configuracao.normalizar:
                    media = torch.nn.functional.normalize(media, p=2, dim=1)
                resultado = media.detach().to("cpu").tolist()
        except Exception as erro:  # noqa: BLE001
            LOGGER.exception("Falha na inferência de embeddings: %r", erro)
            raise ErroInferenciaError(
                f"Falha ao codificar {len(textos)} texto(s): "
                f"{type(erro).__name__}: {erro!r}"
            ) from erro
        finally:
            del codificado
            del saida

        validar_vetores(resultado, self._dimensao)
        return resultado

    def testar(self) -> None:
        """Smoke test curto, executado uma única vez antes do lote."""
        vetores = self.codificar(["interação medicamentosa com varfarina"])
        if len(vetores) != 1:
            raise ErroInferenciaError("O smoke test não devolveu exatamente 1 vetor.")


def validar_vetores(vetores: Sequence[Sequence[float]], dimensao: int) -> None:
    """Recusa dimensão divergente, NaN, infinito e vetor totalmente nulo."""
    for posicao, vetor in enumerate(vetores):
        if len(vetor) != dimensao:
            raise VetorInvalidoError(
                f"O vetor {posicao} possui dimensão {len(vetor)}; "
                f"esperado {dimensao}."
            )
        for valor in vetor:
            if not isinstance(valor, (int, float)) or not math.isfinite(valor):
                raise VetorInvalidoError(
                    f"O vetor {posicao} contém valor inválido: {valor!r}."
                )
        if not any(valor != 0.0 for valor in vetor):
            raise VetorInvalidoError(f"O vetor {posicao} é totalmente nulo.")
