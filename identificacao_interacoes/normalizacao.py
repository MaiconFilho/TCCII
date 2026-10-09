"""Normalizações controladas usadas na comparação, nunca na gravação.

Nomes de medicamentos, princípios ativos e alvos usam a mesma regra que gerou
``bulas.nome_normalizado`` (caixa, acentos e espaços), importada do scraper.

As evidências também são comparadas com normalizações apenas de formatação:
quebras de linha e espaços repetidos, caixa, acentos, aspas e traços
tipográficos e hifens invisíveis. A passagem gravada é sempre recortada do
texto original, pelas posições mapeadas aqui.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from scraping_anvisa.anvisa_scraper.normalizacao import normalizar_nome


__all__ = [
    "TextoMapeado",
    "mapear_texto",
    "normalizar_alvo",
    "normalizar_citacao",
    "normalizar_nome",
    "contem_termo",
    "palavras_relevantes",
]

# Variantes tipográficas tratadas como formatação.
_EQUIVALENCIAS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "−": "-", " ": " ", " ": " ", " ": " ",
}
_INVISIVEIS = {"­", "​", "‌", "‍", "﻿"}
_SIMBOLOS_MARCA = re.compile(r"[®™©]")
_BORDAS = " \t\r\n.,;:!?()[]{}\"'*…-"
_STOPWORDS = frozenset(
    {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "com",
     "por", "para", "ou", "na", "no", "nas", "nos", "um", "uma", "the"}
)


def _dobrar(caractere: str) -> str:
    caractere = _EQUIVALENCIAS.get(caractere, caractere)
    decomposto = unicodedata.normalize("NFKD", caractere.casefold())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


@dataclass(frozen=True, slots=True)
class TextoMapeado:
    """Texto normalizado e a posição original de cada caractere seu."""

    texto: str
    posicoes: tuple[int, ...]

    def intervalo_original(self, inicio: int, fim: int) -> tuple[int, int]:
        """Converte [inicio, fim) normalizado em [inicio, fim) original."""
        return self.posicoes[inicio], self.posicoes[fim - 1] + 1


def mapear_texto(original: str, *, unir_hifenizacao: bool = False) -> TextoMapeado:
    """Normaliza mantendo o mapa para o texto original.

    Com ``unir_hifenizacao``, uma quebra de linha precedida de hífen entre
    letras ("metaboli-\\nzado") é removida, como faria a leitura corrida.
    """
    saida: list[str] = []
    posicoes: list[int] = []
    em_espaco = True  # ignora espaços iniciais
    indice = 0
    tamanho = len(original)
    while indice < tamanho:
        caractere = original[indice]
        if (
            unir_hifenizacao
            and caractere == "-"
            and saida
            and saida[-1].isalpha()
        ):
            seguinte = indice + 1
            while seguinte < tamanho and original[seguinte] in " \t\r":
                seguinte += 1
            if seguinte < tamanho and original[seguinte] == "\n":
                seguinte += 1
                while seguinte < tamanho and original[seguinte] in " \t\r":
                    seguinte += 1
                if seguinte < tamanho and original[seguinte].isalpha():
                    indice = seguinte
                    continue
        if caractere in _INVISIVEIS:
            indice += 1
            continue
        dobrado = _dobrar(caractere)
        if not dobrado:
            indice += 1
            continue
        if dobrado.isspace():
            if not em_espaco:
                saida.append(" ")
                posicoes.append(indice)
                em_espaco = True
            indice += 1
            continue
        for parte in dobrado:
            saida.append(parte)
            posicoes.append(indice)
        em_espaco = False
        indice += 1
    while saida and saida[-1] == " ":
        saida.pop()
        posicoes.pop()
    return TextoMapeado("".join(saida), tuple(posicoes))


def normalizar_citacao(citacao: str) -> str:
    """Citação no mesmo espaço normalizado, sem aspas nem pontuação de borda."""
    texto = mapear_texto(citacao).texto
    texto = texto.replace("...", " ").strip(_BORDAS)
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_alvo(alvo: str) -> str:
    """Alvo comparável: mesma regra de nome_normalizado, sem marcas e bordas."""
    texto = _SIMBOLOS_MARCA.sub(" ", alvo or "")
    texto = normalizar_nome(texto)
    return re.sub(r"\s+", " ", texto.strip(_BORDAS)).strip()


def palavras_relevantes(texto_normalizado: str) -> list[str]:
    return [
        palavra
        for palavra in re.findall(r"[a-z0-9]+", texto_normalizado)
        if len(palavra) >= 3 and palavra not in _STOPWORDS
    ]


def contem_termo(texto_normalizado: str, termo_normalizado: str) -> bool:
    """Termo inteiro, respeitando limites de palavra; não aceita substring."""
    if not termo_normalizado:
        return False
    padrao = r"(?<![a-z0-9])" + re.escape(termo_normalizado) + r"(?![a-z0-9])"
    return re.search(padrao, texto_normalizado) is not None
