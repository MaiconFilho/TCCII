from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .erros import (
    RespostaInvalidaError,
    RespostaTruncadaError,
    SecaoSomenteTituloError,
)
from .modelos import DocumentoPdf, RespostaClassificacao


TModelo = TypeVar("TModelo", bound=BaseModel)
PADRAO_ID_LINHA = re.compile(r"^L(\d{6})$")
PADRAO_TITULO_INTERACOES = re.compile(
    r"^\s*(?:(?P<numero>\d{1,2})(?:\s*[.):-]\s*|\s+))?"
    r"(?:intera[çc](?:[ãaâ]o|[õoô]es)(?:\s+medicamentos(?:as?|osas))?"
    r"|intera[çc][õoô]es?\s+com\s+(?:outros\s+)?medicamentos?)"
    r"(?:\s+e\s+outras\s+(?:intera[çc][õoô]es|formas\s+de\s+intera[çc][ãaâ]o))?"
    r"\s*(?(numero)\)?|)[:.?]?\s*$",
    re.IGNORECASE,
)
PADRAO_TITULO_NUMERADO = re.compile(
    r"^\s*(\d{1,2})(?:\s*[.)-]\s*|\s+)\S"
)
TITULOS_PRINCIPAIS_CONHECIDOS = (
    "indicacoes",
    "contraindicacoes",
    "advertencias",
    "posologia",
    "posologia e modo de usar",
    "armazenamento",
    "advertencias e precaucoes",
    "reacoes adversas",
    "eventos adversos e alteracoes de exames laboratoriais",
    "eventos adversos e alteracoes de exames laboratorias",
    "cuidados de armazenamento",
    "cuidados de armazenamento do medicamento",
    "superdose",
    "superdosagem",
    "dizeres legais",
    "onde como e por quanto tempo posso guardar",
    "onde, como e por quanto tempo posso guardar este medicamento",
    "onde como e por quanto tempo posso guardar este medicamento",
    "quais os males que este medicamento pode me causar",
    "o que fazer se alguem usar uma quantidade maior",
    "o que fazer se alguem usar uma quantidade maior do que a indicada deste medicamento",
)
# Excecoes semanticas ao tamanho minimo, somente sob um titulo de interacoes.
# O texto continua sujeito a validacao de limites e a confirmacao da LLM.
RESPOSTAS_CURTAS_INTERACOES = frozenset({
    "nao existem", "nao existe", "nao existem interacoes",
    "nao ha", "nao ha interacoes", "nenhuma", "nenhuma conhecida",
    "nao conhecidas", "nao relatadas", "nao descritas",
    "nao se aplica", "desconhecidas", "inexistentes",
    "nao aplicavel", "nao sao conhecidas", "nao foram descritas", "nao sao descritas",
    "nao apresenta",
})
PADRAO_ENTRADA_SUMARIO = re.compile(r"\.{2,}\s*\d+\s*$")

# Vocabulario fechado de rotulos administrativos. Qualquer palavra desconhecida
# impede descartar o corpo: tabelas clinicas e listas de medicamentos continuam.
PALAVRAS_ROTULOS = set((
    "interacoes medicamentosas indicacoes contraindicacoes advertencias precaucoes "
    "reacoes adversas cuidados armazenamento medicamento medicamentos posologia "
    "modo usar superdose dizeres legais apresentacao composicao resultados eficacia "
    "notificacao alteracao alteracoes inclusao inicial texto bula excipiente maior "
    "moderada de da do das dos e em para por no na o a "
    "mg ml g mcg sol oral cx fr plas opc got x emb hosp ct vp vps similar rdc"
).split())


