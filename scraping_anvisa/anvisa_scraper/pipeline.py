import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selenium.webdriver.chrome.webdriver import WebDriver
    from .controle import ControleColeta
else:
    WebDriver = Any

from .api_anvisa import baixar_pdf, consultar_mais_recente_por_nome
from .erros import (
    BloqueioAnvisaError,
    DataPublicacaoInvalidaError,
    ErroColetaAnvisa,
    LimiteRequisicoesAnvisaError,
    MedicamentoNaoEncontradoError,
    SemBulaProfissionalError,
)
from .modelos import BulaLocalizada, MedicamentoParaColeta, montar_slug_nome


MAPA_STATUS = {
    MedicamentoNaoEncontradoError: "NOME_NAO_ENCONTRADO",
    DataPublicacaoInvalidaError: "DATA_PUBLICACAO_INVALIDA",
    SemBulaProfissionalError: "SEM_BULA_PROFISSIONAL",
}


def salvar_pdf_atomico(conteudo: bytes, destino: Path) -> tuple[str, int]:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".pdf.part")
    temporario.write_bytes(conteudo)
    temporario.replace(destino)
    return hashlib.sha256(conteudo).hexdigest(), len(conteudo)


def montar_nome_arquivo(bula: BulaLocalizada) -> str:
    slug = montar_slug_nome(bula.nome_normalizado)
    identificador = bula.numero_registro or hashlib.sha256(
        bula.id_bula_profissional.encode("utf-8")
    ).hexdigest()[:12]
    return f"{slug}_{identificador}_profissional.pdf"


def processar_lote(
    navegador: WebDriver,
    medicamentos: list[MedicamentoParaColeta],
    controle: "ControleColeta",
    pasta_pdfs: Path,
    intervalo_segundos: float,
    espera_limite_segundos: float = 60.0,
    max_limites_consecutivos: int = 3,
) -> dict[str, int]:
    resumo = {
        "nomes_selecionados": len(medicamentos),
        "concluidos": 0,
        "ignorados": 0,
        "erros": 0,
        "limitados": 0,
    }
    limites_consecutivos = 0
    max_limites_consecutivos = max(1, max_limites_consecutivos)

    for indice, medicamento in enumerate(medicamentos, start=1):
        prefixo = f"[{indice}/{len(medicamentos)}] {medicamento.nome_produto}"
        if controle.concluido(medicamento.nome_normalizado):
            print(f"{prefixo} - já concluído; ignorado.")
            resumo["ignorados"] += 1
            continue
        if controle.recuperar_pdf_existente(medicamento, pasta_pdfs):
            print(f"{prefixo} - PDF existente recuperado no banco; ignorado.")
            resumo["ignorados"] += 1
            continue

        controle.iniciar(medicamento)
        print(
            f"{prefixo} - consultando "
            f"({medicamento.quantidade_registros_planilha} ocorrência(s) na planilha)..."
        )
        bula: BulaLocalizada | None = None
        tempo_consulta_segundos: float | None = None
        tempo_download_segundos: float | None = None
        inicio_total = time.perf_counter()

        try:
            inicio_consulta = time.perf_counter()
            try:
                bula = consultar_mais_recente_por_nome(navegador, medicamento)
            finally:
                tempo_consulta_segundos = time.perf_counter() - inicio_consulta
            inicio_download = time.perf_counter()
            try:
                conteudo = baixar_pdf(navegador, bula)
            finally:
                tempo_download_segundos = time.perf_counter() - inicio_download
            destino = (pasta_pdfs / montar_nome_arquivo(bula)).resolve()
            sha256_pdf, tamanho = salvar_pdf_atomico(conteudo, destino)
            mensagem = "Bula profissional mais recente baixada e validada."

            tempo_total_segundos = time.perf_counter() - inicio_total
            controle.finalizar(
                medicamento,
                status="CONCLUIDO",
                mensagem=mensagem,
                bula=bula,
                caminho_pdf=str(destino),
                sha256_pdf=sha256_pdf,
                tamanho_pdf=tamanho,
                tempo_consulta_segundos=tempo_consulta_segundos,
                tempo_download_segundos=tempo_download_segundos,
                tempo_total_segundos=tempo_total_segundos,
            )
            resumo["concluidos"] += 1
            limites_consecutivos = 0
            print(f"{prefixo} - concluído ({tamanho:,} bytes).")
        except LimiteRequisicoesAnvisaError as erro:
            limites_consecutivos += 1
            espera = max(
                5.0,
                espera_limite_segundos,
                erro.retry_after_segundos or 0.0,
            )
            controle.finalizar(
                medicamento,
                status="LIMITE_REQUISICOES",
                mensagem=str(erro),
                bula=bula,
                tempo_consulta_segundos=tempo_consulta_segundos,
                tempo_download_segundos=tempo_download_segundos,
                tempo_total_segundos=time.perf_counter() - inicio_total,
            )
            resumo["limitados"] += 1
            if limites_consecutivos >= max_limites_consecutivos:
                print(
                    f"{prefixo} - LIMITE_REQUISICOES: {erro} "
                    f"Limite repetido {limites_consecutivos} vezes; interrompendo."
                )
                raise BloqueioAnvisaError(
                    "A Anvisa manteve o HTTP 429 após "
                    f"{limites_consecutivos} itens consecutivos."
                ) from erro
            print(
                f"{prefixo} - LIMITE_REQUISICOES: {erro} "
                f"Aguardando {espera:g}s e seguindo para o próximo."
            )
            if indice < len(medicamentos):
                time.sleep(espera)
            continue
        except BloqueioAnvisaError as erro:
            controle.finalizar(
                medicamento,
                status="INTERROMPIDO_BLOQUEIO",
                mensagem=str(erro),
                bula=bula,
                tempo_consulta_segundos=tempo_consulta_segundos,
                tempo_download_segundos=tempo_download_segundos,
                tempo_total_segundos=time.perf_counter() - inicio_total,
            )
            print(f"{prefixo} - {erro}")
            raise
        except ErroColetaAnvisa as erro:
            limites_consecutivos = 0
            status = next(
                (valor for classe, valor in MAPA_STATUS.items() if isinstance(erro, classe)),
                "ERRO_RESPOSTA",
            )
            controle.finalizar(
                medicamento,
                status=status,
                mensagem=str(erro),
                bula=bula,
                tempo_consulta_segundos=tempo_consulta_segundos,
                tempo_download_segundos=tempo_download_segundos,
                tempo_total_segundos=time.perf_counter() - inicio_total,
            )
            resumo["erros"] += 1
            print(f"{prefixo} - {status}: {erro}")
        except Exception as erro:
            limites_consecutivos = 0
            controle.finalizar(
                medicamento,
                status="ERRO_INESPERADO",
                mensagem=f"{type(erro).__name__}: {erro}",
                bula=bula,
                tempo_consulta_segundos=tempo_consulta_segundos,
                tempo_download_segundos=tempo_download_segundos,
                tempo_total_segundos=time.perf_counter() - inicio_total,
            )
            resumo["erros"] += 1
            print(f"{prefixo} - ERRO_INESPERADO: {type(erro).__name__}: {erro}")

        if indice < len(medicamentos):
            time.sleep(intervalo_segundos)

    return resumo
