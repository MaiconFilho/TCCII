"""Benchmark somente leitura, com respostas preservadas para conferência."""

import logging
from collections import Counter
from time import perf_counter

from .servico import CardsDoMesmoTrecho

LOGGER = logging.getLogger(__name__)


def executar(repo, nome, gerador, limite=5, alvo=None):
    inicio = perf_counter()
    fonte = repo.fonte(nome)
    busca_segundos = perf_counter() - inicio
    if not fonte:
        raise ValueError("Medicamento sem identificação vigente.")
    # Prioriza evidências distintas para não medir só a lista repetida da Marevan.
    unicas = {}
    interacoes = [i for i in fonte["interacoes"] if alvo is None or i["alvo"].casefold() == alvo.casefold()]
    if not interacoes:
        raise ValueError("Nenhuma interação corresponde ao alvo do benchmark.")
    for item in interacoes:
        unicas.setdefault(item["evidencia"], item)
    amostra = list(unicas.values())
    amostra += [i for i in interacoes if i not in amostra]
    resultados = []
    # Como no processamento: literal, reaproveitado do mesmo trecho e efeito, ou LLM.
    cards_bula = CardsDoMesmoTrecho(fonte["interacoes"], reaproveitar_gravados=False)
    for indice, item in enumerate(amostra[:limite], 1):
        print(f"Benchmark [{indice}/{min(limite, len(amostra))}] {item['alvo']} — id {item['id']}", flush=True)
        chamada = perf_counter()
        linha = {"id": item["id"], "alvo": item["alvo"], "evidencia": item["evidencia"], "resumo": None,
                 "erro": None, "origem": "llm"}
        try:
            linha["resumo"], linha["origem"] = cards_bula.card(item, gerador, nome)
        except Exception as erro:
            LOGGER.exception("Benchmark: falha no alvo %s", item["alvo"])
            linha["erro"] = f"{type(erro).__name__}: {erro}"
        linha["segundos"] = round(perf_counter() - chamada, 3)
        resultados.append(linha)
        marca = "" if linha["origem"] == "llm" else f" [{linha['origem']}]"
        print(f"  {'ERRO: ' + linha['erro'] if linha['erro'] else linha['resumo']} ({linha['segundos']:.1f}s){marca}",
              flush=True)
    # O geral usa os cards da amostra, em memória: mede o prompt longo sem gravar.
    cards = [{"alvo": r["alvo"], "resumo": r["resumo"]} for r in resultados if r["resumo"]]
    geral = {"texto": None, "erro": None, "segundos": 0.0, "cards_usados": len(cards)}
    if cards:
        print(f"Benchmark: resumo geral a partir de {len(cards)} cards (sem gravação)...", flush=True)
        chamada = perf_counter()
        try:
            geral["texto"] = gerador.gerar(nome, cards, geral=True)
        except Exception as erro:
            LOGGER.exception("Benchmark: falha no resumo geral")
            geral["erro"] = f"{type(erro).__name__}: {erro}"
        geral["segundos"] = round(perf_counter() - chamada, 3)
        print(f"  {'ERRO: ' + geral['erro'] if geral['erro'] else geral['texto']} ({geral['segundos']:.1f}s)", flush=True)
    gerados = [r for r in resultados if r["origem"] == "llm"]
    return {"medicamento": nome, "modelo": gerador.config.arquivo_modelo,
            "versao_prompt": gerador.versao_prompt,
            "busca_segundos": busca_segundos, "quantidade_interacoes": len(fonte["interacoes"]),
            "chamadas_llm": gerador.chamadas,
            "tokens_card": gerador.tokens_card, "resultados": resultados,
            "falhas": sum(r["erro"] is not None for r in resultados),
            "origens": dict(Counter(r["origem"] for r in resultados if not r["erro"])),
            # Média só dos cards escritos pelo LLM; literais e reaproveitados levam ~0 s.
            "media_segundos": sum(r["segundos"] for r in gerados) / max(1, len(gerados)),
            "geral": geral, "tempo_total": perf_counter() - inicio, "gravacao": False}
