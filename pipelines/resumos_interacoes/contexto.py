"""Preparação conservadora apenas dos cards que serão gerados.

Não reidentifica relações nem altera os registros prontos. Um recorte só é
ampliado quando existe uma seção nominal única no texto literal da própria bula.
Tabelas sem associação segura entre colunas ficam para revisão da evidência.
"""

import re

from pipelines.identificacao_interacoes.normalizacao import mapear_texto, normalizar_alvo

# Rótulo "nome: ..." no meio do texto, depois do fim de uma frase ("... orais. lítio: ...").
ROTULO_INLINE = re.compile(r"(?<=[.;])[ \t]+([^\W\d_][^:\n.;!?]{0,59}?)[ \t]*:(?=\s)")
MARCADOR = r"(?:[•●▪◦‣*–\-]|[a-z]\))"
MARCADOR_LINHA = re.compile(r"(?m)^[ \t]*" + MARCADOR + r"[ \t]+(?=\S)")
ROTULO_ITEM = re.compile(
    r"(?m)(?:^[ \t]*(?:" + MARCADOR + r"[ \t]+)?|(?<=[.;])[ \t]+)"
    r"(?P<rotulo>[^\W\d_][^:\n.;!?]{0,99}?)[ \t]*:(?=\s)")
# Campos de registros e qualificadores: continuam o item, não nomeiam outro.
CAMPOS = frozenset(
    "efeito efeitos gravidade impacto intervencao exemplo exemplos ex mecanismo recomendacao "
    "recomendacoes conduta manejo nota notas observacao observacoes resultado resultados tabela "
    "aumento reducao diminuicao risco riscos significado documentacao inicio consequencia "
    "consequencias incluindo como referencia auc cmax cmin cep t concomitantemente importante "
    "atencao medicamento medicamentos exame planta alimento alimentos interacao nao mas porem "
    "entretanto portanto assim sendo alem tambem ou e".split())
CONTINUACAO = re.compile(
    r"^(?:isso|isto|esse|essa|esses|essas|este|esta|estes|estas|tal|tais|portanto|assim|logo|"
    r"consequentemente|nesse|nessa|nesses|nessas|neste|nesta|nestes|nestas|sendo assim|alem disso|"
    r"dessa forma|desta forma|por isso|da mesma forma|de modo similar|similarmente|tambem|ainda|"
    r"ambos|ambas|os dois|as duas|entretanto|no entanto|porem|contudo|todavia|mas|caso|se|"
    r"pode|podem|deve|devem|recomenda\w*)\b")


class RevisaoDaEvidencia(ValueError):
    """A entrada precisa de conferência; não é uma falha de inferência do modelo."""


def _profundidade(texto: str, posicao: int) -> int:
    return texto.count("(", 0, posicao) - texto.count(")", 0, posicao)


def completar_frase(evidencia: str, trecho: str | None, limite: int = 240) -> str:
    """Completa até o fim da frase uma evidência cortada, pelo trecho da mesma bula.

    Só quando o complemento é único e curto e não entra em outro item. Na
    dúvida devolve a evidência gravada, que nunca é alterada no banco.
    """
    if not evidencia or not trecho:
        return evidencia
    final = evidencia.rstrip()
    abertas = final.count("(") > final.count(")") or final.count("“") > final.count("”")
    if re.search(r"[.!?][\"”’)]*$", final) and not abertas:
        return evidencia
    complementos = set()
    for ocorrencia in re.finditer(re.escape(evidencia), trecho):
        resto = trecho[ocorrencia.end():ocorrencia.end() + limite]
        corte = re.search(r"\n[ \t]*\n|\n[ \t]*" + MARCADOR + r"[ \t]|\n[ \t]*[^\W\d_][^:\n]{0,60}:\s", resto)
        resto = resto[:corte.start()] if corte else resto
        fim = None
        for ponto in re.finditer(r"[.!?][\"”’)]*(?=\s|$)", resto):
            completo = evidencia + resto[:ponto.end()]
            anterior = re.search(r"(?:^|\W)(\w+)$", resto[:ponto.start()])
            if (completo.count("(") == completo.count(")") and completo.count("“") <= completo.count("”")
                    and not (anterior and len(anterior[1]) == 1 and anterior[1].isalpha())):
                fim = ponto.end()
                break
        if fim is None:
            return evidencia
        complementos.add(resto[:fim])
    return evidencia + complementos.pop() if len(complementos) == 1 else evidencia