def corpo_apenas_referencias(textos: Sequence[str]) -> bool:
    """Rejeita apenas rotulos/codigos conhecidos; nao exige prosa em toda tabela."""
    texto = sem_acentos(normalizar_linha(" ".join(textos)))
    palavras = re.findall(r"[a-z]+", texto)
    if not palavras:
        return False
    data = bool(re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", texto))
    embalagem = bool(re.search(r"\b(?:cx|fr|emb|hosp|plas|ct)\b", texto))
    regulatorio = bool(
        re.search(r"\b(?:rdc|notificacao|expediente)\b", texto)
    )
    dosagem = bool(re.search(r"\b(?:mg|ml|mcg)\b", texto))

    # Marcadores regulatorios combinados com data/apresentacao identificam
    # tabelas administrativas mesmo quando o PDF embaralha suas colunas.
    if regulatorio and (data or embalagem):
        return True
    # Mantem a regra fechada para sequencias simples de rotulos conhecidos.
    return set(palavras) <= PALAVRAS_ROTULOS and (
        (embalagem and dosagem) or (data and embalagem)
    )


def normalizar_linha(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", texto)
    texto = texto.replace("\u00a0", " ").replace("\u00ad", "")
    # Apenas a representacao de comparacao; a copia mantem o texto original.
    texto = texto.replace("\u2013", "-").replace("\u2014", "-")
    # Alguns geradores de PDF substituem o espaco do titulo por dois pontos.
    # A regra e restrita a pontuacao entre letras e nao altera o texto copiado.
    texto = re.sub(r"(?<=\w)[.\u00b7]{2,}(?=\w)", " ", texto)
    texto = re.sub(r"^(\d{1,2})\.\s+\1\.\s*", r"\1. ", texto.strip())
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_para_comparacao(texto: str) -> str:
    return normalizar_linha(texto).casefold()


def sem_acentos(texto: str) -> str:
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto.casefold())
        if not unicodedata.combining(caractere)
    )


def titulo_relacionado_a_interacoes(titulo: str) -> bool:
    valor = sem_acentos(titulo)
    return "interacao" in valor or "interacoes" in valor


def remover_cerca_markdown(conteudo: str) -> str:
    texto = conteudo.strip()
    if not texto.startswith("```"):
        return texto
    linhas = texto.splitlines()
    if len(linhas) < 3 or not linhas[-1].strip().startswith("```"):
        return texto
    return "\n".join(linhas[1:-1]).strip()


def analisar_json(
    conteudo: str,
    esquema: type[TModelo],
    resposta_truncada: bool = False,
) -> TModelo:
    if resposta_truncada:
        raise RespostaTruncadaError(
            "A resposta atingiu o limite de tokens antes de terminar."
        )
    try:
        dados = json.loads(remover_cerca_markdown(conteudo))
    except json.JSONDecodeError as erro:
        raise RespostaInvalidaError(f"JSON inválido: {erro.msg}.") from erro
    try:
        return esquema.model_validate(dados)
    except ValidationError as erro:
        mensagem = erro.errors(include_url=False)[0]["msg"]
        raise RespostaInvalidaError(
            f"Estrutura JSON inválida: {mensagem}."
        ) from erro


def numero_linha(identificador: str, permitir_sentilena: int | None = None) -> int:
    correspondencia = PADRAO_ID_LINHA.fullmatch(identificador or "")
    if correspondencia is None:
        raise RespostaInvalidaError(
            f"Identificador de linha inválido: {identificador!r}."
        )
    numero = int(correspondencia.group(1))
    if numero < 1 or (
        permitir_sentilena is not None and numero > permitir_sentilena
    ):
        raise RespostaInvalidaError(
            f"Identificador fora do documento: {identificador!r}."
        )
    return numero


def validar_candidato(
    resposta: RespostaClassificacao,
    documento: DocumentoPdf,
    ids_janela: set[str],
) -> None:
    if resposta.linha_titulo is None or resposta.titulo_encontrado is None:
        raise RespostaInvalidaError("O candidato não informou título e linha.")
    if resposta.linha_titulo not in ids_janela:
        raise RespostaInvalidaError(
            "A linha de título não pertence à janela analisada."
        )
    linha = documento.obter_linha(resposta.linha_titulo)
    if linha is None:
        raise RespostaInvalidaError("A linha de título não existe no PDF.")
    if not titulo_relacionado_a_interacoes(resposta.titulo_encontrado):
        raise RespostaInvalidaError(
            "O título não se relaciona a interações medicamentosas."
        )
    if _parece_sumario(documento, linha.numero - 1):
        raise RespostaInvalidaError(
            "A ocorrência indicada pertence ao sumário, não ao corpo da bula."
        )
    numero = linha.numero - 1
    contexto_titulo = " ".join(
        item.texto_original for item in documento.linhas[numero : numero + 3]
    )
    prefixo = r"^\d{1,2}(?:\s*[.):\-]\s*|\s+)"
    titulo_comparado = re.sub(prefixo, "", normalizar_para_comparacao(resposta.titulo_encontrado))
    contexto_comparado = re.sub(prefixo, "", normalizar_para_comparacao(contexto_titulo))
    if not contexto_comparado.startswith(titulo_comparado):
        raise RespostaInvalidaError(
            "O título informado não corresponde literalmente ao PDF."
        )


