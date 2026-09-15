from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .erros import ErroCarregamentoModeloError, ErroInferenciaError
from .modelos import ConfiguracaoLLM, RespostaLLM
from .provedor_llm import Mensagem


LOGGER = logging.getLogger(__name__)


def calcular_threads_seguros(configurado: int, cpus: int | None = None) -> int:
    if configurado > 0:
        return configurado
    disponiveis = cpus if cpus is not None else (os.cpu_count() or 2)
    return max(1, min(4, disponiveis // 2 or 1))


class LlamaCppProvider:
    """Provedor CPU GGUF com uma única instância do modelo e inferência serial."""

    def __init__(
        self,
        configuracao: ConfiguracaoLLM,
        modelo: Any,
    ) -> None:
        self.configuracao = configuracao
        self.modelo = modelo
        self._semaforo = threading.BoundedSemaphore(configuracao.concorrencia)

    @classmethod
    def carregar(
        cls,
        configuracao: ConfiguracaoLLM,
        diretorio_cache: Path | None = None,
        downloader: Callable[..., str] | None = None,
        fabrica_modelo: Callable[..., Any] | None = None,
    ) -> "LlamaCppProvider":
        try:
            configuracao.validar()
        except ValueError as erro:
            raise ErroCarregamentoModeloError(str(erro)) from erro

        if downloader is None:
            try:
                from huggingface_hub import hf_hub_download
            except ImportError as erro:
                raise ErroCarregamentoModeloError(
                    "huggingface-hub não está instalado. Instale requirements.txt."
                ) from erro
            downloader = hf_hub_download

        if fabrica_modelo is None:
            try:
                from llama_cpp import Llama
            except ImportError as erro:
                raise ErroCarregamentoModeloError(
                    "llama-cpp-python não está instalado. Instale requirements.txt."
                ) from erro
            fabrica_modelo = Llama

        try:
            argumentos_download: dict[str, Any] = {
                "repo_id": configuracao.repositorio_modelo,
                "filename": configuracao.arquivo_modelo,
            }
            if diretorio_cache is not None:
                argumentos_download["cache_dir"] = str(diretorio_cache.resolve())
            caminho_modelo = downloader(**argumentos_download)
        except Exception as erro:
            LOGGER.exception("Falha no download/localização do GGUF: %r", erro)
            raise ErroCarregamentoModeloError(
                "Não foi possível obter o GGUF "
                f"'{configuracao.arquivo_modelo}': "
                f"{type(erro).__name__}: {erro!r}"
            ) from erro

        try:
            threads = calcular_threads_seguros(configuracao.threads)
            modelo = fabrica_modelo(
                model_path=str(caminho_modelo),
                n_ctx=configuracao.tamanho_contexto,
                n_threads=threads,
                n_threads_batch=threads,
                n_batch=configuracao.tamanho_lote,
                n_gpu_layers=configuracao.camadas_gpu,
                use_mmap=True,
                use_mlock=False,
                verbose=False,
            )
        except Exception as erro:
            LOGGER.exception("Falha ao carregar o modelo GGUF: %r", erro)
            raise ErroCarregamentoModeloError(
                "Falha ao carregar o GGUF local: "
                f"{type(erro).__name__}: {erro!r}"
            ) from erro

        return cls(configuracao, modelo)

    def contar_tokens(self, texto: str) -> int:
        try:
            return len(self.modelo.tokenize(texto.encode("utf-8"), add_bos=False))
        except Exception as erro:
            LOGGER.exception("Falha ao tokenizar texto: %r", erro)
            raise ErroInferenciaError(
                f"Falha ao tokenizar: {type(erro).__name__}: {erro!r}"
            ) from erro

    def analisar(
        self, mensagens: Sequence[Mensagem], *, esquema: dict | None = None
    ) -> RespostaLLM:
        try:
            formato: dict[str, Any] = {"type": "json_object"}
            if esquema is not None:
                formato["schema"] = esquema
            # A confirmacao precisa apenas de {"confirmado": true/false}.
            limite_saida = self.configuracao.max_tokens_saida
            if esquema and set(esquema.get("properties", {})) == {"confirmado"}:
                limite_saida = min(limite_saida, 32)
            with self._semaforo:
                resposta = self.modelo.create_chat_completion(
                    messages=list(mensagens),
                    temperature=self.configuracao.temperatura,
                    max_tokens=limite_saida,
                    response_format=formato,
                )
            escolha = resposta["choices"][0]
            conteudo = (escolha["message"]["content"] or "").strip()
            uso = resposta.get("usage") or {}
            return RespostaLLM(
                conteudo=conteudo,
                tokens_entrada=int(uso.get("prompt_tokens", 0)),
                tokens_saida=int(uso.get("completion_tokens", 0)),
                truncada=escolha.get("finish_reason") == "length",
            )
        except Exception as erro:
            LOGGER.exception("Falha durante a inferência llama.cpp: %r", erro)
            raise ErroInferenciaError(
                "Falha durante a inferência local: "
                f"{type(erro).__name__}: {erro!r}"
            ) from erro

    def testar(self) -> None:
        resposta = self.analisar(
            [
                {
                    "role": "system",
                    "content": "Responda somente com JSON válido e mínimo.",
                },
                {
                    "role": "user",
                    "content": 'Responda exatamente com {"ok": true}.',
                },
            ]
        )
        try:
            dados = json.loads(resposta.conteudo)
        except json.JSONDecodeError as erro:
            LOGGER.exception("Smoke test retornou JSON inválido: %r", erro)
            raise ErroInferenciaError(
                "Smoke test retornou JSON inválido: "
                f"{type(erro).__name__}: {erro!r}"
            ) from erro
        if resposta.truncada or dados.get("ok") is not True:
            raise ErroInferenciaError(
                f"Smoke test do modelo falhou: {resposta.conteudo!r}"
            )


# Nome antigo mantido para não quebrar imports de integrações existentes.
ModeloLLM = LlamaCppProvider
