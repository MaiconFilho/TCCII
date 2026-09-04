import json
import re
import unicodedata

from pydantic import ValidationError

from .erros import RespostaInvalidaError, RespostaTruncadaError
from .modelos import RespostaExtracao


PADRAO_MARCADOR_PAGINA = re.compile(r"\[\[PÁGINA\s+\d+\]\]", re.IGNORECASE)


def remover_cerca_markdown(conteudo: str) -> str:
    texto = conteudo.strip()
    if not texto.startswith("```"):
        return texto
    linhas = texto.splitlines()
    if len(linhas) < 3 or not linhas[-1].strip().startswith("```"):
        return texto
    return "\n".join(linhas[1:-1]).strip()


def remover_marcadores_pagina(texto: str) -> str:
    return PADRAO_MARCADOR_PAGINA.sub("", texto)


def normalizar_texto_tecnico(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", remover_marcadores_pagina(texto))
    texto = texto.replace("\u00a0", " ").replace("\u00ad", "")
    linhas = [re.sub(r"[ \t]+", " ", linha).strip() for linha in texto.splitlines()]
    resultado: list[str] = []
    linha_anterior_vazia = False
    for linha in linhas:
        vazia = not linha
        if vazia and linha_anterior_vazia:
            continue
        resultado.append(linha)
        linha_anterior_vazia = vazia
    return "\n".join(resultado).strip()


def normalizar_para_comparacao(texto: str) -> str:
    return re.sub(r"\s+", " ", normalizar_texto_tecnico(texto)).strip()


def titulo_relacionado_a_interacoes(titulo: str) -> bool:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", titulo.casefold())
        if not unicodedata.combining(caractere)
    )
    possui_interacao = "interacao" in sem_acentos or "interacoes" in sem_acentos
    equivalente_sem_palavra_interacao = (
        "medicament" in sem_acentos
        and any(
            termo in sem_acentos
            for termo in ("uso concomitante", "associacao", "compatibilidade")
        )
    )
    return possui_interacao or equivalente_sem_palavra_interacao


def validar_resposta(
    conteudo: str,
    texto_original: str,
    resposta_truncada: bool = False,
) -> RespostaExtracao:
    if resposta_truncada:
        raise RespostaTruncadaError(
            "A geração atingiu max_new_tokens sem produzir token de encerramento."
        )

    conteudo_limpo = remover_cerca_markdown(conteudo)
    try:
        dados = json.loads(conteudo_limpo)
    except json.JSONDecodeError as erro:
        raise RespostaInvalidaError(f"JSON inválido: {erro.msg}.") from erro
    try:
        resposta = RespostaExtracao.model_validate(dados)
    except ValidationError as erro:
        mensagem = erro.errors(include_url=False)[0]["msg"]
        raise RespostaInvalidaError(f"Estrutura JSON inválida: {mensagem}.") from erro

    if not resposta.encontrado:
        return resposta

    titulo = normalizar_texto_tecnico(resposta.titulo_encontrado or "")
    trecho = normalizar_texto_tecnico(resposta.trecho_interacoes or "")
    titulo_comparacao = normalizar_para_comparacao(titulo)
    trecho_comparacao = normalizar_para_comparacao(trecho)
    original_comparacao = normalizar_para_comparacao(texto_original)

    if not titulo_relacionado_a_interacoes(titulo):
        raise RespostaInvalidaError(
            "O título retornado não se relaciona a interações medicamentosas."
        )
    if titulo_comparacao not in original_comparacao:
        raise RespostaInvalidaError("O título retornado não aparece no PDF.")
    if not trecho_comparacao.startswith(titulo_comparacao):
        raise RespostaInvalidaError(
            "O trecho deve começar exatamente no título encontrado."
        )
    if trecho_comparacao not in original_comparacao:
        raise RespostaInvalidaError(
            "O trecho não está contido continuamente no texto do PDF."
        )
    corpo = trecho_comparacao[len(titulo_comparacao) :].strip(" :-–—\n\t")
    if len(re.sub(r"\W", "", corpo, flags=re.UNICODE)) < 15:
        raise RespostaInvalidaError(
            "O trecho contém apenas o título ou conteúdo insuficiente."
        )

    return resposta.model_copy(
        update={
            "titulo_encontrado": titulo,
            "trecho_interacoes": trecho,
        }
    )