def _nomes_do_foco(alvo, sinonimos) -> list[str]:
    from pipelines.identificacao_interacoes.resolucao import forma_sem_sal

    alvos = [alvo] if isinstance(alvo, str) else list(alvo or ())
    nomes = []
    for nome in alvos:
        normal = normalizar_alvo(nome)
        extras = sinonimos.get(nome, ()) if isinstance(sinonimos, dict) else sinonimos
        for forma in [normal, forma_sem_sal(normal), *(normalizar_alvo(s) for s in extras)]:
            if forma and forma not in nomes:
                nomes.append(forma)
    return nomes


def _cita(trecho: str, nomes: list[str]) -> bool:
    from pipelines.identificacao_interacoes.validacao import mencoes_do_alvo

    normal = mapear_texto(trecho, unir_hifenizacao=True).texto
    return any(mencoes_do_alvo(normal, nome) for nome in nomes)


def _itens(texto: str) -> list[tuple[int, int, bool]]:
    """Itens "nome: efeito" e marcadores "•"/"-": (início, fim, cabeçalho)."""
    inicios = {m.start(): False for m in MARCADOR_LINHA.finditer(texto)}
    for m in ROTULO_ITEM.finditer(texto):
        rotulo = m["rotulo"]
        palavras = mapear_texto(rotulo).texto.split()
        if not palavras or len(palavras) > 14 or rotulo.count("(") != rotulo.count(")") or _profundidade(texto, m.start()):
            continue
        linha = texto.rfind("\n", 0, m.start("rotulo")) + 1
        no_inicio = not texto[linha:m.start("rotulo")].strip(" \t•●▪◦‣*–-") or re.fullmatch(
            r"[ \t]*" + MARCADOR + r"[ \t]+", texto[linha:m.start("rotulo")])
        if no_inicio:
            anterior = texto[:linha].rstrip()
            ultima = anterior[anterior.rfind("\n") + 1:]
            if anterior and not re.search(r"[.;:!?)”\"]$", anterior) and re.search(r"[a-zà-ú]", ultima):
                continue
        resto_linha = texto[m.end():texto.find("\n", m.end()) if "\n" in texto[m.end():] else len(texto)]
        cabecalho = not resto_linha.strip()
        if palavras[0] in CAMPOS and not cabecalho:
            continue
        inicio = linha if no_inicio else m.start("rotulo")
        inicios[inicio] = inicios.get(inicio, False) or cabecalho
    posicoes = sorted(inicios)
    return [(p, posicoes[i + 1] if i + 1 < len(posicoes) else len(texto), inicios[p])
            for i, p in enumerate(posicoes)]


def _frases(texto: str) -> list[tuple[int, int]]:
    limites, inicio = [], 0
    for m in re.finditer(r"[.!?][\"”’)]*\s+(?=[A-ZÀ-Ú•\-])", texto):
        anterior = re.search(r"(\w+)[.!?][\"”’)]*$", texto[inicio:m.start() + 1])
        if _profundidade(texto, m.start()) or (anterior and len(anterior[1]) == 1):
            continue
        limites.append((inicio, m.end()))
        inicio = m.end()
    limites.append((inicio, len(texto)))
    return [(a, b) for a, b in limites if texto[a:b].strip()]