def copiar_e_validar_trecho(
    documento: DocumentoPdf,
    linha_inicio: str,
    linha_fim_exclusiva: str,
    titulo: str,
    proximo_titulo: str | None = None,
) -> str:
    inicio = numero_linha(linha_inicio, len(documento.linhas))
    fim = numero_linha(linha_fim_exclusiva, len(documento.linhas) + 1)
    if fim <= inicio:
        raise RespostaInvalidaError("O fim da seção deve ficar depois do título.")

    linhas = documento.linhas[inicio - 1 : fim - 1]
    trecho = "\n".join(linha.texto_original for linha in linhas).strip()
    if trecho not in documento.texto_original:
        raise RespostaInvalidaError(
            "O trecho não está contido literalmente no texto original do PDF."
        )
    titulo_normalizado = normalizar_para_comparacao(titulo)
    trecho_normalizado = normalizar_para_comparacao(trecho)
    # O ID aponta para a linha original inteira. Se a LLM omitir apenas a
    # numeracao no nome do titulo, compare sem ela, mas preserve-a na copia.
    prefixo_numero = r"^\d{1,2}(?:\s*[.):\-]\s*|\s+)"
    if not re.match(prefixo_numero, titulo_normalizado):
        trecho_normalizado = re.sub(prefixo_numero, "", trecho_normalizado, count=1)
    if not trecho_normalizado.startswith(titulo_normalizado):
        # Títulos quebrados em linhas continuam literais após normalização.
        inicio_documento = " ".join(
            linha.texto_original for linha in linhas[:3]
        )
        if not normalizar_para_comparacao(inicio_documento).startswith(
            titulo_normalizado
        ):
            raise RespostaInvalidaError(
                "O trecho não começa exatamente no título indicado."
            )

    corpo = trecho_normalizado[len(titulo_normalizado) :].strip(" :-–—.\t\n")
    resposta_curta_valida = (
        PADRAO_TITULO_INTERACOES.fullmatch(normalizar_linha(titulo)) is not None
        and sem_acentos(corpo) in RESPOSTAS_CURTAS_INTERACOES
    )
    if len(re.sub(r"\s", "", corpo)) < 20 and not resposta_curta_valida:
        raise SecaoSomenteTituloError(
            "Secao sem corpo suficiente nem resposta curta reconhecida."
        )
    if corpo == titulo_normalizado:
        raise SecaoSomenteTituloError("Título e corpo da seção são iguais.")
    # Contar caracteres nao basta: uma sequencia de rotulos tambem pode ser longa.
    partes_corpo = [normalizar_linha(l.texto_original) for l in linhas[1:]]
    if corpo_apenas_referencias(partes_corpo):
        raise SecaoSomenteTituloError("O suposto corpo contem apenas rotulos administrativos e codigos.")
    if partes_corpo and all(
        PADRAO_TITULO_INTERACOES.fullmatch(parte)
        or re.sub(
            r"^\d{1,2}(?:\s*[.)-]\s*|\s+)", "", sem_acentos(parte)
        ).strip(" .:-")
        in TITULOS_PRINCIPAIS_CONHECIDOS
        or re.fullmatch(r"[\d\s./:-]+", parte)
        for parte in partes_corpo
    ):
        raise SecaoSomenteTituloError("Apos o titulo ha apenas rotulos ou numeros, sem corpo.")
    linhas_apos_titulo = [
        normalizar_linha(linha.texto_original) for linha in linhas[1:]
    ]
    if len(linhas_apos_titulo) == 1 and (
        PADRAO_TITULO_NUMERADO.match(linhas_apos_titulo[0])
        or _eh_titulo_principal_conhecido(linhas_apos_titulo[0])
    ):
        raise SecaoSomenteTituloError(
            "Depois do título há somente outro título, sem corpo próprio."
        )

    documento_normalizado = normalizar_para_comparacao(documento.texto_original)
    titulo_original = linhas[0].texto_original
    if fim <= len(documento.linhas) and not limite_no_documento(
        documento, fim - 1, titulo_original
    ):
        raise RespostaInvalidaError(
            "O limite e um subtitulo ou paragrafo, nao o proximo topico principal."
        )
    for linha in linhas[1:]:
        if limite_no_documento(documento, linha.numero - 1, titulo_original):
            raise RespostaInvalidaError(
                "O trecho ultrapassa um titulo principal reconhecido."
            )
    if trecho_normalizado not in documento_normalizado:
        raise RespostaInvalidaError(
            "O trecho copiado deixou de ser uma sequência literal do PDF."
        )
    if proximo_titulo and normalizar_para_comparacao(proximo_titulo) in trecho_normalizado:
        raise RespostaInvalidaError(
            "O trecho inclui o próximo título e está truncado no limite incorreto."
        )
    return trecho


