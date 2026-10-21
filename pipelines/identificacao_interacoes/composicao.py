"""Equivalências declaradas na composição, usadas apenas como complemento.

A simples ocorrência de uma substância na composição não cria vínculo. Só é
aceita uma equivalência **declarada pela própria bula** e **ligada a um
princípio ativo** do cadastro, por exemplo::

    cloridrato de paroxetina ........ 22,214 mg
    (equivalente a 20 mg de paroxetina base)

Linhas de excipientes, veículos e declarações de ausência ("não contém",
"isento de", "livre de") são ignoradas. Nenhum sinônimo é inventado: a
substância equivalente é copiada do texto e comparada pela mesma
normalização de caixa, acentos e espaços.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalizacao import normalizar_alvo


ROTULO_EXCIPIENTE = re.compile(
    r"^\s*[*\-•]?\s*(?:excipientes?|ve[ií]culos?|componentes?\s+inativos?|outros\s+componentes"
    r"|ingredientes?\s+inativos?)\b",
    re.I,
)
NOVO_BLOCO = re.compile(r"^\s*(?:cada\b|composi[cç][aã]o|apresenta[cç][aã]o|princ[ií]pios?\s+ativos?)", re.I)
AUSENCIA = re.compile(
    r"\bn[aã]o\s+cont[eé]m\b|\bisent[oa]s?\s+de\b|\blivres?\s+de\b|\bsem\s+(?:adi[cç][aã]o\s+de\s+)?(?:a[cç][uú]car|lactose|gl[uú]ten|conservantes?)\b",
    re.I,
)
EQUIVALENCIA = re.compile(
    r"(?:equivalente|correspondente|corresponde)s?\s+a\s+"
    r"(?:[\d.,]+\s*(?:mg|g|mcg|µg|μg|ui|u\.i\.|%|ml|meq|mmol)(?![a-z])\.?\s*(?:\([^)]*\)\s*)?)?"
    r"(?:de\s+|do\s+|da\s+)?"
    r"(?P<substancia>[^\d();,*\n.]+?)"
    r"\s*(?:\bbase\b)?\s*(?=[)*,;.\n]|$)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class EquivalenciaDeclarada:
    substancia: str
    principio_ativo: str
    passagem: str


def _chaves_principios(principios_ativos: str | None) -> list[tuple[str, str]]:
    chaves = []
    for principio in (principios_ativos or "").split(";"):
        chave = normalizar_alvo(principio)
        if chave:
            chaves.append((chave, principio.strip()))
    return chaves


def _principio_na_linha(linha_normalizada: str, chaves: list[tuple[str, str]]) -> str | None:
    for chave, original in chaves:
        if re.search(r"(?<![a-z0-9])" + re.escape(chave) + r"(?![a-z0-9])", linha_normalizada):
            return original
    return None


def equivalencias_declaradas(
    composicao: str | None, principios_ativos: str | None
) -> list[EquivalenciaDeclarada]:
    if not composicao or not composicao.strip():
        return []
    chaves = _chaves_principios(principios_ativos)
    if not chaves:
        return []

    encontradas: list[EquivalenciaDeclarada] = []
    em_excipientes = False
    anterior: tuple[str, str | None] | None = None  # (linha, princípio ligado)
    for linha in composicao.splitlines():
        conteudo = linha.strip()
        if not conteudo:
            continue
        if NOVO_BLOCO.match(conteudo):
            em_excipientes = False
        if ROTULO_EXCIPIENTE.match(conteudo):
            em_excipientes = True
        if em_excipientes or AUSENCIA.search(conteudo):
            anterior = None
            continue

        normalizada = normalizar_alvo(conteudo)
        principio = _principio_na_linha(normalizada, chaves)
        passagem = conteudo
        if principio is None and conteudo[:1] in "(*" and anterior and anterior[1]:
            # Nota em linha própria, ligada ao ingrediente da linha anterior.
            principio = anterior[1]
            passagem = f"{anterior[0]}\n{conteudo}"
        if principio is not None:
            for correspondencia in EQUIVALENCIA.finditer(conteudo):
                substancia = re.sub(r"\s+", " ", correspondencia.group("substancia")).strip()
                chave = normalizar_alvo(substancia)
                if not chave or chave == normalizar_alvo(principio):
                    continue
                if len(chave) < 3 or not re.search(r"[a-z]", chave):
                    continue
                encontradas.append(EquivalenciaDeclarada(substancia, principio, passagem))
        anterior = (conteudo, principio)
    return encontradas