def _juntar(texto: str, partes: list[tuple[int, int]]) -> str:
    blocos = []
    for a, b in sorted(partes):
        if blocos and a <= blocos[-1][1]:
            blocos[-1] = (blocos[-1][0], max(b, blocos[-1][1]))
        else:
            blocos.append((a, b))
    return "\n".join(texto[a:b].strip() for a, b in blocos)


def _recorte(texto: str, nomes: list[str]) -> str | None:
    from pipelines.identificacao_interacoes.contexto_evidencia import blocos_estruturados, efeito_do_bloco

    campo = r"(?:gravidade|impacto cl[ií]nico|interven[çc][ãa]o|efeitos?\s+(?:d[ae]\s+)?intera[çc][ãa]o)"
    if efeito_do_bloco(texto) or re.search(r"(?im)^[ \t]*(?:" + MARCADOR + r"[ \t]*)?" + campo + r"\s*:", texto):
        # Registros "Medicamentos:/Efeito da interação:": a lista e o efeito são uma unidade.
        blocos = [(a, b) for a, b in blocos_estruturados(texto) if _cita(texto[a:b], nomes)]
        return _juntar(texto, blocos) if blocos else None
    if sum(bool(re.search(r"\S[ \t]{3,}\S", linha)) for linha in texto.splitlines()) >= 2:
        return None
    itens = _itens(texto)
    if itens:
        segmentos = ([(0, itens[0][0], texto[:itens[0][0]].rstrip().endswith(":"))]
                     if texto[:itens[0][0]].strip() else []) + itens
        escolhidos, cabecalho = [], None
        for a, b, e_cabecalho in segmentos:
            if e_cabecalho:
                cabecalho = (a, b)
            elif _cita(texto[a:b], nomes):
                escolhidos.extend([cabecalho, (a, b)] if cabecalho else [(a, b)])
        if not escolhidos or len(set(escolhidos)) == len(segmentos):
            return None
        return _juntar(texto, list(set(escolhidos)))
    frases = _frases(texto)
    citam = [_cita(texto[a:b], nomes) for a, b in frases]
    if not any(citam) or all(citam):
        return None
    escolhidas = []
    for i, (a, b) in enumerate(frases):
        inicio = mapear_texto(texto[a:b]).texto
        if citam[i]:
            if i and not citam[i - 1] and CONTINUACAO.match(inicio):
                return None
            escolhidas.append((a, b))
        elif escolhidas and escolhidas[-1][1] == a and CONTINUACAO.match(inicio):
            escolhidas.append((a, b))
    return _juntar(texto, escolhidas)


def referencia_do_alvo(texto: str, alvo, origem: str, sinonimos=()) -> str:
    """Item ou orações do bloco literal que tratam do alvo (ou dos alvos do bloco).

    Referência interna, igual para o prompt e para a validação. Se o recorte
    for ambíguo, devolve o bloco inteiro. Evidência e entrada não mudam.
    """
    from .prompts import preparar_fonte_do_alvo

    fonte = preparar_fonte_do_alvo(texto, origem)
    nomes = _nomes_do_foco(alvo, sinonimos)
    recorte = _recorte(fonte, nomes) if nomes else None
    return preparar_fonte_do_alvo(recorte or fonte, origem, alvo)


def recuperar_secao(texto: str, evidencia: str, alvo: str) -> str:
    """Recupera `alvo: ...` até o próximo rótulo, sem unir parágrafos distantes.

    O recorte legado deve ocorrer uma única vez e sobrepor essa seção. Na dúvida
    devolve o original. A evidência resultante continua sendo uma fatia literal.
    """
    if not texto or not evidencia or texto.count(evidencia) != 1:
        return evidencia
    inicio = texto.index(evidencia)
    fim = inicio + len(evidencia)
    rotulos = sorted([*re.finditer(r"(?m)^[ \t]*([^\n:]{2,80}):[ \t]*", texto), *ROTULO_INLINE.finditer(texto)],
                     key=lambda m: m.start(1))
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
