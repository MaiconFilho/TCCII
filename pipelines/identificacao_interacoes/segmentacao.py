"""Unidades de texto (frases ou linhas) e janelas com sobreposição.

As mesmas unidades servem a dois propósitos:

- as janelas nunca cortam uma frase ao meio, então doses, negações e condições
  das bordas chegam inteiras ao modelo;
- a evidência aceita é expandida até as unidades que contêm a citação, de modo
  que a passagem gravada inclua a frase completa.

Todo o texto é coberto: ``criar_janelas`` confere a cobertura e nunca descarta
unidades silenciosamente.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .erros import ErroSegmentacaoError


ABREVIACOES = frozenset(
    {
        "ex", "p", "pex", "aprox", "max", "min", "sr", "sra", "dr", "dra",
        "vs", "n", "no", "nº", "cf", "fig", "tab", "ref", "vol", "pag", "pág",
        "al", "etc", "obs", "aprox", "i.e", "e.g", "mg", "ml", "cap", "art",
    }
)
REFERENCIA_NUMERADA = re.compile(
    r"(?:vide|ver|veja|consulte|se[cç][aã]o|item|itens|t[oó]pico)\s+"
    r"(?:o\s+|a\s+)?\d{1,2}(?:\.\d{1,2})*\.$",
    re.I,
)
TITULO_NUMERADO = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2})*\.?\s+\S")
MARCADOR_LISTA = re.compile(r"^\s*(?:[•▪●◦‣⁃\-–—*]|[a-z]\)|\d+\))\s+")


@dataclass(frozen=True, slots=True)
class UnidadeTexto:
    indice: int
    inicio: int
    fim: int
    tokens: int
    titulo: bool = False


@dataclass(frozen=True, slots=True)
class JanelaTexto:
    indice: int
    unidades: tuple[UnidadeTexto, ...]
    texto: str
    contexto_titulo: str = ""

    @property
    def inicio(self) -> int:
        return self.unidades[0].inicio

    @property
    def fim(self) -> int:
        return self.unidades[-1].fim

    @property
    def tokens(self) -> int:
        return sum(unidade.tokens for unidade in self.unidades)


def _linha_e_titulo(linha: str) -> bool:
    texto = linha.strip()
    if not texto or len(texto) > 110 or texto[-1] in ".;,:":
        return False
    if TITULO_NUMERADO.match(texto):
        return True
    letras = [c for c in texto if c.isalpha()]
    return len(letras) >= 4 and all(c.isupper() for c in letras)


def _fim_de_frase(texto: str, posicao: int) -> bool:
    """``posicao`` aponta para '.', '!' ou '?'; decide se encerra a frase."""
    seguinte = posicao + 1
    while seguinte < len(texto) and texto[seguinte] in "\"')”’]":
        seguinte += 1
    if seguinte >= len(texto):
        return True
    if not texto[seguinte].isspace():
        return False
    proximo = seguinte
    while proximo < len(texto) and texto[proximo].isspace():
        proximo += 1
    if proximo >= len(texto):
        return True
    caractere = texto[proximo]
    if not (caractere.isupper() or caractere.isdigit() or caractere in "•▪●(\"“-*"):
        return False
    if texto[posicao] != ".":
        return True
    inicio_palavra = posicao
    while inicio_palavra > 0 and not texto[inicio_palavra - 1].isspace():
        inicio_palavra -= 1
    palavra = texto[inicio_palavra:posicao].strip("(\"'").casefold()
    if palavra in ABREVIACOES or (len(palavra) == 1 and palavra.isalpha()):
        return False
    linha_atual = texto[texto.rfind("\n", 0, posicao) + 1: posicao + 1]
    if REFERENCIA_NUMERADA.search(linha_atual[-40:]):
        return False
    return True


def _cortes_por_tamanho(texto: str, inicio: int, fim: int, maximo: int) -> list[tuple[int, int]]:
    """Divide um bloco sem pontuação (tabelas) por linhas e, se preciso, espaços."""
    partes: list[tuple[int, int]] = []
    cursor = inicio
    while fim - cursor > maximo:
        limite = cursor + maximo
        corte = texto.rfind("\n", cursor + maximo // 2, limite)
        if corte <= cursor:
            corte = texto.rfind(" ", cursor + maximo // 2, limite)
        if corte <= cursor:
            corte = limite
        partes.append((cursor, corte))
        cursor = corte
    partes.append((cursor, fim))
    return partes


def segmentar(texto: str, max_caracteres: int = 1500) -> list[tuple[int, int, bool]]:
    """Intervalos [inicio, fim) das unidades, sem espaços nas bordas.

    Fronteiras: fim de frase, linha em branco, marcador de lista e título. Um
    bloco sem fronteiras é dividido por linhas para respeitar ``max_caracteres``.
    """
    brutos: list[tuple[int, int, bool]] = []
    inicio = 0
    deslocamento = 0
    linhas = texto.splitlines(keepends=True)
    for numero, linha in enumerate(linhas):
        inicio_linha = deslocamento
        deslocamento += len(linha)
        conteudo = linha.strip()
        if not conteudo:
            if inicio < inicio_linha:
                brutos.append((inicio, inicio_linha, False))
            inicio = deslocamento
            continue
        titulo = _linha_e_titulo(linha)
        lista = MARCADOR_LISTA.match(linha) is not None
        if (titulo or lista) and inicio < inicio_linha:
            brutos.append((inicio, inicio_linha, False))
            inicio = inicio_linha
        if titulo:
            brutos.append((inicio_linha, deslocamento, True))
            inicio = deslocamento
            continue
        for posicao in range(inicio_linha, deslocamento):
            if texto[posicao] in ".!?" and posicao >= inicio and _fim_de_frase(texto, posicao):
                fim = posicao + 1
                while fim < len(texto) and texto[fim] in "\"')”’]":
                    fim += 1
                brutos.append((inicio, fim, False))
                inicio = fim
    if inicio < len(texto):
        brutos.append((inicio, len(texto), False))

    unidades: list[tuple[int, int, bool]] = []
    for bruto_inicio, bruto_fim, titulo in brutos:
        for parte_inicio, parte_fim in _cortes_por_tamanho(
            texto, bruto_inicio, bruto_fim, max_caracteres
        ):
            trecho = texto[parte_inicio:parte_fim]
            esquerda = len(trecho) - len(trecho.lstrip())
            direita = len(trecho.rstrip())
            if direita > esquerda:
                unidades.append((parte_inicio + esquerda, parte_inicio + direita, titulo))
    return unidades


def criar_unidades(
    texto: str,
    contar_tokens: Callable[[str], int],
    max_caracteres: int = 1500,
) -> list[UnidadeTexto]:
    return [
        UnidadeTexto(
            indice=indice,
            inicio=inicio,
            fim=fim,
            tokens=max(1, contar_tokens(texto[inicio:fim])),
            titulo=titulo,
        )
        for indice, (inicio, fim, titulo) in enumerate(segmentar(texto, max_caracteres))
    ]


def _montar_janela(
    texto: str,
    indice: int,
    unidades: Sequence[UnidadeTexto],
    todas: Sequence[UnidadeTexto],
) -> JanelaTexto:
    primeira = unidades[0]
    contexto = ""
    if not primeira.titulo:
        for anterior in reversed(todas[: primeira.indice]):
            if anterior.titulo:
                contexto = texto[anterior.inicio:anterior.fim].strip()
                break
    return JanelaTexto(
        indice=indice,
        unidades=tuple(unidades),
        texto=texto[primeira.inicio:unidades[-1].fim],
        contexto_titulo=contexto,
    )


def criar_janelas(
    texto: str,
    unidades: Sequence[UnidadeTexto],
    limite_tokens: int,
    sobreposicao_tokens: int,
) -> list[JanelaTexto]:
    """Janelas sequenciais de unidades inteiras, com sobreposição em tokens."""
    if limite_tokens <= 0:
        raise ValueError("limite_tokens deve ser positivo.")
    if sobreposicao_tokens < 0 or sobreposicao_tokens >= limite_tokens:
        raise ValueError("A sobreposição deve ser menor que o limite da janela.")
    if not unidades:
        return []

    janelas: list[JanelaTexto] = []
    inicio = 0
    total = len(unidades)
    while inicio < total:
        fim = inicio
        tokens = 0
        while fim < total:
            if fim > inicio and tokens + unidades[fim].tokens > limite_tokens:
                break
            tokens += unidades[fim].tokens
            fim += 1
        janelas.append(_montar_janela(texto, len(janelas), unidades[inicio:fim], unidades))
        if fim >= total:
            break
        novo_inicio = fim
        acumulado = 0
        while novo_inicio > inicio + 1 and acumulado < sobreposicao_tokens:
            candidato = unidades[novo_inicio - 1].tokens
            if acumulado and acumulado + candidato > sobreposicao_tokens:
                break
            novo_inicio -= 1
            acumulado += candidato
        inicio = max(inicio + 1, novo_inicio)

    conferir_cobertura(unidades, janelas)
    return janelas


def conferir_cobertura(
    unidades: Sequence[UnidadeTexto], janelas: Sequence[JanelaTexto]
) -> None:
    cobertas = {unidade.indice for janela in janelas for unidade in janela.unidades}
    ausentes = [unidade.indice for unidade in unidades if unidade.indice not in cobertas]
    if ausentes:
        raise ErroSegmentacaoError(
            f"{len(ausentes)} unidade(s) do texto ficaram fora das janelas: {ausentes[:5]}"
        )


def dividir_janela(texto: str, janela: JanelaTexto, todas: Sequence[UnidadeTexto]) -> list[JanelaTexto]:
    """Divide em duas metades de unidades inteiras, com uma unidade em comum.

    A unidade repetida preserva a continuidade; as relações repetidas são
    consolidadas depois. Uma janela de uma única unidade não é divisível.
    """
    if len(janela.unidades) < 2:
        raise ErroSegmentacaoError("A janela tem uma única unidade e não pode ser dividida.")
    meio = len(janela.unidades) // 2
    esquerda = janela.unidades[: meio + (1 if len(janela.unidades) > 2 else 0)]
    direita = janela.unidades[meio:]
    return [
        _montar_janela(texto, janela.indice, esquerda, todas),
        _montar_janela(texto, janela.indice, direita, todas),
    ]


def unidades_da_passagem(
    unidades: Sequence[UnidadeTexto], inicio: int, fim: int
) -> list[UnidadeTexto]:
    """Unidades que se sobrepõem a [inicio, fim) do texto original."""
    return [u for u in unidades if u.fim > inicio and u.inicio < fim]
