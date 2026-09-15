"""OCR local e seletivo; nao envia PDFs para servicos externos."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .erros import ErroOCRError, OCRIndisponivelError
from .layout_pdf import ordenar_colunas

LOGGER = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent
# Invalida resultados gerados antes da ordenacao recursiva de multiplas colunas.
VERSAO_CACHE = 3


@dataclass(frozen=True)
class ConfiguracaoOCR:
    habilitado: bool = True
    idioma: str = "por"
    dpi: int = 200
    tessdata: Path = BASE / "modelos_ocr" / "tessdata"
    cache: Path | None = BASE / ".cache" / "ocr"
    max_pixels: int = 20_000_000

    @classmethod
    def do_ambiente(cls):
        ativo = os.getenv("OCR_HABILITADO", "true").lower().strip()
        if ativo not in {"true", "false", "1", "0"}:
            raise ErroOCRError("OCR_HABILITADO deve ser true ou false.")
        try:
            dpi = int(os.getenv("OCR_DPI", "200"))
        except ValueError as erro:
            raise ErroOCRError("OCR_DPI deve ser inteiro.") from erro
        if not 100 <= dpi <= 300:
            raise ErroOCRError("OCR_DPI deve estar entre 100 e 300.")
        pasta = os.getenv("OCR_TESSDATA") or os.getenv("TESSDATA_PREFIX")
        return cls(
            habilitado=ativo in {"true", "1"}, dpi=dpi,
            idioma=os.getenv("OCR_IDIOMA", "por"),
            tessdata=Path(pasta).expanduser().resolve() if pasta else cls.tessdata,
        )


def motivo_ocr(pagina, texto: str) -> str | None:
    """Heuristica por pagina: logo/figura pequena nao equivale a digitalizacao."""
    letras = sum(c.isalpha() for c in texto)
    if texto.count("\ufffd") > max(10, len(texto) * 0.1):
        return "texto_corrompido"
    area_pagina = pagina.rect.get_area()
    if area_pagina <= 0:
        return None
    palavras = None
    for imagem in pagina.get_image_info():
        rect = pymupdf.Rect(imagem["bbox"]) & pagina.rect
        proporcao = rect.get_area() / area_pagina
        if proporcao >= 0.5 and letras < 200:
            return "imagem_sem_texto_suficiente"
        if proporcao >= 0.12:
            if palavras is None:
                palavras = pagina.get_text("words")
            letras_na_imagem = sum(
                sum(c.isalpha() for c in palavra[4])
                for palavra in palavras
                if rect.contains(pymupdf.Point(
                    (palavra[0] + palavra[2]) / 2,
                    (palavra[1] + palavra[3]) / 2,
                ))
            )
            if letras_na_imagem < 40:
                return "imagem_parcial_sem_texto"
    # Letras convertidas em curvas tambem precisam de OCR. Nao renderizar
    # paginas realmente vazias ou uma simples moldura.
    if letras < 40 and len(pagina.get_cdrawings()) >= 50:
        return "texto_vetorial_sem_camada_textual"
    return None


def texto_ordenado_ocr(pagina, textpage):
    """Preserva a ordem de leitura de uma ou mais colunas reconhecidas."""
    linhas = [
        (pymupdf.Rect(linha["bbox"]), " ".join(
            span["text"] for span in linha.get("spans", []) if span["text"].strip()
        ).strip())
        for bloco in pagina.get_text("dict", textpage=textpage)["blocks"]
        if bloco.get("type") == 0
        for linha in bloco.get("lines", [])
    ]
    linhas = [(rect, texto) for rect, texto in linhas if texto]
    ordenadas = ordenar_colunas(linhas)
    if ordenadas is None:
        return pagina.get_text("text", textpage=textpage, sort=True) or ""
    return "\n".join(texto for _, texto in ordenadas)


class LeitorOCR:
    def __init__(self, caminho: Path, configuracao: ConfiguracaoOCR):
        self.caminho = caminho
        self.configuracao = configuracao
        self._hash_pdf = None
        self._hash_modelos = None
        self.paginas: list[int] = []
        self.tempo_segundos = 0.0
        self.acertos_cache = 0

    def _validar_modelos(self):
        if self._hash_modelos is not None:
            return
        if not self.configuracao.habilitado:
            raise OCRIndisponivelError(
                "Este PDF precisa de OCR, mas OCR_HABILITADO=false."
            )
        assinatura = hashlib.sha256()
        for idioma in self.configuracao.idioma.split("+"):
            if not idioma or not idioma.replace("_", "").isalnum():
                raise OCRIndisponivelError("OCR_IDIOMA invalido.")
            arquivo = self.configuracao.tessdata / (idioma + ".traineddata")
            if not arquivo.is_file():
                raise OCRIndisponivelError(
                    f"Modelo OCR ausente: {arquivo}. Execute python preparar_ocr.py "
                    "ou configure OCR_TESSDATA para os modelos instalados."
                )
            assinatura.update(arquivo.read_bytes())
        self._hash_modelos = assinatura.hexdigest()

    def _arquivo_cache(self, pagina, dpi, full):
        if self.configuracao.cache is None:
            return None
        if self._hash_pdf is None:
            with self.caminho.open("rb") as arquivo:
                self._hash_pdf = hashlib.file_digest(arquivo, "sha256").hexdigest()
        dados = (VERSAO_CACHE, pymupdf.VersionBind, self._hash_pdf,
                 self._hash_modelos, self.configuracao.idioma, dpi, full, pagina.number)
        chave = hashlib.sha256(repr(dados).encode()).hexdigest()
        return self.configuracao.cache / (chave + ".json")

    def ler_pagina(self, pagina, motivo):
        inicio = time.perf_counter()
        self.paginas.append(pagina.number + 1)
        try:
            self._validar_modelos()
            dpi_maximo = int(72 * math.sqrt(
                self.configuracao.max_pixels / pagina.rect.get_area()
            ))
            if dpi_maximo < 72:
                raise ErroOCRError("Pagina excessivamente grande para OCR seguro.")
            dpi = min(self.configuracao.dpi, dpi_maximo)
            full = motivo != "imagem_parcial_sem_texto"
            cache = self._arquivo_cache(pagina, dpi, full)
            if cache and cache.is_file():
                try:
                    dados = json.loads(cache.read_text(encoding="utf-8"))
                    if isinstance(dados.get("texto"), str) and dados["texto"].strip():
                        self.acertos_cache += 1
                        return dados["texto"]
                except (OSError, ValueError, AttributeError):
                    LOGGER.warning("Cache OCR invalido; recalculando pagina %s.", pagina.number + 1)
            LOGGER.info("OCR pagina %s (%s), %s dpi.", pagina.number + 1, motivo, dpi)
            textpage = pagina.get_textpage_ocr(
                language=self.configuracao.idioma, dpi=dpi, full=full,
                tessdata=str(self.configuracao.tessdata),
            )
            texto = texto_ordenado_ocr(pagina, textpage)
            if sum(c.isalpha() for c in texto) < 20:
                raise ErroOCRError(
                    f"OCR sem texto suficiente na pagina {pagina.number + 1}; "
                    "nao e possivel confirmar ausencia de secao."
                )
            if cache:
                temporario = None
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8", dir=cache.parent,
                        suffix=".tmp", delete=False,
                    ) as arquivo:
                        temporario = Path(arquivo.name)
                        json.dump({"texto": texto}, arquivo, ensure_ascii=False)
                    os.replace(temporario, cache)
                except OSError:
                    LOGGER.warning("Nao foi possivel salvar cache OCR; leitura preservada.")
                finally:
                    if temporario:
                        try:
                            temporario.unlink(missing_ok=True)
                        except OSError:
                            LOGGER.warning("Nao foi possivel remover arquivo temporario do cache OCR.")
            return texto
        except (OCRIndisponivelError, ErroOCRError):
            raise
        except Exception as erro:
            raise ErroOCRError(
                f"Falha de OCR na pagina {pagina.number + 1}: {erro}"
            ) from erro
        finally:
            self.tempo_segundos += time.perf_counter() - inicio
