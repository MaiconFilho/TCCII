from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path


BASE_PROJETO = Path(__file__).resolve().parent
if str(BASE_PROJETO.parent) not in sys.path:
    # Permite executar `python main_embeddings.py` sem duplicar a pasta.
    sys.path.insert(0, str(BASE_PROJETO.parent))

from dotenv import load_dotenv

from conversao_interacoes_vetores.erros import (
    DimensaoInvalidaError,
    ErroBancoError,
    ErroCarregamentoModeloError,
    ErroInferenciaError,
    ExtensaoVectorIndisponivelError,
    ModeloInvalidoError,
)
from conversao_interacoes_vetores.modelo_embeddings import (
    ProvedorEmbeddingsTransformers,
)
from conversao_interacoes_vetores.modelos import (
    ConfiguracaoEmbeddings,
    StatusEmbedding,
)
from conversao_interacoes_vetores.pipeline import processar_lote
from conversao_interacoes_vetores.relatorio import RelatorioEmbeddingsCsv
from conversao_interacoes_vetores.repositorio import RepositorioEmbeddings
from conversao_interacoes_vetores.servico import ServicoEmbeddings


MODELO_PADRAO = "intfloat/multilingual-e5-small"


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


def _booleano_env(nome: str, padrao: bool) -> bool:
    valor = os.getenv(nome, "true" if padrao else "false").strip().lower()
    if valor in {"true", "1", "sim", "yes"}:
        return True
    if valor in {"false", "0", "nao", "não", "no"}:
        return False
    raise ValueError(f"{nome} deve conter true ou false.")


def criar_argumentos(argumentos: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Converte os trechos de interações medicamentosas em vetores e "
            "grava no PostgreSQL com pgvector."
        )
    )
    parser.add_argument("--inicio", type=inteiro_nao_negativo, default=0)
    parser.add_argument(
        "--limite",
        type=inteiro_positivo,
        default=1,
        help="Quantidade de medicamentos; o padrão seguro é 1.",
    )
    parser.add_argument(
        "--todos",
        action="store_true",
        help="Processa todos os registros elegíveis a partir de --inicio.",
    )
    parser.add_argument(
        "--reprocessar",
        action="store_true",
        help="Regera e substitui os vetores mesmo que já existam.",
    )
    parser.add_argument(
        "--nome-normalizado",
        help="Seleciona exatamente um medicamento pelo nome normalizado.",
    )
    parser.add_argument("--batch-size", type=inteiro_positivo)
    parser.add_argument("--modelo", help="Sobrescreve EMBEDDING_MODEL_ID.")
    parser.add_argument("--device", choices=("cpu", "cuda"))
    parser.add_argument("--chunk-size", type=inteiro_positivo)
    parser.add_argument("--chunk-overlap", type=inteiro_nao_negativo)
    parser.add_argument(
        "--criar-indice",
        action="store_true",
        help="Cria o índice HNSW de cosseno após confirmar a dimensão.",
    )
    parser.add_argument(
        "--relatorio",
        type=Path,
        default=BASE_PROJETO / "relatorios" / "embeddings.csv",
    )
    return parser.parse_args(argumentos)


def prefixo_do_ambiente() -> str:
    """O .env perde o espaço final; a família E5 exige "passage: " com espaço."""
    prefixo = os.getenv("EMBEDDING_PASSAGE_PREFIX", "passage: ")
    if prefixo and not prefixo.endswith(" "):
        prefixo += " "
    return prefixo


def configuracao_do_ambiente() -> ConfiguracaoEmbeddings:
    return ConfiguracaoEmbeddings(
        modelo_id=os.getenv("EMBEDDING_MODEL_ID", MODELO_PADRAO).strip(),
        dispositivo=os.getenv("EMBEDDING_DEVICE", "cpu").strip().lower(),
        tamanho_lote=_inteiro_env("EMBEDDING_BATCH_SIZE", 8),
        max_tokens_entrada=_inteiro_env("EMBEDDING_MAX_INPUT_TOKENS", 512),
        normalizar=_booleano_env("EMBEDDING_NORMALIZE", True),
        tamanho_chunk=_inteiro_env("EMBEDDING_CHUNK_SIZE", 450),
        sobreposicao_chunk=_inteiro_env(
            "EMBEDDING_CHUNK_OVERLAP", 60, permitir_zero=True
        ),
        threads=_inteiro_env("EMBEDDING_THREADS", 0, permitir_zero=True),
        limite_memoria_mb=_inteiro_env(
            "EMBEDDING_MAX_PROCESS_MEMORY_MB", 5500, permitir_zero=True
        ),
        concorrencia=_inteiro_env("EMBEDDING_INFERENCE_CONCURRENCY", 1),
        prefixo_passagem=prefixo_do_ambiente(),
    )


