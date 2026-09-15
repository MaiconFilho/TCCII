from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


BASE_PROJETO = Path(__file__).resolve().parent
if str(BASE_PROJETO.parent) not in sys.path:
    # Permite executar `python main.py` sem criar outra pasta de mesmo nome.
    sys.path.insert(0, str(BASE_PROJETO.parent))

from dotenv import load_dotenv

from extracao_interacoes.erros import (
    ErroBancoError,
    ErroCarregamentoModeloError,
    ErroInferenciaError,
)
from extracao_interacoes.modelo_llm import LlamaCppProvider
from extracao_interacoes.modelos import (
    BulaParaExtracao,
    ConfiguracaoLLM,
    ResultadoExtracao,
    StatusExtracao,
)
from extracao_interacoes.pipeline import _linha_relatorio, processar_lote
from extracao_interacoes.relatorio import RelatorioCsv
from extracao_interacoes.repositorio import RepositorioInteracoes
from extracao_interacoes.servico import ServicoExtracaoInteracoes


REPOSITORIO_PADRAO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
ARQUIVO_PADRAO = "qwen2.5-1.5b-instruct-q4_k_m.gguf"


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


def _inteiro_env(nome: str, padrao: int, permitir_zero: bool = False) -> int:
    valor = os.getenv(nome, str(padrao))
    try:
        return (
            inteiro_nao_negativo(valor)
            if permitir_zero
            else inteiro_positivo(valor)
        )
    except (ValueError, argparse.ArgumentTypeError) as erro:
        regra = "não negativo" if permitir_zero else "maior que zero"
        raise ValueError(f"{nome} deve conter um inteiro {regra}.") from erro


def _float_env(nome: str, padrao: float) -> float:
    try:
        valor = float(os.getenv(nome, str(padrao)))
    except ValueError as erro:
        raise ValueError(f"{nome} deve conter um número.") from erro
    if valor < 0:
        raise ValueError(f"{nome} não pode ser negativo.")
    return valor


def criar_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extrai a seção de interações medicamentosas com uma LLM GGUF local."
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
        help="Atualiza um registro somente após uma nova extração válida.",
    )
    parser.add_argument(
        "--nome-normalizado",
        help="Seleciona exatamente um medicamento pelo nome normalizado.",
    )
    parser.add_argument(
        "--relatorio",
        type=Path,
        default=BASE_PROJETO / "relatorios" / "resultado.csv",
    )
    return parser.parse_args()


def configuracao_do_ambiente() -> ConfiguracaoLLM:
    return ConfiguracaoLLM(
        backend=os.getenv("LLM_BACKEND", "llama_cpp").strip(),
        modo_extracao=os.getenv("EXTRACAO_MODO", "rapido").strip().lower(),
        repositorio_modelo=os.getenv(
            "HF_MODEL_REPO", REPOSITORIO_PADRAO
        ).strip(),
        arquivo_modelo=os.getenv("HF_MODEL_FILE", ARQUIVO_PADRAO).strip(),
        tamanho_contexto=_inteiro_env("LLM_CONTEXT_SIZE", 4096),
        tokens_janela=_inteiro_env("LLM_CHUNK_TOKENS", 700),
        sobreposicao_tokens=_inteiro_env(
            "LLM_CHUNK_OVERLAP", 100, permitir_zero=True
        ),
        max_tokens_saida=_inteiro_env("LLM_MAX_OUTPUT_TOKENS", 256),
        threads=_inteiro_env("LLM_THREADS", 0, permitir_zero=True),
        tamanho_lote=_inteiro_env("LLM_BATCH_SIZE", 128),
        camadas_gpu=_inteiro_env("LLM_GPU_LAYERS", 0, permitir_zero=True),
        temperatura=_float_env("LLM_TEMPERATURE", 0),
        limite_memoria_mb=_inteiro_env(
            "LLM_MAX_PROCESS_MEMORY_MB", 5500, permitir_zero=True
        ),
        concorrencia=_inteiro_env("LLM_INFERENCE_CONCURRENCY", 1),
    )


