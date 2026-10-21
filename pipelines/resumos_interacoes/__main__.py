"""CLI offline. Sem --todos, o padrão é apenas um medicamento pendente."""

import argparse
import csv
import logging
import json
import os
from collections import Counter
from datetime import datetime
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from pipelines.identificacao_interacoes.main_identificacao import (
    configuracao_do_ambiente, diretorio_modelos, inteiro_positivo,
)
from .provedor import ProvedorIsolado
from .repositorio import RepositorioResumos
from .servico import GeradorResumos, processar

BASE = Path(__file__).resolve().parent


def data_hora(valor: str) -> datetime:
    try:
        return datetime.fromisoformat(valor)
    except ValueError as erro:
        raise argparse.ArgumentTypeError('use o formato "AAAA-MM-DD HH:MM", no horário local') from erro


def argumentos(argv=None):
    parser = argparse.ArgumentParser(description="Gera resumos offline. Piloto padrão: um medicamento pendente.")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--nome-normalizado")
    grupo.add_argument("--limite", type=inteiro_positivo)
    grupo.add_argument("--todos", action="store_true")
    parser.add_argument("--reprocessar", action="store_true")
    parser.add_argument("--corrigir-inconsistentes", action="store_true",
                        help="Refaz cards com contexto incompleto ou ausência sem suporte e gerais que perderam o foco no medicamento pesquisado.")
    parser.add_argument("--listar", action="store_true", help="Lista a seleção sem carregar modelo nem gravar.")
    parser.add_argument("--refazer-antes", type=data_hora, metavar='"AAAA-MM-DD HH:MM"',
                        help="Também refaz bulas cujo resumo foi gravado antes desta data/hora "
                             "(versão anterior do código). Retomável: as refeitas ganham data nova.")
    parser.add_argument("--limite-interacoes", type=inteiro_positivo)
    parser.add_argument("--benchmark-modelo", action="store_true")
    parser.add_argument("--alvo-benchmark", help="Restringe o benchmark a um alvo textual, sem gravação.")
    parser.add_argument("--tokens-card", type=inteiro_positivo, default=192)
    parser.add_argument("--tokens-geral", type=inteiro_positivo, default=320)
    parser.add_argument("--timeout", type=inteiro_positivo, default=90)
    parser.add_argument("--modelo-repo")
    parser.add_argument("--modelo-arquivo")
    parser.add_argument("--contexto", type=inteiro_positivo, default=4096)
    args = parser.parse_args(argv)
    if args.benchmark_modelo and not args.nome_normalizado:
        parser.error("Benchmark exige --nome-normalizado e não grava no banco.")
    if args.alvo_benchmark and not args.benchmark_modelo:
        parser.error("--alvo-benchmark exige --benchmark-modelo.")
    if args.corrigir_inconsistentes and (args.reprocessar or args.refazer_antes or args.benchmark_modelo):
        parser.error("--corrigir-inconsistentes não combina com --reprocessar, --refazer-antes ou --benchmark-modelo.")
    return args


