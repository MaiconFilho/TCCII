"""Preparação conservadora apenas dos cards que serão gerados.

Não reidentifica relações nem altera os registros prontos. Um recorte só é
ampliado quando existe uma seção nominal única no texto literal da própria bula.
Tabelas sem associação segura entre colunas ficam para revisão da evidência.
"""

import re

from pipelines.identificacao_interacoes.normalizacao import normalizar_alvo


class RevisaoDaEvidencia(ValueError):
    """A entrada precisa de conferência; não é uma falha de inferência do modelo."""


def recuperar_secao(texto: str, evidencia: str, alvo: str) -> str:
    """Recupera `alvo: ...` até o próximo rótulo, sem unir parágrafos distantes.

    O recorte legado deve ocorrer uma única vez e sobrepor essa seção. Na dúvida
    devolve o original. A evidência resultante continua sendo uma fatia literal.
    """
    if not texto or not evidencia or texto.count(evidencia) != 1:
        return evidencia
    inicio = texto.index(evidencia)
    fim = inicio + len(evidencia)
    rotulos = list(re.finditer(r"(?m)^[ \t]*([^\n:]{2,80}):[ \t]*", texto))
    candidatas = []
    for indice, rotulo in enumerate(rotulos):
        if normalizar_alvo(rotulo.group(1)) != normalizar_alvo(alvo):
            continue
        limite = rotulos[indice + 1].start() if indice + 1 < len(rotulos) else len(texto)
        if rotulo.start() < fim and limite > inicio:
            trecho = texto[rotulo.start():limite].strip()
            # Impede uma expansão excessiva causada por rótulos ausentes no PDF.
            if len(trecho) <= 1800 and rotulo.start() >= inicio - 1200:
                candidatas.append(trecho)
    return candidatas[0] if len(candidatas) == 1 else evidencia


def validar_entrada(item: dict, nome: str) -> None:
    """Não tenta consertar por aproximação nomes ou tabelas corrompidos."""
    alvo, evidencia = item["alvo"], item["evidencia"]
    if re.search(r"[↑↓↔\uF000-\uF8FF]", alvo) or alvo.count("(") != alvo.count(")"):
        raise RevisaoDaEvidencia("Revisão da identificação: o nome do alvo contém símbolos de tabela ou parênteses incompletos.")
    fonte = normalizar_alvo(evidencia)
    origem = normalizar_alvo(nome)
    destino = normalizar_alvo(alvo)
    if re.search(r"(?<!\w)" + re.escape(origem) + r"\s*\(\s*" + re.escape(destino) + r"\s*\)", fonte):
        raise RevisaoDaEvidencia("Revisão da identificação: o trecho apresenta o alvo como componente do próprio medicamento.")
    colunas = sum(bool(re.search(r"\S[ \t]{3,}\S", linha)) for linha in evidencia.splitlines())
    if colunas >= 2 and re.search(r"[↑↓↔\uF000-\uF8FF]|\b(?:ASC|AUC|Cmáx|Cmax|coadministrada)\b", evidencia, re.I):
        raise RevisaoDaEvidencia("Revisão da evidência: a tabela mistura linhas ou colunas; confira o cabeçalho e a linha do alvo antes de resumir.")
    # Lista isolada não informa o efeito. Não a transforma em ausência de interação.
    if re.fullmatch(r"[\w\s,;/()+.®™-]+", evidencia) and not re.search(
            r"\b(?:aument\w*|elev\w*|redu[cz]\w*|diminu\w*|potencializ\w*|inib\w*|"
            r"risco|efeito\w*|concentrac\w*|absor\w*|metaboli\w*|interac\w*|"
            r"administr\w*|utiliz\w*|uso|afet\w*|influenc\w*|clearance|meia.vida)\b", fonte):
        raise RevisaoDaEvidencia("Revisão da evidência: o recorte contém nomes, mas não descreve o efeito da interação.")