def _eh_titulo_principal_conhecido(texto: str) -> bool:
    valor = re.sub(
        r"^\s*\d{1,2}(?:\s*[.)-]\s*|\s+)", "", sem_acentos(normalizar_linha(texto))
    )
    valor = valor.strip(" .:?-")
    # O nome completo precisa ser um titulo conhecido. Prefixos como
    # "posologia de ropinirol durante tratamento..." continuam sendo corpo.
    return valor in TITULOS_PRINCIPAIS_CONHECIDOS


def eh_limite_principal(titulo_atual: str, seguinte: str) -> bool:
    """Verifica hierarquia; subtitulos internos e itens de listas nao encerram a secao."""
    atual = PADRAO_TITULO_NUMERADO.match(normalizar_linha(titulo_atual))
    texto = normalizar_linha(seguinte)
    # Uma referencia quebrada pode comecar com o nome de outro topico:
    # "(vide item 5. / ADVERTENCIAS E PRECAUCOES)." nao encerra a secao.
    # O ')' de '7) CUIDADOS...' pertence a numeracao, nao a uma referencia.
    sem_numero = re.sub(r"^\s*\d{1,2}\s*\)\s*", "", texto)
    if sem_numero.count(")") > sem_numero.count("(") or sem_numero.count("]") > sem_numero.count("["):
        return False
    if re.match(r"^\d+\.\d", texto):
        return False
    proximo = PADRAO_TITULO_NUMERADO.match(texto)
    if proximo:
        if atual and int(proximo.group(1)) <= int(atual.group(1)):
            return False
        return texto.isupper() or _eh_titulo_principal_conhecido(texto)
    return _eh_titulo_principal_conhecido(texto)


def _parece_sumario(documento: DocumentoPdf, indice: int) -> bool:
    anteriores = documento.linhas[max(0, indice - 15) : indice]
    if any(
        re.fullmatch(r"(?:sumario|indice)(?:\s+(?:geral|da bula))?[:.]?", sem_acentos(normalizar_linha(linha.texto_original)))
        for linha in anteriores
    ):
        return True
    seguintes = documento.linhas[indice : indice + 7]
    entradas = sum(
        PADRAO_ENTRADA_SUMARIO.search(linha.texto_original) is not None
        for linha in seguintes
    )
    return entradas >= 2


def limite_no_documento(documento, indice, titulo):
    anterior=' '.join(l.texto_original for l in documento.linhas[max(0,indice-3):indice])
    if re.search(r'\([^)]*(?:vide|veja|ver|consulte)[^)]*$',anterior,re.IGNORECASE):
        return False
    return eh_limite_principal(titulo,documento.linhas[indice].texto_original)


def localizar_titulos_interacoes(
    documento: DocumentoPdf,
) -> list[tuple[int, int, str]]:
    """Localiza apenas titulos explicitos, inclusive quando quebrados em linhas."""
    resultados: list[tuple[int, int, str]] = []
    administrativas = paginas_administrativas(documento)
    fim_ultimo_titulo = -1
    for indice, linha in enumerate(documento.linhas):
        if indice <= fim_ultimo_titulo:
            continue
        titulo = normalizar_linha(linha.texto_original)
        quantidade_linhas_titulo = 1
        # O PDF pode quebrar o numero e as palavras do titulo em ate tres linhas.
        for quantidade in (2, 3):
            composto = normalizar_linha(" ".join(
                item.texto_original for item in documento.linhas[indice:indice + quantidade]
            ))
            if PADRAO_TITULO_INTERACOES.fullmatch(composto):
                titulo, quantidade_linhas_titulo = composto, quantidade
        if PADRAO_TITULO_INTERACOES.fullmatch(titulo) is None:
            continue
        fim_ultimo_titulo = indice + quantidade_linhas_titulo - 1
        if linha.pagina in administrativas or _parece_sumario(documento, indice):
            continue
        resultados.append((indice, quantidade_linhas_titulo, titulo))
    # Nao confundir subtitulos de farmacologia/advertencias/reacoes adversas
    # com a secao principal, quando esta existe explicitamente numerada.
    if any(PADRAO_TITULO_NUMERADO.match(t) for _, _, t in resultados):
        filtrados = []
        for i, n, t in resultados:
            if not PADRAO_TITULO_NUMERADO.match(t):
                anteriores = [normalizar_linha(l.texto_original) for l in documento.linhas[:i]
                    if PADRAO_TITULO_NUMERADO.match(normalizar_linha(l.texto_original))
                    and normalizar_linha(l.texto_original).isupper()]
                pai = sem_acentos(anteriores[-1]) if anteriores else ''
                if any(s in pai for s in ('caracteristicas farmacologicas', 'advertencias', 'reacoes adversas')):
                    continue
            filtrados.append((i, n, t))
        resultados = filtrados
    return resultados


