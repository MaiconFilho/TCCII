from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # Compatibilidade com versões anteriores do PyMuPDF.
    import fitz

from .erros import PdfInvalidoError, PdfSemTextoError
from .modelos import DocumentoPdf


MINIMO_CARACTERES_TEXTO = 40


def ler_pdf(
    caminho: Path,
    minimo_caracteres: int = MINIMO_CARACTERES_TEXTO,
) -> DocumentoPdf:
    caminho = caminho.resolve()
    if not caminho.is_file():
        raise PdfInvalidoError(f"PDF não encontrado: {caminho}")

    try:
        with fitz.open(caminho) as documento:
            if not documento.is_pdf or documento.page_count <= 0:
                raise PdfInvalidoError(f"Arquivo não é um PDF válido: {caminho}")
            textos_paginas = [
                pagina.get_text("text", sort=True).strip()
                for pagina in documento
            ]
    except PdfInvalidoError:
        raise
    except (fitz.FileDataError, RuntimeError, ValueError) as erro:
        raise PdfInvalidoError(f"Falha ao abrir o PDF '{caminho.name}': {erro}") from erro

    texto_sem_marcadores = "\n\n".join(textos_paginas).strip()
    caracteres_uteis = sum(1 for caractere in texto_sem_marcadores if not caractere.isspace())
    if caracteres_uteis < minimo_caracteres:
        raise PdfSemTextoError(
            f"PDF sem camada textual suficiente: {caracteres_uteis} "
            f"caracteres úteis; mínimo {minimo_caracteres}."
        )

    paginas_marcadas = [
        f"[[PÁGINA {indice}]]\n{texto}"
        for indice, texto in enumerate(textos_paginas, start=1)
    ]
    texto_com_marcadores = "\n\n".join(paginas_marcadas)
    return DocumentoPdf(
        texto_com_marcadores=texto_com_marcadores,
        texto_sem_marcadores=texto_sem_marcadores,
        quantidade_paginas=len(textos_paginas),
        quantidade_caracteres=len(texto_sem_marcadores),
    )
