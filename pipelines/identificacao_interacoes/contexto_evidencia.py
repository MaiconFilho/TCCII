"""Blocos estruturados da bula: a lista e seu efeito são uma evidência literal."""

import re

# Rótulos em início de linha ou após pontuação, sem consumir a posição original.
ROTULO = re.compile(
    r"(?im)(?:^|(?<=[.;])\s+)[ \t]*(?P<tipo>medicamentos?|"
    r"efeitos?\s+(?:da\s+)?intera[çc][aã]o)\s*:")


def blocos_estruturados(texto: str) -> list[tuple[int, int]]:
    """Até o próximo Medicamento(s):; só une quando há Efeito(s) no mesmo bloco.

    Os índices sempre apontam para o texto original, inclusive linhas do PDF.
    Nunca associa uma lista ao efeito de outra lista.
    """
    rotulos = list(ROTULO.finditer(texto))
    blocos = []
    for indice, atual in enumerate(rotulos):
        if not atual.group("tipo").lower().startswith("medicamento"):
            continue
        seguintes = rotulos[indice + 1:]
        proximo = next((r for r in seguintes if r.group("tipo").lower().startswith("medicamento")), None)
        fim = proximo.start() if proximo else len(texto)
        efeito = next((r for r in seguintes if r.start() < fim
                       and not r.group("tipo").lower().startswith("medicamento")), None)
        if efeito is None:
            continue
        # Sem próximo medicamento, não ultrapassa um novo tópico numerado da bula.
        topico = re.search(r"\n\s*\d{1,2}[.)]\s+[A-ZÁÉÍÓÚÇ][A-ZÁÉÍÓÚÇ \t]{5,}", texto[efeito.end():fim])
        if topico:
            fim = efeito.end() + topico.start()
        inicio = atual.start()
        while inicio < fim and texto[inicio].isspace():
            inicio += 1
        while fim > inicio and texto[fim - 1].isspace():
            fim -= 1
        blocos.append((inicio, fim))
    return blocos


def completar_evidencia(texto: str, evidencia: str) -> str:
    """Amplia um recorte antigo apenas se todas as ocorrências tiverem o mesmo bloco."""
    if not texto or not evidencia:
        return evidencia
    faixas = blocos_estruturados(texto)
    if not faixas:
        return evidencia
    achados = [m.start() for m in re.finditer(re.escape(evidencia), texto)]
    completos = set()
    for inicio in achados:
        fim = inicio + len(evidencia)
        bloco = next(((a, b) for a, b in faixas if a <= inicio < b and fim <= b), None)
        # Um título de seção pode preceder o rótulo Medicamentos no recorte legado.
        if bloco is None:
            bloco = next(((a, b) for a, b in faixas if inicio <= a < fim <= b
                          and re.fullmatch(r"[\s\d.\-•A-ZÁÉÍÓÚÂÊÔÃÕÇ]+", texto[inicio:a])), None)
        if bloco is None:
            return evidencia
        a, b = bloco
        completos.add(texto[min(a, inicio):b])
    return completos.pop() if len(completos) == 1 else evidencia


def efeito_do_bloco(evidencia: str) -> str:
    for rotulo in ROTULO.finditer(evidencia):
        if not rotulo.group("tipo").lower().startswith("medicamento"):
            return evidencia[rotulo.end():].strip()
    return ""