def configurar_log() -> Path:
    caminho = BASE_PROJETO / "logs" / "extracao_interacoes.log"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=caminho,
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return caminho


def registrar_falha_inicial(
    bulas: list[BulaParaExtracao],
    relatorio: Path,
    status: StatusExtracao,
    erro: Exception,
) -> None:
    with RelatorioCsv(relatorio) as saida:
        for bula in bulas:
            resultado = ResultadoExtracao(
                status=status,
                detalhe_erro=repr(erro),
            )
            saida.registrar(_linha_relatorio(bula, resultado, 0.0))


def main() -> int:
    load_dotenv(BASE_PROJETO / ".env")
    configurar_log()
    try:
        args = criar_argumentos()
        configuracao = configuracao_do_ambiente()
        configuracao.validar()
    except ValueError as erro:
        print(f"Configuração inválida: {erro}")
        return 2

    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL não configurada. Ajuste o arquivo .env local.")
        return 4

    limite = None if args.todos else args.limite
    try:
        repositorio = RepositorioInteracoes(dsn)
    except ErroBancoError as erro:
        logging.exception("Não foi possível preparar o PostgreSQL: %r", erro)
        print("Não foi possível preparar o PostgreSQL. Consulte o arquivo de log.")
        return 4

    try:
        bulas = repositorio.selecionar_bulas(
            inicio=args.inicio,
            limite=limite,
            reprocessar=args.reprocessar,
            nome_normalizado=args.nome_normalizado,
        )
        if not bulas:
            print("Nenhuma bula elegível foi encontrada para os parâmetros informados.")
            return 0

        print(
            "Carregando uma única instância do modelo GGUF Q4_K_M em CPU; "
            "o primeiro uso pode baixar cerca de 1,1 GB..."
        )
        print(f"Modo de extracao: {configuracao.modo_extracao}.")
        try:
            provedor = LlamaCppProvider.carregar(
                configuracao,
                diretorio_cache=BASE_PROJETO / "modelos_hf",
            )
        except ErroCarregamentoModeloError as erro:
            logging.exception("Erro de carregamento do modelo: %r", erro)
            registrar_falha_inicial(
                bulas,
                args.relatorio,
                StatusExtracao.ERRO_CARREGAMENTO_MODELO,
                erro,
            )
            print("O modelo não pôde ser carregado. Consulte o log detalhado.")
            return 5

        print("Executando smoke test curto do modelo...")
        try:
            provedor.testar()
        except ErroInferenciaError as erro:
            logging.exception("Smoke test do modelo falhou: %r", erro)
            registrar_falha_inicial(
                bulas,
                args.relatorio,
                StatusExtracao.ERRO_INFERENCIA,
                erro,
            )
            print("O smoke test do modelo falhou. Nenhum PDF foi processado.")
            return 5

        servico = ServicoExtracaoInteracoes(provedor, configuracao)
        with RelatorioCsv(args.relatorio) as relatorio:
            resumo = processar_lote(
                bulas,
                servico,
                repositorio,
                relatorio,
                reprocessar=args.reprocessar,
            )

        print("\nResumo:")
        for status, quantidade in resumo.items():
            print(f"- {status}: {quantidade}")
        print(f"Relatório: {args.relatorio.resolve()}")
        print(f"Log detalhado: {(BASE_PROJETO / 'logs' / 'extracao_interacoes.log').resolve()}")
        status_erro = {
            StatusExtracao.PDF_SEM_TEXTO.value,
            StatusExtracao.PDF_INVALIDO.value,
            StatusExtracao.RESPOSTA_INVALIDA.value,
            StatusExtracao.LIMITE_MEMORIA.value,
            StatusExtracao.ERRO_INFERENCIA.value,
            StatusExtracao.ERRO_BANCO.value,
            StatusExtracao.REVISAO_MANUAL.value,
        }
        return 1 if status_erro.intersection(resumo) else 0
    finally:
        repositorio.fechar()


if __name__ == "__main__":
    raise SystemExit(main())
