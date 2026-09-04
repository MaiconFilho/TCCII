import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from extracao_interacoes.erros import ErroBancoError, ErroModeloError
from extracao_interacoes.modelo_llm import ModeloLLM
from extracao_interacoes.modelos import ConfiguracaoModelo
from extracao_interacoes.pipeline import processar_lote
from extracao_interacoes.relatorio import RelatorioCsv
from extracao_interacoes.repositorio import RepositorioInteracoes


BASE_PROJETO = Path(__file__).resolve().parent
MODELO_PADRAO = "Qwen/Qwen3-4B-Instruct-2507"


def inteiro_positivo(valor: str) -> int:
    convertido = int(valor)
    if convertido <= 0:
        raise argparse.ArgumentTypeError("o valor deve ser maior que zero")
    return convertido


def inteiro_nao_negativo(valor: str) -> int:
    convertido = int(valor)
    if convertido < 0:
        raise argparse.ArgumentTypeError("o valor não pode ser negativo")
    return convertido


def _inteiro_env(nome: str, padrao: int) -> int:
    valor = os.getenv(nome, str(padrao))
    try:
        return inteiro_positivo(valor)
    except (ValueError, argparse.ArgumentTypeError) as erro:
        raise ValueError(f"{nome} deve conter um inteiro maior que zero.") from erro


def criar_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extrai integralmente a seção de interações medicamentosas das "
            "bulas profissionais usando um modelo local."
        )
    )
    parser.add_argument("--inicio", type=inteiro_nao_negativo, default=0)
    parser.add_argument(
        "--limite",
        type=inteiro_positivo,
        default=1,
        help="Quantidade de PDFs; o padrão seguro é 1.",
    )
    parser.add_argument(
        "--todos",
        action="store_true",
        help="Processa todos os PDFs elegíveis a partir de --inicio.",
    )
    parser.add_argument(
        "--reprocessar",
        action="store_true",
        help="Inclui registros já existentes e os atualiza explicitamente.",
    )
    parser.add_argument(
        "--modelo",
        default=os.getenv("HF_MODEL_ID", MODELO_PADRAO),
    )
    parser.add_argument(
        "--max-input-tokens",
        type=inteiro_positivo,
        default=_inteiro_env("HF_MAX_INPUT_TOKENS", 262144),
    )
    parser.add_argument(
        "--max-new-tokens",
        type=inteiro_positivo,
        default=_inteiro_env("HF_MAX_NEW_TOKENS", 16384),
    )
    parser.add_argument(
        "--relatorio",
        type=Path,
        default=BASE_PROJETO / "relatorios" / "resultado.csv",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(BASE_PROJETO / ".env")
    args = criar_argumentos()
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print(
            "DATABASE_URL não configurada. Copie '.env.example' para '.env' "
            "e informe a conexão do PostgreSQL."
        )
        return 4

    configuracao = ConfiguracaoModelo(
        modelo_id=args.modelo,
        max_input_tokens=args.max_input_tokens,
        max_new_tokens=args.max_new_tokens,
        dispositivo=os.getenv("HF_DEVICE", "auto").strip() or "auto",
    )
    limite = None if args.todos else args.limite

    try:
        repositorio = RepositorioInteracoes(dsn)
    except ErroBancoError as erro:
        print(f"Não foi possível preparar o PostgreSQL: {erro}")
        return 4

    try:
        bulas = repositorio.selecionar_bulas(
            inicio=args.inicio,
            limite=limite,
            reprocessar=args.reprocessar,
        )
        if not bulas:
            print("Nenhuma bula elegível foi encontrada para os parâmetros informados.")
            return 0

        print(
            f"Carregando o modelo local '{configuracao.modelo_id}' uma única vez "
            f"com HF_DEVICE={configuracao.dispositivo}..."
        )
        try:
            modelo = ModeloLLM.carregar(configuracao)
        except ErroModeloError as erro:
            print(f"Não foi possível carregar o modelo: {erro}")
            return 5

        with RelatorioCsv(args.relatorio) as relatorio:
            resumo = processar_lote(
                bulas=bulas,
                modelo=modelo,
                repositorio=repositorio,
                relatorio=relatorio,
                reprocessar=args.reprocessar,
            )
        print("\nResumo:")
        for status, quantidade in resumo.items():
            print(f"- {status}: {quantidade}")
        print(f"Relatório: {args.relatorio.resolve()}")
        status_erro = {
            "PDF_SEM_TEXTO",
            "PDF_INVALIDO",
            "CONTEXTO_EXCEDIDO",
            "RESPOSTA_INVALIDA",
            "RESPOSTA_TRUNCADA",
            "ERRO_MODELO",
            "ERRO_BANCO",
        }
        return 1 if status_erro.intersection(resumo) else 0
    finally:
        repositorio.fechar()


if __name__ == "__main__":
    raise SystemExit(main())
