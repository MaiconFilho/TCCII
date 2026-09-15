from pathlib import Path

import pymupdf

from .erros import PdfInvalidoError, PdfSemTextoError, PdfComAnexosError
from .ocr import ConfiguracaoOCR, LeitorOCR, motivo_ocr
from .modelos import DocumentoPdf, LinhaDocumento
from .validacao import normalizar_linha
from .layout_pdf import ler_texto_nativo, separar_cabecalhos


MINIMO_CARACTERES_TEXTO = 40


def ler_pdf(
    caminho: Path,
    minimo_caracteres: int = MINIMO_CARACTERES_TEXTO,
    *,
    configuracao_ocr: ConfiguracaoOCR | None = None,
) -> DocumentoPdf:
    """Lê todas as páginas em ordem e atribui IDs globais às linhas úteis."""
    caminho = caminho.resolve()
    if not caminho.is_file():
        raise PdfInvalidoError(f"PDF não encontrado: {caminho}")

    linhas: list[LinhaDocumento] = []
    ocr = LeitorOCR(caminho, configuracao_ocr or ConfiguracaoOCR.do_ambiente())
    quantidade_paginas = 0
    try:
        with pymupdf.open(caminho) as documento:
            if not documento.is_pdf or documento.page_count <= 0:
                raise PdfInvalidoError(f"Arquivo não é um PDF válido: {caminho}")
            quantidade_paginas = documento.page_count
            if documento.embfile_count():
                raise PdfComAnexosError(
                    "PDF contem arquivos anexados/portfolio. A capa nao comprova "
                    "ausencia de interacoes; e necessario selecionar a bula interna."
                )
            for numero_pagina, pagina in enumerate(documento, start=1):
                # sort=True é importante: os IDs precisam acompanhar a ordem de leitura.
                texto_pagina = ler_texto_nativo(pagina)
                motivo = motivo_ocr(pagina, texto_pagina)
                if motivo:
                    texto_pagina = separar_cabecalhos(ocr.ler_pagina(pagina, motivo))
                for posicao, texto_original in enumerate(
                    texto_pagina.splitlines(), start=1
                ):
                    texto_original = texto_original.rstrip()
                    if not texto_original.strip():
                        continue
                    linhas.append(
                        LinhaDocumento(
                            numero=len(linhas) + 1,
                            pagina=numero_pagina,
                            posicao_pagina=posicao,
                            texto_original=texto_original,
                            texto_normalizado=normalizar_linha(texto_original),
                        )
                    )
    except PdfInvalidoError:
        raise
    except (pymupdf.FileDataError, RuntimeError, ValueError) as erro:
        raise PdfInvalidoError(
            f"Falha ao abrir o PDF '{caminho.name}': {erro!r}"
        ) from erro

    quantidade_caracteres = sum(
        len(linha.texto_original.strip()) for linha in linhas
    )
    if quantidade_caracteres < minimo_caracteres:
        raise PdfSemTextoError(
            "PDF sem camada textual suficiente: "
            f"{quantidade_caracteres} caracteres úteis; mínimo {minimo_caracteres}."
        )

    return DocumentoPdf(
        linhas=tuple(linhas),
        quantidade_paginas=quantidade_paginas,
        quantidade_caracteres=quantidade_caracteres,
        paginas_ocr=tuple(ocr.paginas),
        tempo_ocr_segundos=ocr.tempo_segundos,
        acertos_cache_ocr=ocr.acertos_cache,
    )