def main(argv=None):
    args = argumentos(argv)
    load_dotenv(BASE / ".env")
    load_dotenv(BASE.parent / "identificacao_interacoes" / ".env")
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("Configure DATABASE_URL em pipelines/identificacao_interacoes/.env ou resumos_interacoes/.env.")
        return 4
    (BASE / "logs").mkdir(exist_ok=True)
    (BASE / "relatorios").mkdir(exist_ok=True)
    logging.basicConfig(filename=BASE / "logs" / "resumos_interacoes.log",
                        encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Argumentos da execução: %s", vars(args))
    repo = None
    provedor = None
    inicio = perf_counter()
    try:
        config = configuracao_do_ambiente()
        config = replace(config, tamanho_contexto=args.contexto, max_tokens_saida=args.tokens_geral,
                         temperatura=0.0, camadas_gpu=0,
                         repositorio_modelo=args.modelo_repo or config.repositorio_modelo,
                         arquivo_modelo=args.modelo_arquivo or config.arquivo_modelo)
        config.validar()
        repo = RepositorioResumos(dsn)
        busca = perf_counter()
        nomes = [args.nome_normalizado] if args.benchmark_modelo else repo.selecionar(args.nome_normalizado, None if args.todos else args.limite or 1,
                               reprocessar=args.reprocessar, antes=args.refazer_antes,
                               corrigir_inconsistentes=args.corrigir_inconsistentes)
        print(f"Seleção no banco: {perf_counter() - busca:.3f}s", flush=True)
        if not nomes:
            print("Nenhuma bula com interações próprias e resumo pendente para esta seleção.")
            return 0
        if args.listar:
            from .repositorio import necessita_correcao, necessita_correcao_geral
            for nome in nomes:
                fonte = repo.fonte(nome)
                quantidade = sum(necessita_correcao(i) for i in fonte["interacoes"]) if fonte else 0
                geral_inconsistente = necessita_correcao_geral(fonte) if fonte else False
                print(f"{nome}: {quantidade} cards com inconsistência detectada; "
                      f"geral fora de contexto: {'sim' if geral_inconsistente else 'não'}", flush=True)
            print(f"Total: {len(nomes)} bulas. Somente leitura; nenhum modelo carregado.")
            return 0
        print(f"Selecionadas {len(nomes)} bulas. Modo: {'BENCHMARK SEM GRAVAÇÃO' if args.benchmark_modelo else 'GRAVAÇÃO POR CARD'}.", flush=True)
        provedor = ProvedorIsolado(config, diretorio_modelos(), timeout=args.timeout,
                                  informar=lambda texto: print(texto, flush=True))
        provedor.iniciar()
        gerador = GeradorResumos(provedor, config, tokens_card=args.tokens_card, tokens_geral=args.tokens_geral)
        if args.benchmark_modelo:
            from .benchmark import executar
            resultado = executar(repo, args.nome_normalizado, gerador, args.limite_interacoes or 5,
                                 alvo=args.alvo_benchmark)
            resultado.update(caminho_modelo=provedor.caminho, tempo_carregamento=provedor.tempo_carga,
                             tempo_localizacao_download=provedor.tempo_localizacao,
                             pico_memoria_mb=provedor.pico_memoria_mb,
                             tempo_com_carga=perf_counter() - inicio)
            arquivo = BASE / "relatorios" / f"benchmark_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
            arquivo.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
            geral = resultado["geral"]
            print(f"Benchmark: {resultado['falhas']} falhas em cards; origens {resultado['origens']}; "
                  f"média por card escrito pelo LLM {resultado['media_segundos']:.1f}s; "
                  f"geral {'ERRO' if geral['erro'] else 'ok'} em {geral['segundos']:.1f}s; "
                  f"{resultado['chamadas_llm']} chamadas; carga {provedor.tempo_carga:.1f}s; "
                  f"pico RAM {provedor.pico_memoria_mb:.0f} MB; relatório: {arquivo}", flush=True)
            return 2 if resultado["falhas"] or geral["erro"] else 0
        arquivo = BASE / "relatorios" / f"resumos_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
        contagem = Counter()
        with arquivo.open("w", encoding="utf-8-sig", newline="") as saida:
            escritor = None
            for indice, nome in enumerate(nomes, 1):
                inicio_item = perf_counter()
                try:
                    refazer = args.refazer_antes is not None and repo.gerado_antes(nome, args.refazer_antes)
                    print(f"[{indice}/{len(nomes)}] {nome} — "
                          f"{'refazendo resumos da versão anterior' if refazer else 'gerando resumos'}...", flush=True)
                    resultado = processar(repo, nome, gerador, reprocessar=args.reprocessar or refazer,
                                          limite_interacoes=args.limite_interacoes,
                                          corrigir_inconsistentes=args.corrigir_inconsistentes)
                except Exception as erro:
                    logging.exception("Falha ao processar %s", nome)
                    resultado = {"nome_normalizado": nome, "status": "ERRO_BANCO",
                                 "quantidade_interacoes": 0, "chamadas_llm": gerador.chamadas,
                                 "tempo_inferencia_segundos": round(gerador.tempo_inferencia, 3),
                                 "tempo_total_segundos": round(perf_counter() - inicio_item, 3),
                                 "erro": f"{type(erro).__name__}: {erro}", "gravacao_parcial": False}
                if escritor is None:
                    escritor = csv.DictWriter(saida, fieldnames=list(resultado))
                    escritor.writeheader()
                escritor.writerow(resultado)
                saida.flush()
                # Uma linha por card, acrescentada a cada bula: reescrever um JSON com
                # todos os cards a cada bula ficaria quadrático na base inteira (66 mil cards).
                with arquivo.with_suffix(".jsonl").open("a", encoding="utf-8") as auditoria:
                    for registro in gerador.registros:
                        auditoria.write(json.dumps({"modelo": config.arquivo_modelo, **registro},
                                                   ensure_ascii=False) + "\n")
                gerador.registros.clear()
                logging.info("Resultado: %s", resultado)
                contagem[resultado["status"]] += 1
                print(f"  {resultado['status']} — {resultado['tempo_total_segundos']:.1f}s; {resultado['erro']}", flush=True)
        print(f"Resumo: {dict(contagem)}\nTempo incluindo carga do modelo: {perf_counter() - inicio:.1f}s\nRelatório: {arquivo}")
        return 2 if contagem["FALHA"] or contagem["ERRO_BANCO"] else 0
    except Exception as erro:
        logging.exception("Falha na etapa de resumos")
        print(f"Não foi possível continuar: {type(erro).__name__}: {erro}")
        return 4
    finally:
        if provedor:
            provedor.fechar()
        if repo:
            repo.fechar()


if __name__ == "__main__":
    raise SystemExit(main())
