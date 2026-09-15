from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Protocol

from .erros import ErroBancoError
from .modelos import (
    BulaParaExtracao,
    MetodoExtracao,
    ResultadoExtracao,
    StatusExtracao,
)
from .servico import ServicoExtracaoInteracoes


LOGGER = logging.getLogger(__name__)


class RepositorioProtocolo(Protocol):
    def gravar_resultado(
        self,
        bula: BulaParaExtracao,
        trecho_interacoes: str | None,
        reprocessar: bool = False,
        *,
        status_extracao: str | None = None,
        detalhe_revisao: str | None = None,
        tempo_leitura_segundos: float | None = None,
        tempo_inferencia_segundos: float | None = None,
        tempo_total_segundos: float | None = None,
    ) -> None: ...


class RelatorioProtocolo(Protocol):
    def registrar(self, dados: dict) -> None: ...


def _linha_relatorio(
    bula: BulaParaExtracao,
    resultado: ResultadoExtracao,
    tempo_total: float,
) -> dict:
    return {
        "nome_normalizado": bula.nome_normalizado,
        "numero_registro": bula.numero_registro,
        "expediente": bula.expediente or "",
        "arquivo_pdf": str(bula.caminho_pdf),
        "status": resultado.status.value,
        "metodo_extracao": resultado.metodo.value if resultado.metodo else "",
        "titulo_encontrado": resultado.titulo_encontrado,
        "linha_inicio": resultado.linha_inicio,
        "linha_fim_exclusiva": resultado.linha_fim_exclusiva,
        "quantidade_paginas": resultado.quantidade_paginas,
        "paginas_ocr": ";".join(map(str, resultado.paginas_ocr)),
        "tempo_ocr_segundos": round(resultado.tempo_ocr_segundos, 3),
        "acertos_cache_ocr": resultado.acertos_cache_ocr,
        "quantidade_janelas": resultado.quantidade_janelas,
        "quantidade_chamadas_llm": resultado.quantidade_chamadas_llm,
        "tokens_entrada_total": resultado.tokens_entrada_total,
        "tokens_saida_total": resultado.tokens_saida_total,
        "memoria_antes_mb": round(resultado.memoria_antes_mb, 1),
        "pico_memoria_mb": round(resultado.pico_memoria_mb, 1),
        "memoria_depois_mb": round(resultado.memoria_depois_mb, 1),
        "tempo_leitura_segundos": round(resultado.tempo_leitura_segundos, 3),
        "tempo_inferencia_segundos": round(
            resultado.tempo_inferencia_segundos, 3
        ),
        "tempo_total_segundos": round(tempo_total, 3),
        "detalhe_erro": resultado.detalhe_erro,
    }


def processar_lote(
    bulas: list[BulaParaExtracao],
    servico: ServicoExtracaoInteracoes,
    repositorio: RepositorioProtocolo,
    relatorio: RelatorioProtocolo,
    reprocessar: bool = False,
) -> dict[str, int]:
    contagens: Counter[str] = Counter()
    metodos: Counter[str] = Counter()

    for indice, bula in enumerate(bulas, start=1):
        prefixo = f"[{indice}/{len(bulas)}] {bula.nome_normalizado}"
        print(f"{prefixo} — analisando PDF...")
        inicio_total = time.perf_counter()
        resultado = servico.extrair(
            nome_normalizado=bula.nome_normalizado,
            numero_registro=bula.numero_registro,
            expediente=bula.expediente,
            caminho_pdf=bula.caminho_pdf,
        )

        # Mede a extracao por PDF, incluindo leitura, LLM e validacao.
        # Exclui carregamento do modelo, gravacao no banco e escrita do CSV.
        tempo_total = time.perf_counter() - inicio_total
        if resultado.status in {
            StatusExtracao.CONCLUIDO,
            StatusExtracao.SEM_SECAO_INTERACOES,
            StatusExtracao.REVISAO_MANUAL,
        }:
            try:
                # Pendencias sao identificadas, nunca promovidas a conclusao.
                # O repositorio protege resultados anteriores ja concluidos.
                repositorio.gravar_resultado(
                    bula,
                    resultado.trecho_interacoes,
                    reprocessar=reprocessar,
                    status_extracao=resultado.status.value,
                    detalhe_revisao=resultado.detalhe_erro or None,
                    tempo_leitura_segundos=resultado.tempo_leitura_segundos,
                    tempo_inferencia_segundos=resultado.tempo_inferencia_segundos,
                    tempo_total_segundos=tempo_total,
                )
            except ErroBancoError as erro:
                LOGGER.exception(
                    "Falha de banco para %s: %r", bula.nome_normalizado, erro
                )
                resultado.status = StatusExtracao.ERRO_BANCO
                resultado.detalhe_erro = repr(erro)
            except Exception as erro:
                LOGGER.exception(
                    "Falha inesperada de banco para %s: %r",
                    bula.nome_normalizado,
                    erro,
                )
                resultado.status = StatusExtracao.ERRO_BANCO
                resultado.detalhe_erro = repr(erro)

        relatorio.registrar(_linha_relatorio(bula, resultado, tempo_total))
        contagens[resultado.status.value] += 1
        if resultado.metodo is not None:
            metodos[resultado.metodo.value] += 1
        if resultado.status == StatusExtracao.CONCLUIDO:
            print(f"{prefixo} — concluído ({resultado.metodo.value}).")
        else:
            print(f"{prefixo} — {resultado.status.value}.")

    concluidos = contagens[StatusExtracao.CONCLUIDO.value]
    fallbacks = metodos[MetodoExtracao.FALLBACK_ESTRUTURAL.value]
    if concluidos and fallbacks / concluidos > 0.20:
        aviso = (
            "QUALIDADE: FALLBACK_ESTRUTURAL superou 20% dos resultados "
            "concluídos; revise prompts, modelo e amostras."
        )
        LOGGER.warning(aviso)
        print(aviso)
    return dict(sorted(contagens.items()))
