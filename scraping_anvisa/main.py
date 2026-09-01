import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from anvisa_scraper.controle import ControleColeta
from anvisa_scraper.erros import BloqueioAnvisaError
from anvisa_scraper.navegador import abrir_sessao_publica, criar_navegador
from anvisa_scraper.pipeline import processar_lote
from anvisa_scraper.planilha import ABA_PADRAO, carregar_medicamentos


BASE_PROJETO = Path(__file__).resolve().parent
PLANILHA_PADRAO = BASE_PROJETO / "dados" / "StatusBulasANVISA_formatada_2026-08-26.xlsx"


def criar_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Baixa bulas profissionais da Anvisa com Selenium."
    )
    parser.add_argument("--planilha", type=Path, default=PLANILHA_PADRAO)
    parser.add_argument("--aba", default=ABA_PADRAO)
    parser.add_argument(
        "--limite",
        type=int,
        default=5,
        help="Quantidade máxima de nomes únicos. O padrão é 5.",
    )
    parser.add_argument(
        "--inicio",
        type=int,
        default=0,
        help="Índice inicial, começando em zero.",
    )
    parser.add_argument(
        "--intervalo",
        type=float,
        default=5.0,
        help="Pausa entre medicamentos; mínimo aplicado: 3 segundos.",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--sem-pausa-inicial",
        action="store_true",
        help="Não pedir ENTER após abrir o Bulário.",
    )
    parser.add_argument(
        "--todos",
        action="store_true",
        help="Seleciona todos os nomes únicos; exige confirmação digitada.",
    )
    return parser.parse_args()


def main() -> int:
    args = criar_argumentos()
    medicamentos = carregar_medicamentos(args.planilha.resolve(), args.aba)
    total_linhas = sum(item.quantidade_registros_planilha for item in medicamentos)
    print(
        f"Planilha validada: {total_linhas} registros, "
        f"{len(medicamentos)} nomes únicos."
    )

    if args.inicio < 0 or args.inicio >= len(medicamentos):
        raise ValueError("--inicio está fora do intervalo da planilha.")
    if args.limite <= 0:
        raise ValueError("--limite deve ser maior que zero.")

    if args.todos:
        confirmacao = input(
            f"Você selecionou os {len(medicamentos)} nomes únicos. "
            "Digite PROCESSAR TODOS para confirmar: "
        )
        if confirmacao != "PROCESSAR TODOS":
            print("Execução cancelada.")
            return 2
        selecionados = medicamentos[args.inicio :]
    else:
        selecionados = medicamentos[args.inicio : args.inicio + args.limite]

    intervalo = max(3.0, args.intervalo)
    load_dotenv(BASE_PROJETO / ".env")
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print(
            "DATABASE_URL não configurada. Copie '.env.example' para '.env' "
            "e informe a conexão do PostgreSQL."
        )
        return 4

    try:
        controle = ControleColeta(dsn)
    except Exception as erro:
        print(f"Não foi possível conectar ao PostgreSQL: {type(erro).__name__}: {erro}")
        return 4

    navegador = None
    codigo_saida = 0

    try:
        navegador = criar_navegador(
            BASE_PROJETO / ".chrome-profile-anvisa",
            headless=args.headless,
        )
        abrir_sessao_publica(
            navegador,
            aguardar_confirmacao=not args.sem_pausa_inicial,
        )
        resumo = processar_lote(
            navegador=navegador,
            medicamentos=selecionados,
            controle=controle,
            pasta_pdfs=BASE_PROJETO / "pdfs",
            intervalo_segundos=intervalo,
        )
        print("\nResumo:")
        for chave, valor in resumo.items():
            print(f"- {chave}: {valor}")
    except BloqueioAnvisaError:
        codigo_saida = 3
        print("\nExecução encerrada por bloqueio ou limitação da Anvisa.")
    finally:
        controle.exportar_csv(BASE_PROJETO / "controle" / "resultado.csv")
        controle.fechar()
        if navegador is not None:
            navegador.quit()

    return codigo_saida


if __name__ == "__main__":
    raise SystemExit(main())
