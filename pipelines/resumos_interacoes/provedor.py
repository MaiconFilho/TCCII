"""Worker local isolado: timeout real encerra a inferência e libera o GGUF."""

import logging
import multiprocessing as mp
import time
import traceback
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import psutil

LOGGER = logging.getLogger(__name__)


def worker(canal, config, cache):
    try:
        from huggingface_hub import hf_hub_download
        from pipelines.identificacao_interacoes.provedor import configuracao_llm
        from pipelines.extracao_interacoes.modelo_llm import LlamaCppProvider
        inicio = time.perf_counter()
        args = dict(repo_id=config.repositorio_modelo, filename=config.arquivo_modelo,
                    cache_dir=str(cache))
        try:
            caminho = hf_hub_download(**args, local_files_only=True)
        except Exception:
            caminho = hf_hub_download(**args)
        canal.send(("caminho", caminho))
        localizar_segundos = time.perf_counter() - inicio
        inicio = time.perf_counter()
        provedor = LlamaCppProvider.carregar(configuracao_llm(config),
                                            downloader=lambda **kw: caminho)
        canal.send(("ok", {"caminho": caminho, "tempo_carga": time.perf_counter() - inicio,
                           "tempo_localizacao": localizar_segundos}))
        while True:
            pedido = canal.recv()
            if pedido is None:
                break
            try:
                if pedido["op"] == "tokens":
                    valor = provedor.contar_tokens(pedido["texto"])
                else:
                    provedor.configuracao = replace(provedor.configuracao,
                                                   max_tokens_saida=pedido["max_tokens"])
                    valor = asdict(provedor.analisar(pedido["mensagens"], esquema=pedido["esquema"]))
                canal.send(("ok", valor))
            except Exception:
                canal.send(("erro", traceback.format_exc()))
    except BaseException:
        try:
            canal.send(("erro", traceback.format_exc()))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        canal.close()


class ProvedorIsolado:
    def __init__(self, config, cache: Path, *, timeout=90, timeout_carga=600,
                 informar=print, alvo_worker=worker):
        self.config = config
        self.cache = cache
        self.timeout = timeout
        self.timeout_carga = timeout_carga
        self.informar = informar
        self.alvo_worker = alvo_worker
        self.processo = None
        self.canal = None
        self.caminho = ""
        self.tempo_carga = 0.0
        self.tempo_localizacao = 0.0
        self.pico_memoria_mb = 0.0

    def iniciar(self):
        if self.processo is not None and self.processo.is_alive():
            return
        self.fechar()
        contexto = mp.get_context("spawn")
        self.canal, filho = contexto.Pipe()
        self.processo = contexto.Process(target=self.alvo_worker, args=(filho, self.config, self.cache), daemon=True)
        self.informar(f"Carregando modelo {self.config.arquivo_modelo} (CPU)...")
        self.processo.start()
        filho.close()
        valor = self._receber(self.timeout_carga, "carregamento")
        self.caminho = valor["caminho"]
        self.tempo_carga += valor["tempo_carga"]
        self.tempo_localizacao += valor.get("tempo_localizacao", 0.0)
        self.informar(f"Modelo carregado em {valor['tempo_carga']:.1f}s; caminho: {self.caminho}")

    def _receber(self, timeout, etapa):
        inicio = ultimo = time.monotonic()
        while True:
            decorrido = time.monotonic() - inicio
            if decorrido >= timeout:
                self.fechar()
                raise TimeoutError(f"Timeout de {timeout}s na etapa {etapa}; worker encerrado.")
            try:
                memoria = psutil.Process(self.processo.pid).memory_info()
                self.pico_memoria_mb = max(self.pico_memoria_mb, memoria.rss / 1024**2,
                                          getattr(memoria, "peak_wset", 0) / 1024**2)
                if self.config.limite_memoria_mb and self.pico_memoria_mb > self.config.limite_memoria_mb:
                    self.fechar()
                    raise MemoryError("Worker excedeu o limite configurado de memória.")
            except psutil.Error:
                pass
            if self.canal.poll(min(0.2, timeout - decorrido)):
                try:
                    status, valor = self.canal.recv()
                except EOFError as erro:
                    self.fechar()
                    raise RuntimeError("Worker LLM encerrou sem resposta.") from erro
                if status == "caminho":
                    self.informar(f"GGUF: {valor}")
                    continue
                if status == "erro":
                    LOGGER.error("Traceback do worker:\n%s", valor)
                    raise RuntimeError(valor)
                return valor
            if not self.processo.is_alive():
                codigo = self.processo.exitcode
                self.fechar()
                raise RuntimeError(f"Worker LLM encerrou com código {codigo}.")
            if time.monotonic() - ultimo >= 10:
                self.informar(f"  {etapa}: {decorrido:.0f}s; RAM aproximada {self.pico_memoria_mb:.0f} MB...")
                ultimo = time.monotonic()

    def contar_tokens(self, texto):
        self.iniciar()
        self.canal.send({"op": "tokens", "texto": texto})
        return self._receber(self.timeout, "tokenização")

    def analisar(self, mensagens, *, esquema, max_tokens=192):
        self.iniciar()
        self.canal.send({"op": "gerar", "mensagens": mensagens, "esquema": esquema, "max_tokens": max_tokens})
        return SimpleNamespace(**self._receber(self.timeout, "inferência"))

    def fechar(self):
        if self.processo is not None:
            if self.processo.is_alive():
                self.processo.terminate()
            self.processo.join(timeout=3)
            if self.processo.is_alive():
                self.processo.kill()
                self.processo.join(timeout=3)
            self.processo.close()
            self.processo = None
        if self.canal is not None:
            self.canal.close()
            self.canal = None
