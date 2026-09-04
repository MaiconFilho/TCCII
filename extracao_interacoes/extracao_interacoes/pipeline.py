import time
from collections import Counter
from typing import Protocol

from .erros import (
    ContextoExcedidoError,
    ErroBancoError,
    ErroModeloError,
    PdfInvalidoError,
    PdfSemTextoError,
    RespostaInvalidaError,
    RespostaTruncadaError,
)
from .leitor_pdf import ler_pdf
from .modelos import BulaParaExtracao, RespostaExtracao, StatusExtracao
from .validacao import validar_resposta


class ModeloProtocolo(Protocol):
    def gerar(self, texto_pdf: str, erro_validacao: str | None = None): ...


class RepositorioProtocolo(Protocol):
    def gravar_resultado(
        self,
        bula: BulaParaExtracao,
        trecho_interacoes: str | None,
        reprocessar: bool = False,
    ) -> None: ...


class RelatorioProtocolo(Protocol):
    def registrar(self, dados: dict) -> None: ...


def _registrar_erro(
    registro: dict,
    status: StatusExtracao,
    erro: Exception,
) -> None:
    registro["status"] = status.value
    registro["detalhe_erro"] = f"{type(erro).__name__}: {erro}"


def processar_lote(
    bulas: list[BulaParaExtracao],
    modelo: ModeloProtocolo,
    repositorio: RepositorioProtocolo,
    relatorio: RelatorioProtocolo,
    reprocessar: bool = False,
) -> dict[str, int]:
    contagens: Counter[str] = Counter()

    for indice, bula in enumerate(bulas, start=1):
        prefixo = f"[{indice}/{len(bulas)}] {bula.nome_normalizado}"
        inicio_total = time.perf_counter()
        registro = {
            "nome_normalizado": bula.nome_normalizado,
            "numero_registro": bula.numero_registro,
            "expediente": bula.expediente or "",
            "arquivo_pdf": str(bula.caminho_pdf),
            "status": "",
            "titulo_encontrado": "",
            "quantidade_paginas": 0,
            "quantidade_caracteres": 0,
            "quantidade_tokens_entrada": 0,
            "quantidade_tokens_saida": 0,
            "tempo_leitura_segundos": 0.0,
            "tempo_inferencia_segundos": 0.0,
            "tempo_total_segundos": 0.0,
            "detalhe_erro": "",
        }

        print(f"{prefixo} — lendo PDF...")
        inicio_leitura = time.perf_counter()
        try:
            documento = ler_pdf(bula.caminho_pdf)
            registro["quantidade_paginas"] = documento.quantidade_paginas
            registro["quantidade_caracteres"] = documento.quantidade_caracteres
        except PdfSemTextoError as erro:
            _registrar_erro(registro, StatusExtracao.PDF_SEM_TEXTO, erro)
        except PdfInvalidoError as erro:
            _registrar_erro(registro, StatusExtracao.PDF_INVALIDO, erro)
        except Exception as erro:
            _registrar_erro(registro, StatusExtracao.PDF_INVALIDO, erro)
        finally:
            registro["tempo_leitura_segundos"] = (
                time.perf_counter() - inicio_leitura
            )

        resposta_validada: RespostaExtracao | None = None
        if not registro["status"]:
            print(f"{prefixo} — enviando texto completo ao modelo...")
            inicio_inferencia = time.perf_counter()
            erro_validacao: str | None = None
            try:
                for tentativa in range(2):
                    geracao = modelo.gerar(
                        documento.texto_com_marcadores,
                        erro_validacao=erro_validacao,
                    )
                    registro["quantidade_tokens_entrada"] += (
                        geracao.quantidade_tokens_entrada
                    )
                    registro["quantidade_tokens_saida"] += (
                        geracao.quantidade_tokens_saida
                    )
                    try:
                        resposta_validada = validar_resposta(
                            geracao.conteudo,
                            documento.texto_com_marcadores,
                            resposta_truncada=geracao.truncada,
                        )
                        break
                    except RespostaInvalidaError as erro:
                        erro_validacao = str(erro)
                        if tentativa == 1:
                            status = (
                                StatusExtracao.RESPOSTA_TRUNCADA
                                if isinstance(erro, RespostaTruncadaError)
                                else StatusExtracao.RESPOSTA_INVALIDA
                            )
                            _registrar_erro(registro, status, erro)
            except ContextoExcedidoError as erro:
                registro["quantidade_tokens_entrada"] += erro.tokens_entrada
                _registrar_erro(registro, StatusExtracao.CONTEXTO_EXCEDIDO, erro)
            except ErroModeloError as erro:
                _registrar_erro(registro, StatusExtracao.ERRO_MODELO, erro)
            except Exception as erro:
                _registrar_erro(registro, StatusExtracao.ERRO_MODELO, erro)
            finally:
                registro["tempo_inferencia_segundos"] = (
                    time.perf_counter() - inicio_inferencia
                )

        if resposta_validada is not None and not registro["status"]:
            try:
                repositorio.gravar_resultado(
                    bula,
                    resposta_validada.trecho_interacoes,
                    reprocessar=reprocessar,
                )
                if resposta_validada.encontrado:
                    registro["status"] = StatusExtracao.CONCLUIDO.value
                    registro["titulo_encontrado"] = (
                        resposta_validada.titulo_encontrado or ""
                    )
                    print(
                        f"{prefixo} — seção encontrada: "
                        f"{resposta_validada.titulo_encontrado}"
                    )
                else:
                    registro["status"] = (
                        StatusExtracao.SEM_SECAO_INTERACOES.value
                    )
                    print(f"{prefixo} — seção de interações não encontrada.")
                print(f"{prefixo} — resposta validada e gravada.")
            except ErroBancoError as erro:
                _registrar_erro(registro, StatusExtracao.ERRO_BANCO, erro)
            except Exception as erro:
                _registrar_erro(registro, StatusExtracao.ERRO_BANCO, erro)

        registro["tempo_total_segundos"] = time.perf_counter() - inicio_total
        for campo in (
            "tempo_leitura_segundos",
            "tempo_inferencia_segundos",
            "tempo_total_segundos",
        ):
            registro[campo] = round(float(registro[campo]), 3)
        relatorio.registrar(registro)
        contagens[registro["status"]] += 1
        if registro["detalhe_erro"]:
            print(
                f"{prefixo} — {registro['status']}: "
                f"{registro['detalhe_erro']}"
            )

    return dict(sorted(contagens.items()))