def aplicar_argumentos(
    configuracao: ConfiguracaoEmbeddings, args: argparse.Namespace
) -> ConfiguracaoEmbeddings:
    alteracoes: dict[str, object] = {}
    if args.modelo:
        alteracoes["modelo_id"] = args.modelo.strip()
    if args.device:
        alteracoes["dispositivo"] = args.device
    if args.batch_size:
        alteracoes["tamanho_lote"] = args.batch_size
    if args.chunk_size:
        alteracoes["tamanho_chunk"] = args.chunk_size
    if args.chunk_overlap is not None:
        alteracoes["sobreposicao_chunk"] = args.chunk_overlap
    return replace(configuracao, **alteracoes) if alteracoes else configuracao


def configurar_log() -> Path:
    caminho = BASE_PROJETO / "logs" / "conversao_interacoes_vetores.log"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=caminho,
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return caminho


def main() -> int:
    load_dotenv(BASE_PROJETO / ".env")
    configurar_log()
    try:
        args = criar_argumentos()
        configuracao = aplicar_argumentos(configuracao_do_ambiente(), args)
        configuracao.validar()
    except ValueError as erro:
        print(f"Configuração inválida: {erro}")
        return 2

    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL não configurada. Ajuste o arquivo .env local.")
        return 4

    try:
        repositorio = RepositorioEmbeddings(dsn)
    except ErroBancoError as erro:
        logging.exception("Não foi possível preparar o PostgreSQL: %r", erro)
        print(f"Não foi possível preparar o PostgreSQL: {erro}")
        return 4

    try:
        try:
            repositorio.garantir_extensao_vector()
        except ExtensaoVectorIndisponivelError as erro:
            logging.exception("pgvector indisponível: %r", erro)
            print(f"pgvector indisponível: {erro}")
            return 4

        print(
            f"Carregando uma única instância de '{configuracao.modelo_id}' em "
            f"{configuracao.dispositivo.upper()}; o primeiro uso baixa os pesos..."
        )
        try:
            provedor = ProvedorEmbeddingsTransformers.carregar(
                configuracao,
                diretorio_cache=BASE_PROJETO / "modelos_hf",
            )
        except (ErroCarregamentoModeloError, ModeloInvalidoError) as erro:
            logging.exception("Erro de carregamento do modelo: %r", erro)
            print(f"O modelo não pôde ser carregado: {erro}")
            return 5

        print(
            f"Dimensão do modelo: {provedor.dimensao}; "
            f"limite útil por chunk: {provedor.limite_tokens} tokens."
        )
        try:
            repositorio.garantir_tabela(provedor.dimensao)
            if args.criar_indice:
                repositorio.garantir_indice(provedor.dimensao)
                print("Índice HNSW (vector_cosine_ops) verificado/criado.")
        except DimensaoInvalidaError as erro:
            logging.exception("Dimensão incompatível: %r", erro)
            print(f"Dimensão incompatível: {erro}")
            return 6
        except ErroBancoError as erro:
            logging.exception("Falha ao preparar a tabela de vetores: %r", erro)
            print(f"Falha ao preparar a tabela de vetores: {erro}")
            return 4

        print("Executando smoke test curto do modelo...")
        try:
            provedor.testar()
        except ErroInferenciaError as erro:
            logging.exception("Smoke test do modelo falhou: %r", erro)
            print("O smoke test do modelo falhou. Nenhum registro foi processado.")
            return 5

        limite = None if args.todos else args.limite
        interacoes = repositorio.selecionar_interacoes(
            inicio=args.inicio,
            limite=limite,
            reprocessar=args.reprocessar,
            nome_normalizado=args.nome_normalizado,
        )
        if not interacoes:
            print(
                "Nenhuma interação elegível foi encontrada para os parâmetros "
                "informados."
            )
            return 0

        servico = ServicoEmbeddings(provedor, configuracao)
        with RelatorioEmbeddingsCsv(args.relatorio) as relatorio:
            resumo = processar_lote(
                interacoes,
                servico,
                repositorio,
                relatorio,
                reprocessar=args.reprocessar,
            )

        print("\nResumo:")
        for status, quantidade in resumo.items():
            print(f"- {status}: {quantidade}")
        print(f"Relatório: {args.relatorio.resolve()}")
        print(
            "Log detalhado: "
            f"{(BASE_PROJETO / 'logs' / 'conversao_interacoes_vetores.log').resolve()}"
        )
        status_erro = {
            StatusEmbedding.SEM_TEXTO.value,
            StatusEmbedding.MODELO_INVALIDO.value,
            StatusEmbedding.DIMENSAO_INVALIDA.value,
            StatusEmbedding.ERRO_CHUNKING.value,
            StatusEmbedding.ERRO_INFERENCIA.value,
            StatusEmbedding.ERRO_BANCO.value,
            StatusEmbedding.LIMITE_MEMORIA.value,
        }
        return 1 if status_erro.intersection(resumo) else 0
    finally:
        repositorio.fechar()


if __name__ == "__main__":
    raise SystemExit(main())
