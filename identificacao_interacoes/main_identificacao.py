"""CLI da identificação estruturada das interações.

Execute a partir da raiz do projeto, como módulo (sem alterar ``sys.path``)::

    python -m identificacao_interacoes --simular --todos
    python -m identificacao_interacoes --todos

A estratégia padrão (``dicionario``) procura os nomes do banco no tópico de
interações, sem modelo; ``atual`` e ``duas_etapas`` usam a LLM local.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .erros import ErroBancoError, EsquemaBancoIncompativelError
from .modelos import ESTRATEGIAS, STATUS_EXECUCAO_COM_ERRO, ConfiguracaoIdentificacao
from .relatorio import RelatorioIdentificacao


BASE_MODULO = Path(__file__).resolve().parent
RAIZ_PROJETO = BASE_MODULO.parent
REPOSITORIO_PADRAO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
ARQUIVO_PADRAO = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
# O GGUF já baixado pela extração é reaproveitado; nada é copiado.
CACHE_MODELOS_PADRAO = RAIZ_PROJETO / "extracao_interacoes" / "modelos_hf"
CAMINHO_LOG = BASE_MODULO / "logs" / "identificacao_interacoes.log"


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
        return inteiro_nao_negativo(valor) if permitir_zero else inteiro_positivo(valor)
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


def _booleano_env(nome: str, padrao: bool) -> bool:
    valor = os.getenv(nome, "true" if padrao else "false").strip().lower()
    if valor in {"true", "1", "sim", "yes"}:
        return True
    if valor in {"false", "0", "nao", "não", "no"}:
        return False
    raise ValueError(f"{nome} deve conter true ou false.")


def criar_argumentos(argumentos: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m identificacao_interacoes",
        description=(
            "Identifica as interações descritas em bulas_interacoes (por nomes do banco "
            "ou com a LLM local) e grava as relações no PostgreSQL."
        ),
    )
    parser.add_argument(
        "--estrategia", choices=ESTRATEGIAS,
        help="dicionario (padrão, sem modelo), atual ou duas_etapas (LLM). "
             "Sobrepõe IDENTIFICACAO_ESTRATEGIA.",
    )
    parser.add_argument(
        "--simular", action="store_true",
        help="Gera o relatório sem gravar nada no banco (conferência antes de publicar).",
    )
    parser.add_argument("--inicio", type=inteiro_nao_negativo, default=0)
    parser.add_argument(
        "--limite", type=inteiro_positivo, default=1,
        help="Quantidade de medicamentos; o padrão seguro é o piloto de 1.",
    )
    parser.add_argument(
        "--todos", action="store_true",
        help="Processa todos os elegíveis a partir de --inicio (precisa ser explícito).",
    )
    parser.add_argument(
        "--nome-normalizado", help="Seleciona exatamente um medicamento pelo nome normalizado."
    )
    parser.add_argument(
        "--reprocessar", action="store_true",
        help="Cria nova versão mesmo que já exista resultado vigente; o anterior vira histórico.",
    )
    parser.add_argument(
        "--atualizar-vinculos", action="store_true",
        help="Recalcula os vínculos com o cadastro atual sem carregar o modelo.",
    )
    parser.add_argument(
        "--retirar-processamento", type=inteiro_positivo, metavar="ID",
        help=(
            "Retira da vigência um processamento reconhecido como incorreto (exige --motivo). "
            "Nada é apagado e o modelo não é carregado."
        ),
    )
    parser.add_argument("--motivo", help="Motivo registrado junto da retirada explícita.")
    parser.add_argument(
        "--relatorio", type=Path,
        help="CSV de saída (padrão: relatorios/identificacao_<data>.csv).",
    )
    return parser.parse_args(argumentos)


def configuracao_do_ambiente() -> ConfiguracaoIdentificacao:
    return ConfiguracaoIdentificacao(
        repositorio_modelo=os.getenv("IDENTIFICACAO_HF_MODEL_REPO", REPOSITORIO_PADRAO).strip(),
        arquivo_modelo=os.getenv("IDENTIFICACAO_HF_MODEL_FILE", ARQUIVO_PADRAO).strip(),
        tamanho_contexto=_inteiro_env("IDENTIFICACAO_LLM_CONTEXT_SIZE", 8192),
        tokens_janela=_inteiro_env("IDENTIFICACAO_JANELA_TOKENS", 1000),
        sobreposicao_tokens=_inteiro_env("IDENTIFICACAO_JANELA_SOBREPOSICAO", 150, permitir_zero=True),
        max_tokens_saida=_inteiro_env("IDENTIFICACAO_LLM_MAX_OUTPUT_TOKENS", 3072),
        threads=_inteiro_env("IDENTIFICACAO_LLM_THREADS", 0, permitir_zero=True),
        tamanho_lote=_inteiro_env("IDENTIFICACAO_LLM_BATCH_SIZE", 256),
        camadas_gpu=_inteiro_env("IDENTIFICACAO_LLM_GPU_LAYERS", 0, permitir_zero=True),
        temperatura=_float_env("IDENTIFICACAO_LLM_TEMPERATURE", 0),
        limite_memoria_mb=_inteiro_env("IDENTIFICACAO_LLM_MAX_PROCESS_MEMORY_MB", 5500, permitir_zero=True),
        concorrencia=_inteiro_env("IDENTIFICACAO_LLM_INFERENCE_CONCURRENCY", 1),
        tentativas_correcao=_inteiro_env("IDENTIFICACAO_TENTATIVAS_CORRECAO", 1, permitir_zero=True),
        max_divisoes_janela=_inteiro_env("IDENTIFICACAO_MAX_DIVISOES_JANELA", 4, permitir_zero=True),
        max_caracteres_resumo=_inteiro_env("IDENTIFICACAO_MAX_CARACTERES_RESUMO", 300),
        exemplos_de_classe=_booleano_env("IDENTIFICACAO_EXEMPLOS_DE_CLASSE", True),
        releitura_de_listas=_booleano_env("IDENTIFICACAO_RELEITURA_LISTAS", True),
        estrategia=os.getenv("IDENTIFICACAO_ESTRATEGIA", "dicionario").strip(),
        lote_resumos=_inteiro_env("IDENTIFICACAO_LOTE_RESUMOS", 6),
    )


def diretorio_modelos() -> Path:
    configurado = os.getenv("IDENTIFICACAO_MODELOS_DIR", "").strip()
    return Path(configurado) if configurado else CACHE_MODELOS_PADRAO


def configurar_log() -> Path:
    CAMINHO_LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=CAMINHO_LOG,
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return CAMINHO_LOG


def caminho_relatorio(args: argparse.Namespace) -> Path:
    if args.relatorio:
        return args.relatorio
    sufixo = (
        "vinculos" if args.atualizar_vinculos
        else "simulacao" if getattr(args, "simular", False)
        else "identificacao"
    )
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    return BASE_MODULO / "relatorios" / f"{sufixo}_{carimbo}.csv"


def _imprimir_resumo(resumo: dict[str, int], relatorio: Path) -> None:
    print("\nResumo:")
    for status, quantidade in resumo.items():
        print(f"- {status}: {quantidade}")
    print(f"Relatório: {relatorio.resolve()}")
    print(f"Detalhes para revisão: {relatorio.resolve().with_name(relatorio.stem + '_detalhes.jsonl')}")
    print(f"Log detalhado: {CAMINHO_LOG.resolve()}")


def precisam_de_modelo(fontes, repositorio, versao: str, reprocessar: bool) -> bool:
    if reprocessar:
        return True
    for fonte in fontes:
        registrado = repositorio.processamento_registrado(fonte.nome_normalizado)
        if registrado is None or registrado["versao_processamento"] != versao:
            return True
    return False


def main(argumentos: list[str] | None = None) -> int:
    load_dotenv(BASE_MODULO / ".env")
    configurar_log()
    try:
        args = criar_argumentos(argumentos)
        configuracao = configuracao_do_ambiente()
        if args.estrategia:
            configuracao = replace(configuracao, estrategia=args.estrategia)
        configuracao.validar()
    except ValueError as erro:
        print(f"Configuração inválida: {erro}")
        return 2
    if args.todos and args.nome_normalizado:
        print("Use --todos ou --nome-normalizado, não ambos.")
        return 2
    if args.retirar_processamento and not (args.motivo or "").strip():
        print("Informe --motivo para retirar um processamento.")
        return 2

    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL não configurada. Ajuste identificacao_interacoes/.env.")
        return 4

    # Imports do PostgreSQL e da resolução só depois de validar a entrada.
    from .pipeline import atualizar_vinculos_lote, processar_lote, simular_lote
    from .repositorio import RepositorioIdentificacao
    from .resolucao import CatalogoMedicamentos

    try:
        repositorio = RepositorioIdentificacao(dsn)
    except EsquemaBancoIncompativelError as erro:
        print(f"Esquema incompatível: {erro}")
        return 4
    except ErroBancoError as erro:
        logging.exception("Não foi possível preparar o PostgreSQL: %r", erro)
        print("Não foi possível conectar ao PostgreSQL. Consulte o log.")
        return 4

    limite = None if args.todos else args.limite
    relatorio_csv = caminho_relatorio(args)
    try:
        if args.retirar_processamento:
            try:
                repositorio.invalidar_processamento(args.retirar_processamento, args.motivo)
            except ValueError as erro:
                print(f"Retirada não aplicada: {erro}")
                return 2
            print(
                f"Processamento {args.retirar_processamento} retirado da vigência. "
                "Histórico, relações e vínculos foram preservados."
            )
            return 0
        if args.atualizar_vinculos:
            processamentos = repositorio.processamentos_para_vinculos(
                inicio=args.inicio, limite=limite, nome_normalizado=args.nome_normalizado
            )
            if not processamentos:
                print("Nenhum processamento vigente encontrado para atualizar vínculos.")
                return 0
            catalogo = CatalogoMedicamentos(repositorio.carregar_catalogo())
            print(f"Cadastro carregado: {catalogo.quantidade} medicamento(s). Nenhum modelo será carregado.")
            with RelatorioIdentificacao(relatorio_csv) as relatorio:
                resumo = atualizar_vinculos_lote(processamentos, catalogo, repositorio, relatorio)
            _imprimir_resumo(resumo, relatorio_csv)
            return 1 if STATUS_EXECUCAO_COM_ERRO.intersection(resumo) else 0

        catalogo = CatalogoMedicamentos(repositorio.carregar_catalogo())
        if configuracao.usa_modelo:
            from .servico import ServicoIdentificacaoInteracoes

            # A versão não depende do modelo: dá para saber antes de carregá-lo se
            # há algo a processar (reexecução de um item já processado não carrega).
            servico = ServicoIdentificacaoInteracoes(None, configuracao)
        else:
            from .dicionario import ServicoDicionario

            servico = ServicoDicionario(catalogo)
            print(
                f"Busca por nomes do banco: {len(servico.termos)} nome(s) de "
                f"{catalogo.quantidade} medicamento(s); nenhum modelo será carregado."
            )
        print(f"Versão do processamento: {servico.versao_processamento}")
        fontes = repositorio.selecionar_fontes(
            inicio=args.inicio, limite=limite, nome_normalizado=args.nome_normalizado,
            reprocessar=args.reprocessar or args.simular, versao=servico.versao_processamento,
        )
        if not fontes:
            print("Nenhuma extração elegível foi encontrada para os parâmetros informados.")
            return 0

        carregar = configuracao.usa_modelo and (
            args.simular
            or precisam_de_modelo(fontes, repositorio, servico.versao_processamento, args.reprocessar)
        )
        if carregar:
            from .provedor import carregar_provedor

            print(
                f"Carregando uma única instância de {configuracao.arquivo_modelo} em CPU "
                f"(contexto {configuracao.tamanho_contexto}, saída até "
                f"{configuracao.max_tokens_saida} tokens)..."
            )
            try:
                servico.provedor = carregar_provedor(configuracao, diretorio_modelos())
                print("Executando smoke test curto do modelo...")
                servico.provedor.testar()
            except Exception as erro:  # noqa: BLE001 - erros do llama.cpp são heterogêneos
                logging.exception("Modelo indisponível: %r", erro)
                print(f"O modelo não pôde ser carregado ou testado: {type(erro).__name__}. Consulte o log.")
                return 5
        elif configuracao.usa_modelo:
            print("Os selecionados já têm processamento vigente desta versão; o modelo não será carregado.")
        with RelatorioIdentificacao(relatorio_csv) as relatorio:
            if args.simular:
                resumo = simular_lote(fontes, servico, catalogo, relatorio)
            else:
                resumo = processar_lote(
                    fontes, servico, catalogo, repositorio, relatorio, reprocessar=args.reprocessar
                )
        if args.simular:
            print("\nSimulação: nada foi gravado no banco.")
            if not configuracao.usa_modelo:
                # Para avaliar_referencias --nomes-banco, sem acessar o banco.
                nomes = relatorio_csv.with_name(relatorio_csv.stem + "_nomes_banco.txt")
                with nomes.open("x", encoding="utf-8") as arquivo:
                    arquivo.write("\n".join(sorted(servico.termos)) + "\n")
                print(f"Nomes do banco usados na busca: {nomes.resolve()}")
        _imprimir_resumo(resumo, relatorio_csv)
        return 1 if STATUS_EXECUCAO_COM_ERRO.intersection(resumo) else 0
    except ErroBancoError as erro:
        logging.exception("Falha de banco: %r", erro)
        print(f"Falha de banco: {erro}")
        return 4
    finally:
        repositorio.fechar()


if __name__ == "__main__":
    raise SystemExit(main())