def paginas_administrativas(documento: DocumentoPdf) -> set[int]:
    """Tabela regulatoria exige combinacao de marcadores, nunca apenas uma palavra."""
    paginas = {}
    for l in documento.linhas:
        paginas.setdefault(l.pagina, []).append(l.texto_original)
    resultado = set()
    for p, linhas in paginas.items():
        texto = sem_acentos(normalizar_linha(' '.join(linhas)))
        cabecalhos = sum(s in texto for s in ('submissao eletronica', 'peticao/notificacao', 'itens de bula', 'apresentacoes relacionadas', 'data de aprovacao'))
        formal = any(s in texto for s in ('texto de bula', 'alteracao de texto', 'inclusao inicial', 'dados das alteracoes'))
        tabela = formal and ('notificacao' in texto or 'inclusao inicial' in texto) and ('rdc' in texto or 'expediente' in texto)
        datas = len(re.findall(r'\b\d{2}/\d{2}/\d{4}\b', texto))
        rotulos = sum(bool(re.search(r'\b'+s+r'\b', texto)) for s in ('vps', 'vp', 'assunto', 'versoes', 'expediente', 'apresentacoes'))
        if cabecalhos >= 3 or (tabela and datas >= 2 and rotulos >= 1) or (
            'historico de alteracoes' in texto and tabela and rotulos >= 2
        ):
            resultado.add(p)
        elif (
            p - 1 in resultado
            and "notificacao" in texto
            and "rdc" in texto
            and rotulos >= 2
        ):
            # A ultima pagina da tabela pode repetir apenas os dados das linhas,
            # sem repetir o cabecalho "texto de bula"/"historico".
            resultado.add(p)
    return resultado


def localizar_fallback_estrutural(
    documento: DocumentoPdf,
) -> list[tuple[str, str, str]]:
    """Candidatos estruturais para confirmacao hibrida ou fallback do modo completo."""
    resultados: list[tuple[str, str, str]] = []
    for indice, _quantidade_linhas_titulo, titulo in localizar_titulos_interacoes(
        documento
    ):
        linha = documento.linhas[indice]
        fim = None
        for proxima in documento.linhas[indice + 1 :]:
            texto = normalizar_linha(proxima.texto_original)
            if limite_no_documento(documento, proxima.numero - 1, titulo):
                fim = proxima.numero
                break
        if fim is None:
            fim = len(documento.linhas) + 1
        try:
            copiar_e_validar_trecho(
                documento,
                linha.identificador,
                f"L{fim:06d}",
                titulo,
            )
        except RespostaInvalidaError:
            continue
        resultados.append((linha.identificador, f"L{fim:06d}", titulo))
    # Algumas bulas concatenam duas apresentacoes com a mesma secao profissional.
    # Somente copias integralmente iguais apos normalizacao podem ser consolidadas.
    unicos: dict[str, tuple[str, str, str]] = {}
    for inicio, fim, titulo in resultados:
        a = numero_linha(inicio)
        b = numero_linha(fim)
        texto = "\n".join(
            linha.texto_original for linha in documento.linhas[a - 1 : b - 1]
        )
        unicos.setdefault(normalizar_para_comparacao(texto), (inicio, fim, titulo))
    return list(unicos.values())


def ids_das_linhas(linhas: Sequence) -> set[str]:
    return {linha.identificador for linha in linhas}
