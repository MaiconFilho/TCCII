"""Barreiras de formato e de tom. Não conferem fidelidade clínica: o card é uma
explicação do LLM com as próprias palavras, e a evidência literal fica em "Ver evidência"."""

import json
import re
from collections import Counter
from difflib import SequenceMatcher

from pipelines.identificacao_interacoes.normalizacao import mapear_texto
from pipelines.identificacao_interacoes.validacao import GRAVIDADE

from .prompts import (SEM_DETALHE, sem_rotulo_intercalado, preparar_fonte_do_alvo,
                      proteger_remissoes, grafias_dos_exemplos, sujeito_do_estudo_comparativo,
                      sujeito_conhecido_por_causar)
from pipelines.identificacao_interacoes.contexto_evidencia import efeito_do_bloco

FALTA_INFO = re.compile(
    r"\bdesconhecid\w*|\bnao\s+(?:se\s+sabe|e\s+conhecid\w*|ha\s+(?:\w+\s+){0,2}informac\w*|"
    r"(?:tem|ha)\s+sido\s+relatad\w*|(?:foi|foram|e|sao)\s+(?:estudad\w*|investigad\w*|relatad\w*|documentad\w*))"
    r"|\bnao\s+(?:ha|existem|tem|dispomos\s+de)\s+(?:\w+\s+){0,2}(?:dados|informacoes|estudos|evidencias|relatos)"
    r"|\bnao\s+(?:estudad\w*|investigad\w*|relatad\w*|documentad\w*)"
    r"|\bsem\s+(?:dados|informacoes|estudos)|\bfalt\w*\s+(?:dados|informacoes|estudos)")
SEM_INTERACAO = re.compile(
    r"\b(?:nao\s+(?:ha|houve|havera|existe\w*|ocorre\w*)|sem|ausencia\s+de)\s+(?:qualquer\s+)?interac\w*"
    r"|\bnao\s+interag\w*")
# Uma concentração não ter mudado não comprova ausência de toda interação nem
# segurança. A voz passiva da bula e a ativa do resumo são equivalentes só aqui.
SEM_ALTERACAO = re.compile(
    r"\bnao\s+(?:(?:foi|foram|e|sao|era|eram)\s+)?(?:afet\w*|influenci\w*|alter\w*)"
    r"|\bsem\s+(?:influencia|alterac\w*)"
    r"|\bnao\s+(?:foi|foram)\s+observad\w*\s+alterac\w*")
SEGURANCA = re.compile(r"\b(?:segur\w*|sem\s+riscos?)\b")
EFEITO = re.compile(
    r"\b(?:aument\w*|elev\w*|redu[cz]\w*|diminu\w*|potencializ\w*|inibi\w*|"
    r"risco\w*|efeito\w*\s+advers\w*|ritmo\s+cardiaco\s+irregular)\b")


class ReescritaTruncada(ValueError):
    """A saída terminou pelo limite de geração; não é falha de fidelidade."""


def validar_fundamentacao(texto: str, evidencia: str) -> None:
    """Ausência de contexto nunca vira uma afirmação de ausência de interação."""
    normal = mapear_texto(texto).texto.lower()
    fonte = mapear_texto(sem_rotulo_intercalado(evidencia)).texto.lower()
    efeito = mapear_texto(efeito_do_bloco(evidencia)).texto.lower()
    if texto.strip() == SEM_DETALHE:
        if EFEITO.search(fonte):
            raise ValueError("A evidência descreve um efeito; não afirme falta de informação. Resuma o efeito descrito.")
        return
    if FALTA_INFO.search(normal) and not FALTA_INFO.search(fonte):
        raise ValueError("Falta de estudos ou interação desconhecida não consta na evidência. Resuma somente o efeito descrito.")
    for padrao in (SEM_INTERACAO, SEM_ALTERACAO, SEGURANCA):
        if padrao.search(normal) and not padrao.search(fonte):
            raise ValueError("A evidência não sustenta negar a interação ou afirmar segurança.")
    if re.search(r"\b(?:pode|podem)\b", efeito) and not re.search(r"\b(?:pode|podem|possivel|possibilidade)\b", normal):
        raise ValueError("O efeito é uma possibilidade: mantenha 'pode' no resumo.")
    if "nasal" in efeito and "nasal" not in normal:
        raise ValueError("Preserve a via nasal descrita no efeito da interação.")


def validar_contexto_geral(texto: str, nome: str) -> None:
    """Cada parágrafo precisa se referir explicitamente à bula pesquisada."""
    origem = " ".join(mapear_texto(nome).texto.lower().split())
    if not origem:
        raise ValueError("Informe o medicamento pesquisado para validar o resumo geral.")
    for paragrafo in re.split(r"\n\s*\n", texto):
        normal = " ".join(mapear_texto(paragrafo).texto.lower().split())
        if not normal:
            continue
        if not re.search(r"(?<!\w)" + re.escape(origem) + r"(?!\w)", normal):
            raise ValueError(f"O parágrafo deve explicar as interações de {nome}, citando esse nome; não descreva outro fármaco isoladamente.")
        if re.search(r"\bquando interage com outros farmacos\b", normal):
            raise ValueError("O resumo geral descreve outro fármaco genericamente; explique a relação com o medicamento pesquisado.")

# Trechos das instruções que o modelo às vezes devolve no lugar do card. O 3B
# respondeu "O efeito descrito para nortriptilina é o mesmo que para amiodarona: '...'";
# no geral da Aerotrat, o 1.5B copiou "A tentativa anterior foi rejeitada." da correção.
ECO_DO_PROMPT = (
    "como modelo de", "voce e um especialista", "responda somente", "gere um resumo",
    "efeito descrito para", "card ja escrito", "trocando apenas o nome", "medicamento da bula:",
    "tipo do alvo", "alvo:", "em json", "tentativa anterior", "foi rejeitada",
    "corrija esse problema", "atencao:", "texto corrido", "nome generico", "nomes do trecho",
    "sem citar esses nomes", "escreva so sobre",
)
# Palavras que, no fim de uma linha, indicam frase quebrada pelo PDF, e não um rótulo.
CONECTORES = frozenset({
    "a", "o", "as", "os", "ao", "aos", "à", "às", "de", "da", "do", "das", "dos", "e", "ou",
    "em", "na", "no", "nas", "nos", "com", "por", "pela", "pelo", "pelas", "pelos", "para",
    "que", "como", "um", "uma", "se", "sem", "entre", "sobre",
})


def _rotulo(linha: str, seguinte: str) -> bool:
    """Linha curta sem pontuação seguida de maiúscula: título ou rótulo da bula
    ("Lítio", "Medicamentos que aumentam o risco de angioedema"), não quebra de frase."""
    ultima = re.findall(r"\w+", linha.lower())
    return (len(linha) <= 90 and not linha.endswith((".", ":", ";", ",", "!", "?", "-"))
            and bool(ultima) and ultima[-1] not in CONECTORES and seguinte[:1].isupper())


def limpar(texto: str) -> str:
    """Junta as linhas do PDF numa frase pontuada, sem marcadores de lista (•, - e *).

    Antes, "INTERAÇÕES MEDICAMENTOSAS\\nO salbutamol..." e "Lítio\\nAumentos..." viravam
    "INTERAÇÕES MEDICAMENTOSAS O salbutamol..." e "Lítio Aumentos...": o título sai e o
    rótulo ganha ponto final.
    """
    linhas = [re.sub(r"^[-–*]\s*", "", linha.strip()) for linha in texto.replace("•", " ").splitlines()]
    linhas = [linha for linha in linhas if linha]
    # Título de seção em caixa alta no início não faz parte da frase.
    while len(linhas) > 1 and not re.search(r"[a-zà-ú]", linhas[0]) and re.search(r"[A-ZÀ-Ú]{3}", linhas[0]):
        linhas.pop(0)
    # Rótulo inicial que a frase já nomeia ("Ciclosporina" + "... com ciclosporina.") é
    # redundante no card, cujo título já traz o medicamento; "Baclofeno" + "Aumento do
    # efeito anti-hipertensivo." fica, porque a frase sozinha não diz qual é.
    if len(linhas) > 1 and _rotulo(linhas[0], linhas[1]):
        resto = mapear_texto(" ".join(linhas[1:])).texto.lower()
        nomes = [p for p in re.findall(r"[a-z0-9]+", mapear_texto(linhas[0]).texto.lower()) if len(p) >= 3]
        if nomes and all(re.search(r"\b" + p + r"\b", resto) for p in nomes):
            linhas.pop(0)
    partes = [linha + "." if seguinte and _rotulo(linha, seguinte) else linha
              for linha, seguinte in zip(linhas, linhas[1:] + [""])]
    texto = re.sub(r"[\s,;:]+$", "", " ".join(" ".join(partes).split()))
    return texto + "." if texto and texto[-1] not in ".!?" else texto


def recuperar_truncado(conteudo: str) -> str | None:
    """Parágrafo cortado pelo limite de tokens: fica até a última frase completa.

    O 1.5B tende a encadear todas as linhas do grupo e passar do limite."""
    inicio = re.match(r'\s*\{\s*"texto"\s*:\s*"', conteudo)
    if not inicio:
        return None
    parcial = conteudo[inicio.end():]
    fim = max(parcial.rfind(". "), parcial.rfind(".\n"))
    if fim < 0:
        return None
    try:
        return json.loads('"' + parcial[:fim + 1] + '"', strict=False)
    except ValueError:
        return None


def grafia_da_fonte(texto: str, evidencia: str) -> str:
    """Devolve a acentuação da evidência a palavras dela ("mefenámico" → "mefenâmico").

    Só palavras com 6+ letras: nas curtas há pares distintos como "está"/"esta".
    """
    grafias = {}
    for palavra in re.findall(r"\w{6,}", evidencia):
        grafias.setdefault(mapear_texto(palavra).texto.lower(), palavra)

    def trocar(m):
        original = grafias.get(mapear_texto(m.group(0)).texto.lower())
        return original if original and original.lower() != m.group(0).lower() else m.group(0)
    return re.sub(r"\w{6,}", trocar, texto)


def validar_citacoes(texto: str, evidencia: str) -> None:
    """Aspas tipográficas são formatação; o conteúdo citado precisa estar na fonte.

    Aceita 'rash' quando a bula usa “rash”, sem aceitar uma citação inventada
    simplesmente porque a bula também contém algum caractere de aspas.
    """
    normal = mapear_texto(texto).texto
    fonte = mapear_texto(evidencia).texto
    fonte = " ".join(re.sub(r"[\"']", "", fonte).split())
    for citacao in re.finditer(r"(?<!\w)([\"'])(.+?)\1(?!\w)", normal):
        conteudo = " ".join(re.sub(r"[\"']", "", citacao.group(2)).split())
        if conteudo and not re.search(r"(?<!\w)" + re.escape(conteudo) + r"(?!\w)", fonte):
            raise ValueError("Resumo contém citação entre aspas ausente da evidência.")


def validar_resposta(resposta, evidencia: str, *, paragrafo: bool = False) -> str:
    """Card de uma interação ou um parágrafo do resumo geral (contra os cards do grupo)."""
    if resposta.truncada:
        recuperado = recuperar_truncado(resposta.conteudo) if paragrafo else None
        if recuperado is None:
            raise ValueError("Resposta do modelo truncada.")
        objeto = {"texto": recuperado}
    else:
        try:
            # strict=False: o GGUF às vezes escreve quebras de linha literais na string.
            objeto = json.loads(resposta.conteudo, strict=False)
        except (ValueError, TypeError) as erro:
            raise ValueError("Resposta do modelo não é JSON válido.") from erro
    if not isinstance(objeto, dict) or set(objeto) != {"texto"} or not isinstance(objeto["texto"], str):
        raise ValueError("Resposta fora do esquema texto.")
    # Antes da limpeza, que junta as linhas. No card, • vem da cópia literal da bula.
    marcadores = r"\d+[.)]|[-*•]" if paragrafo else r"\d+[.)]|[-*]"
    if (re.search(rf"(?m)^\s*(?:{marcadores})\s", objeto["texto"])
            or len(re.findall(r"\b\d{1,2}[.)]\s+[A-ZÀ-Ú]", objeto["texto"])) >= 2):
        raise ValueError("Resumo enumera itens; escreva texto corrido, sem listas ou numeração.")
    texto = grafia_da_fonte(limpar(objeto["texto"]), evidencia)
    # Só no parágrafo: no card, "Mulheres em uso de varfarina: ..." pode ser a redação da bula.
    if paragrafo and re.match(r"[^.:]{3,40}:\s", texto):
        raise ValueError("Parágrafo começa com título; escreva texto corrido.")
    minimo, maximo = (60, 1200) if paragrafo else (40, 600)
    if not minimo <= len(texto) <= maximo:
        raise ValueError(f"Resumo vazio ou fora do tamanho permitido ({minimo}–{maximo}).")
    normal = mapear_texto(texto).texto.lower()
    fonte = mapear_texto(evidencia).texto.lower()
    if re.search(r'[{}\[\]`]|<\|.*?\|>|(?:system|assistant|user)\s*:|"texto"\s*:', texto, re.I):
        raise ValueError("Resumo contém marcação ou lixo de prompt.")
    if any(t in normal for t in ECO_DO_PROMPT):
        raise ValueError("Resumo repete instruções do prompt.")
    validar_citacoes(texto, evidencia)
    # Nomes genéricos dos exemplos do prompt; o 1.5B escreveu "potencializar o efeito de X".
    genericos = set(re.findall(r"\b[XBCD]\b", texto)) - set(re.findall(r"\b[XBCD]\b", evidencia))
    if genericos:
        raise ValueError(f"Resumo usa o nome genérico dos exemplos ({', '.join(sorted(genericos))}); "
                         "use os nomes do trecho.")
    # Tom: o pedido original veta certeza absoluta, gravidade inventada e alarmismo.
    for termo in ("com certeza", "sempre", "nunca", "certamente", "garantido"):
        if re.search(r"\b" + termo + r"\b", normal) and not re.search(r"\b" + termo + r"\b", fonte):
            raise ValueError(f"Certeza não sustentada pela evidência: {termo}.")
    for correspondencia in GRAVIDADE.finditer(normal):
        if correspondencia.group(1)[:5] not in fonte:
            raise ValueError(f"O resumo usa '{correspondencia.group(1)}', gravidade ausente da evidência.")
    # No parágrafo, cards parecidos repetem expressões ("níveis plasmáticos de nebivolol")
    # de forma legítima; laço degenerado repete frases inteiras.
    tamanho = 10 if paragrafo else 6
    palavras = normal.split()
    sequencias = [tuple(palavras[i:i + tamanho]) for i in range(max(0, len(palavras) - tamanho + 1))]
    if sequencias and max(sequencias.count(s) for s in set(sequencias)) >= 3:
        raise ValueError("Repetição excessiva no resumo.")
    if paragrafo and texto == SEM_DETALHE:
        raise ValueError("O parágrafo do resumo geral não pode ser a frase de falta de informação.")
    validar_fundamentacao(texto, evidencia)
    return texto


class RevisaoDaReescrita(ValueError):
    """Inconsistência a conferir; nenhuma substituição do original é permitida."""


def _normal_simples(texto):
    """Equivalências curtas de redação, não equivalências entre efeitos clínicos."""
    normal = mapear_texto(sem_rotulo_intercalado(texto), unir_hifenizacao=True).texto
    # Formulação encontrada na fonte: "excreção renal dos níveis de X".
    # Nessa oração, o que se descreve é a eliminação de X. Não é, por si,
    # uma segunda afirmação sobre aumento de sua concentração no sangue.
    # A normalização é só de comparação; a evidência permanece literal.
    normal = re.sub(r"\b(excrecao renal|eliminacao pelos rins|depuracao renal)\s+dos niveis de\b",
                    r"\1 de", normal)
    for padrao, troca in [
        (r"\b(?:concomitantemente|concomitantes?|simultaneamente)\b", "ao mesmo tempo"),
        (r"\bconcentrac\w*\s+plasmatic\w*", "quantidade no sangue"),
        (r"\b(?:niveis?|quantidade|concentrac\w*)\s+(?:do medicamento\s+)?no sangue", "quantidade no sangue"),
        (r"\bhepatic\w*\b", "do figado"),
        (r"\bhipotensao\b|\bpressao (?:arterial )?baixa\b", "pressao arterial baixa"),
        (r"\bhipercalemia\b", "niveis altos de potassio no sangue"),
        (r"\bhipocalemia\b", "niveis baixos de potassio no sangue"),
        (r"\b(?:niveis elevados|concentracao elevada) de potassio no sangue\b", "niveis altos de potassio no sangue"),
        (r"\bletais\b", "letal"),
        (r"\bfatais\b", "fatal"),
        (r"\bcefaleias?\b|\bdores de cabeca\b", "dor de cabeca"),
        (r"\baliviar a dor\b", "analgesico"),
        (r"\b(?:medicamento|farmaco)\s+para\s+(?:a\s+)?dor\b", "analgesico"),
        (r"\bmais (?:devagar|lentamente|lent[oa]s?)\b", "com velocidade reduzida"),
        (r"\bmais (?:rapid[oa]s?|rapidamente)\b", "com velocidade aumentada"),
        (r"\bum ano\b", "1 ano"),
        (r"\b(?:e possivel que|existe a possibilidade de)\b", "pode"),
    ]:
        normal = re.sub(padrao, troca, normal)
    normal = re.sub(r"(?<=\d)\.(?=\d)", ",", normal)
    return re.sub(r"\b(\d+(?:[.,]\d+)?)\s+(mg|g|mcg|ug|µg|ml|ui)\b", r"\1\2", normal)


SUBTIPO_RECEPTOR = re.compile(r"\b(?:alfa|alpha|beta|gama|gamma|delta)\s*-\s*\d+\b")
FREQUENCIA = re.compile(
    r"\b(?:(?P<vezes>uma|duas|tres|quatro|\d+)\s+vez(?:es)?\s+(?:ao|por)\s+(?P<periodo>dia|semana)"
    r"|a\s+cada\s+(?P<intervalo>\d+(?:[.,]\d+)?)\s*(?P<unidade>horas?|h|dias?)"
    r"|(?P<diaria>diariamente|doses?\s+diarias?))\b")
LIMITES_NUMERICOS = [
    ("ge", re.compile(r"(?<!\w)(?:pelo menos|no minimo|>=|≥)\s*(\d+(?:[.,]\d+)?)")),
    ("le", re.compile(r"(?<!\w)(?:ate|no maximo|<=|≤)\s*(\d+(?:[.,]\d+)?)")),
    ("gt", re.compile(r"(?<!\w)(?:maiores? que|superiores? a|acima de|mais de|>(?!=))\s*(\d+(?:[.,]\d+)?)")),
    ("lt", re.compile(r"(?<!\w)(?:menores? que|inferiores? a|abaixo de|menos de|<(?!=))\s*(\d+(?:[.,]\d+)?)")),
]
RESTRICAO_USO = re.compile(
    r"\bnao\s+(?:se\s+)?(?:dev\w*|pod\w*|use\w*|us[ae]r?|tom\w*|administr\w*|receit\w*|combin\w*|"
    r"mistur\w*|associ\w*|prescrev\w*|inger\w*)\b|\b(?:contraindicad\w*|proibid\w*)\b")


def _frequencias(texto):
    saida = Counter()
    numerais = {"uma": "1", "duas": "2", "tres": "3", "quatro": "4"}
    for m in FREQUENCIA.finditer(texto):
        if m["diaria"]:
            chave = "1/dia"
        elif m["vezes"]:
            chave = numerais.get(m["vezes"], m["vezes"]) + "/" + m["periodo"]
        else:
            unidade = "dia" if m["unidade"].startswith("dia") else "h"
            chave = "cada " + m["intervalo"].replace(".", ",") + unidade
        saida[chave] += 1
    return saida


def _limites_numericos(texto):
    return {(tipo, m[1].replace(".", ",")) for tipo, padrao in LIMITES_NUMERICOS for m in padrao.finditer(texto)}


def _texto_efeito(texto):
    normal = _normal_simples(efeito_do_bloco(texto) or texto)
    normal = normal.replace("↓", " reducao ").replace("↑", " aumento ")
    # Retardar a eliminação descreve menor velocidade de eliminação.
    # Não interpreta "liberação retardada" como direção de uma interação.
    normal = re.sub(r"\bretard\w*\s+(?:(?:a|da|de)\s+)?(?:eliminacao|excrecao)\b",
                    lambda m: m[0] + " com velocidade reduzida", normal)
    # Quantidade eliminada: "os rins eliminam menos" expressa redução,
    # sem exigir que a saída use o substantivo técnico "diminuição".
    # Não transforma limites como "eliminar menos de 15 mg" em direções.
    normal = re.sub(r"\b(elimin\w*|excret\w*|retir\w*|remov\w*)\s+(menos|mais)\b(?!\s+de\b)",
                    lambda m: m[1] + " em quantidade " + ("reduzida" if m[2] == "menos" else "aumentada"), normal)
    # "Inibidores da ECA/MAO" nomeia classes na lista, não afirma que
    # a interação do alvo reduz o efeito de outro medicamento.
    normal = re.sub(r"\binibidor(?:a|es|as)?\b", "classe", normal)
    for _, padrao in LIMITES_NUMERICOS:
        normal = padrao.sub("limite numerico", normal)
    return re.sub(r"\b(?:doses?\s+(?:(?:mais|menos)\s+)?(?:baix[ao]s?|alt[ao]s?|elevad[oa]s?|menores?|maiores?|reduzid[oa]s?|aumentad[oa]s?)|"
                  r"(?:baixas?|menores?|maiores?)\s+doses?)\b", "dose qualificada", normal)


def _autoriza_uso(texto):
    padrao = r"\b(?:pode|podem|podemos)\s+(?:ser\s+)?(?:usar|usad\w*|utilizar|utilizad\w*|administrar|administrad\w*|tomar|tomad\w*|associar|associad\w*|combinar|combinad\w*)\b"
    return any(not re.search(r"\bnao\s*$", texto[:m.start()]) for m in re.finditer(padrao, texto))


def _subtipos_receptores(texto):
    return {re.sub(r"\s+", "", m.group()) for m in SUBTIPO_RECEPTOR.finditer(texto)}


def _numeros_clinicos(texto):
    # "beta-2" identifica uma classe; não é uma dose da interação com outro
    # integrante da lista. Doses, contagens, percentuais e intervalos continuam.
    subtipos = [m.span() for padrao in (SUBTIPO_RECEPTOR, FREQUENCIA) for m in padrao.finditer(texto)]
    # Ano em referência bibliográfica não é uma quantidade clínica. Uma dose
    # como "1997 mg" continua sendo conferida, mesmo entre parênteses.
    subtipos.extend(m.span() for m in re.finditer(r"\([a-z][a-z .'-]*,\s*(?:19|20)\d{2}[a-z]?\)", texto))
    # Títulos numerados e códigos de estudo identificam o documento, não
    # uma dose. Mantém qualquer número associado a unidade clínica.
    subtipos.extend(m.span() for m in re.finditer(
        r"\b\d{1,2}\s*[-.)]\s*interacoes medicamentosas\b|\b(?:bm|protocolo)\s+\d{3,}\b", texto))
    # Mês/ano de revisão no rodapé não é dose ou resultado do estudo.
    subtipos.extend(m.span() for m in re.finditer(
        r"\bprofissional\s*[-–—]\s*(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}\b", texto))
    # A remissão inteira pode listar vários itens: "ver seções 3, 4 e 5".
    # Reconhece também subitens após a normalização de pontos decimais.
    numero_secao = r"\d{1,2}(?:[.,]\d+)*"
    referencias = [m.span() for m in re.finditer(
        r"\b(?:veja|vide|ver|consulte)\s+(?:(?:os?|as?)\s+)?"
        r"(?:item|itens|subitem|subitens|secao|secoes|subsecao|subsecoes)\s+" + numero_secao +
        r"(?:\s*(?:[,;–-]|\be\b|\ba\b)\s*" + numero_secao + r")*\b", texto)]
    # Uma unidade clínica explícita não vira referência documental apenas
    # por aparecer depois de uma remissão ou junto de uma vírgula.
    from pipelines.identificacao_interacoes.validacao import QUANTIDADE
    quantidades = [m.span() for m in QUANTIDADE.finditer(texto)]
    quantidades.extend(m.span() for m in re.finditer(
        r"\d+(?:[.,]\d+)?\s*(?:%|anos?\b|meses?\b|semanas?\b|dias?\b|horas?\b|minutos?\b|vezes\b)", texto))
    return {m.group().replace(".", ",") for m in re.finditer(r"(?<!\w)\d+(?:[.,]\d+)?", texto)
            if not any(inicio <= m.start() < fim for inicio, fim in subtipos)
            and (not any(inicio <= m.start() < fim for inicio, fim in referencias)
                 or any(inicio <= m.start() < fim for inicio, fim in quantidades))}


def _assinaturas_relacionais(texto, entrada):
    """Confere construções diretas ativas/passivas; não é um analisador clínico.

    Ex.: A aumenta a ação de B / a ação de B é aumentada por A. Não usa a
    resposta do modelo como julgamento de sua própria fidelidade.
    """
    from pipelines.identificacao_interacoes.resolucao import substancias, forma_sem_sal

    aliases = {mapear_texto(entrada["origem"]).texto: "origem"}
    for principio in substancias(entrada.get("principios_ativos")):
        aliases[principio] = "origem"
        aliases[forma_sem_sal(principio)] = "origem"
    for evidencia in entrada["evidencias"]:
        alvo = mapear_texto(evidencia["alvo"]).texto
        aliases.setdefault(alvo, alvo)
        aliases.setdefault(forma_sem_sal(alvo), alvo)
    nomes = "|".join(re.escape(n) for n in sorted(aliases, key=len, reverse=True) if n)
    saida = set()
    for frase in re.split(r"[.;]\s+|\n", _normal_simples(texto)):
        ocorrencias = list(re.finditer(r"(?<!\w)(?:" + nomes + r")(?!\w)", frase)) if nomes else []
        for efeito in re.finditer(r"\b(?:aument\w*|elev\w*|potencializ\w*|intensific\w*|redu[cz]\w*|diminu\w*|inib\w*)", frase):
            anteriores = [n for n in ocorrencias if n.end() <= efeito.start()]
            posteriores = [n for n in ocorrencias if n.start() >= efeito.end()]
            if not anteriores or not posteriores:
                continue
            esquerda, direita = anteriores[-1], posteriores[0]
            antes_efeito = " ".join(re.sub(r"[,()]", " ", frase[max(0, efeito.start() - 45):efeito.start()]).split())
            negado = bool(re.search(r"\b(?:nao|sem)\b(?:\s+\w+){0,2}$", antes_efeito))
            if re.match(r"\s*por\b", frase[efeito.end():]):
                # "A atividade de A pode ser aumentada por B, C...": todos
                # os integrantes da mesma lista têm o papel de agente, até
                # aparecer outro efeito. Não inverte o sujeito da passiva.
                for nome in posteriores:
                    meio = frase[efeito.end():nome.start()]
                    if re.search(r"\b(?:aument\w*|redu[cz]\w*|diminu\w*|inib\w*|potencializ\w*)\b", meio):
                        break
                    if aliases[nome.group()] != aliases[esquerda.group()]:
                        direcao = "reducao" if re.match(r"redu|diminu|inib", efeito.group()) else "aumento"
                        saida.add((aliases[nome.group()], aliases[esquerda.group()], direcao, negado))
                continue
            if direita.start() - efeito.end() > 120 or efeito.start() - esquerda.end() > 120:
                continue
            sujeito = frase[:efeito.start()].rsplit(",", 1)[-1]
            if (re.search(r"\b(?:uso|administracao|combinacao|associacao)\b", sujeito)
                    and re.search(r"\b(?:concomitante|conjunt\w*|com|e)\b", sujeito)):
                # O sujeito é o uso conjunto, não necessariamente o último
                # medicamento antes do verbo. Não inventa um agente individual.
                continue
            agente, afetado = aliases[esquerda.group()], aliases[direita.group()]
            entre = frase[efeito.end():direita.start()]
            if re.search(r"\bpor\s+(?:(?:o|a|os|as)\s+)?$", entre):
                agente, afetado = afetado, agente
            elif re.search(r"\bpor\b", entre):
                # Passiva seguida por classe/lista: o alvo não está junto do
                # verbo. Não infere que o sujeito da passiva seja o agente.
                continue
            elif entre.strip() not in {"", "a", "o", "as", "os"} and not (
                    re.search(r"\b(?:efeito|acao|atividade|nivel|niveis|quantidade|concentracao|cmax|cmin|auc|exposicao|metabolismo|absorcao|eliminacao|excrecao|depuracao|eficacia)\w*\b", entre)
                    and re.search(r"\b(?:de|da|do|das|dos)\s*$", entre)):
                # "Aumenta a pressão quando usado com Y" não diz que aumenta Y.
                continue
            if agente != afetado:
                direcao = "reducao" if re.match(r"redu|diminu|inib", efeito.group()) else "aumento"
                antes_efeito = " ".join(re.sub(r"[,()]", " ", frase[max(0, efeito.start() - 45):efeito.start()]).split())
                negado = bool(re.search(r"\b(?:nao|sem)\b(?:\s+\w+){0,2}$", antes_efeito))
                saida.add((agente, afetado, direcao, negado))
    return saida


def conferir_fidelidade(texto: str, referencia: str, entrada: dict, *, preservar=False, preservar_estudo=True) -> None:
    """Barreiras determinísticas: falhar pede revisão; passar não prova fidelidade."""
    from pipelines.identificacao_interacoes.validacao import DIRECOES, QUANTIDADE, VIA
    from pipelines.identificacao_interacoes.resolucao import substancias, forma_sem_sal

    referencia = preparar_fonte_do_alvo(referencia, entrada["origem"], entrada.get("alvo"))
    normal, fonte = _normal_simples(texto), _normal_simples(referencia)
    agente_fonte = sujeito_conhecido_por_causar(referencia)
    agente_saida = sujeito_conhecido_por_causar(texto)
    if agente_fonte and agente_saida and agente_fonte != agente_saida:
        raise RevisaoDaReescrita('O causador do efeito foi trocado. Preserve o sujeito da oração: ' + agente_fonte + '.')
    medida = r'\bconcentrac\w*\b|\bquantidade[^.;!?]{0,70}\b(?:sangue|plasma)\b'
    apoio_medida = medida + r'|\bniveis?\b|\b(?:auc|cmax|cmin)\b|\bhipercal\w*\b|\bhipocal\w*\b'
    if re.search(medida, normal) and not re.search(apoio_medida, fonte):
        raise RevisaoDaReescrita('Parâmetro de concentração acrescentado sem suporte: reescreva apenas o mecanismo informado, sem criar substâncias ou etapas adicionais.')
    if re.search(r'\bcom outros medicamentos\b', normal) and not re.search(r'\boutros medicamentos\b', fonte):
        raise RevisaoDaReescrita("Combinação generalizada: não acrescente 'com outros medicamentos'; mantenha a combinação expressa na fonte.")
    estudo = sujeito_do_estudo_comparativo(referencia)
    if estudo:
        redacao = sujeito_do_estudo_comparativo(texto)
        outra = re.escape(estudo["referencia_estrutural"])
        transfere_resultado = re.search(r"(?<!\w)" + outra + r"(?!\w)(?:,\s*[^,;.]+,)?\s+"
                                        r"(?:pode|podem)\s+ser\s+(?:administrad\w*|usad\w*)", normal)
        if ((redacao and redacao["substancia_estudada"] != estudo["substancia_estudada"])
                or transfere_resultado):
            raise RevisaoDaReescrita("Resultado atribuído à substância errada: o estudo descreve " +
                                    estudo["substancia_estudada"] + "; não transfira o resultado à referência estrutural.")
    palavras_novas = set(re.findall(r"\b\w{7,}\b", mapear_texto(texto).texto)) - set(re.findall(r"\w+", mapear_texto(referencia).texto))
    nomes = set(grafias_dos_exemplos(entrada))
    # A origem e os alvos já são conhecidos; não precisam ser adivinhados
    # pelo modelo, inclusive quando a oração usa apenas a classe (AINEs).
    fornecidos = [entrada["origem"], entrada.get("alvo")] + [e["alvo"] for e in entrada["evidencias"]]
    nomes.update(n for n in fornecidos if n and re.fullmatch(r"[^\W\d_]{7,}", n))
    for nome in nomes:
        grafia = mapear_texto(nome).texto
        for palavra in palavras_novas:
            alias_parentetico = re.search(r"\(\s*" + re.escape(palavra) + r"\s*\)", normal)
            limiar = 0.8 if nome in fornecidos and alias_parentetico else 0.86
            if palavra != grafia and palavra[:3] == grafia[:3] and SequenceMatcher(None, palavra, grafia).ratio() >= limiar:
                raise RevisaoDaReescrita(f"Grafia de nome alterada: {palavra}; copie {nome} exatamente como na fonte.")
    efeito, efeito_fonte = _texto_efeito(texto), _texto_efeito(referencia)
    if (re.search(r"\bclearance plasmatic\w*\b", fonte) and re.search(r"\batividade\b", fonte)
            and not re.search(r"\beficac\w*\b|\bbeneficio clinico\b|\bresposta clinica\b|\befeito terapeutico\b", fonte)
            and re.search(r"\bmais eficaz\b|\bmaior eficacia\b|\bmelhor tratamento\b", normal)):
        raise RevisaoDaReescrita("Não transforme atividade e redução da eliminação em ganho de eficácia clínica; descreva a retirada do sangue mais devagar.")
    inotropico = r"\binotropic\w*[ -]+negativ\w*"
    explicado = r"\benfraquec\w*\b[^.;!?]{0,60}\bcontrac\w*\b[^.;!?]{0,30}\bcoracao\b"
    if re.search(inotropico, fonte) and not (re.search(inotropico, normal) or re.search(explicado, normal)):
        raise RevisaoDaReescrita("Preserve o tipo de efeito: efeitos que enfraquecem as contrações do coração (inotrópicos negativos).")
    if re.search(r"\banticoagul\w*", normal) and not re.search(r"\banticoagul\w*", fonte):
        raise RevisaoDaReescrita("Não introduza 'anticoagulante' quando o trecho informa apenas atividade ou ação do medicamento.")
    if (re.search(r"\banticoagul\w*", fonte) and re.search(r"\bcoagul\w*", normal)
            and not re.search(r"\bcoagul\w*", fonte)):
        # Uma explicação nova de anticoagulação como coagulação muda o sentido.
        # Paráfrases desse termo precisam de revisão, sem inferir clínica aqui.
        raise RevisaoDaReescrita("Possível confusão entre anticoagulação e coagulação.")
    origem = _normal_simples(entrada["origem"])
    principios = {alias for p in substancias(entrada.get("principios_ativos"))
                  for alias in (p, forma_sem_sal(p)) if alias and alias != origem}
    for principio in principios:
        for agente, afetado in ((origem, principio), (principio, origem)):
            padrao = (r"(?<!\w)" + re.escape(agente) + r"\s+(?:(?:pode|podem|tambem|nao)\s+){0,3}"
                      r"(?:aument\w*|elev\w*|potencializ\w*|redu[cz]\w*|diminu\w*|inib\w*)\b"
                      r"[^.;\n]{0,60}\b(?:efeito|acao|atividade|concentracao)\w*\b[^.;\n]{0,30}"
                      r"(?<!\w)" + re.escape(afetado) + r"(?!\w)")
            if re.search(padrao, normal) and not re.search(padrao, fonte):
                raise RevisaoDaReescrita("Atribui efeito do medicamento sobre seu próprio princípio ativo sem suporte.")
    try:
        validar_fundamentacao(texto, referencia)
    except ValueError as erro:
        raise RevisaoDaReescrita(str(erro)) from erro
    for direcao, padrao in DIRECOES.items():
        if padrao.search(efeito) and not padrao.search(efeito_fonte):
            raise RevisaoDaReescrita(f"Afirma {direcao} sem suporte na referência.")
    if _autoriza_uso(normal) and not _autoriza_uso(fonte):
        raise RevisaoDaReescrita("O trecho não autoriza recomendar o uso conjunto.")
    if (re.search(r"\bdeve(?:-se)?\s+ter\s+cuidado\b", fonte)
            and re.search(r"\bpode(?:-se)?\s+ter\s+cuidado\b", normal)):
        raise RevisaoDaReescrita("Mantenha 'deve-se ter cuidado' ou 'é preciso ter cuidado'; não enfraqueça para 'pode-se ter cuidado'.")
    if RESTRICAO_USO.search(normal) and not RESTRICAO_USO.search(fonte):
        raise RevisaoDaReescrita("Precaução ou observação foi transformada em restrição de uso.")
    def pares_do_relato(t):
        pares = set()
        padrao = r"\binterac(?:ao|oes) advers\w*[^.;!?]{0,60}?\bentre\s+(.+?)\s+e\s+(.+?)(?=,|[.;!?]|$)"
        for m in re.finditer(padrao, t):
            nomes = [re.split(r"\b(?:pode|podem|deve|devem|foi|foram|sao|quando)\b", n)[0] for n in m.groups()]
            nomes = [re.sub(r"^(?:a|o|as|os)\s+", "", n.strip()).strip() for n in nomes]
            pares.add(tuple(sorted(nomes)))
        return pares
    relatos = pares_do_relato(fonte)
    if relatos and pares_do_relato(normal) - relatos:
        raise RevisaoDaReescrita("Mantenha os medicamentos do relato de interação adversa. O par citado no relato não pode ser trocado pelo par da orientação de cuidado.")
    if relatos and preservar:
        faltantes = {n for par in relatos for n in par
                     if n and not re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", normal)}
        if faltantes:
            raise RevisaoDaReescrita("Preserve explicitamente os nomes do relato: " + ", ".join(sorted(faltantes)) +
                                    ". Não substitua esses nomes por 'os dois medicamentos' da orientação de cuidado.")
    cuidado = re.search(r"\bdeve(?:-se)?\s+ter\s+cuidado\b", fonte)
    if cuidado and preservar:
        if not re.search(r"\b(?:cuidado|cautela|precauc\w*)\b", normal):
            raise RevisaoDaReescrita("A orientação de cuidado foi omitida; preserve 'deve-se ter cuidado'.")
        trecho_cuidado = re.split(r"[.!?;\n]", fonte[cuidado.end():], maxsplit=1)[0]
        citados = {p for p in principios | {origem} if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", trecho_cuidado)}
        if citados and not any(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", normal)
                               for p in citados | {origem}):
            raise RevisaoDaReescrita("Preserve o medicamento da orientação de cuidado: " + ", ".join(sorted(citados)) + ".")
    relacao_medidas = r"\bauc\b[^.!?]{0,120}\d+(?:[.,]\d+)?\s*%\s+(?:da|do)\s+(?:concentracao maxima|cmax)\b"
    if re.search(relacao_medidas, normal) and not re.search(relacao_medidas, fonte):
        raise RevisaoDaReescrita("AUC e Cmax são medidas distintas. Relate separadamente a variação de cada uma; AUC não é uma porcentagem de Cmax.")
    # Compara vínculos explícitos da tabela, sem escolher a medida por
    # proximidade quando a frase reúne mais de um nome de medida.
    medidas_tabela = {}
    for m in re.finditer(r"\b(auc(?:\d+|∞)?|cmax|cmin)\s*:\s*[↓↑]\s*(\d+(?:[.,]\d+)?\s*%)", fonte):
        medidas_tabela.setdefault(m[1], set()).add(re.sub(r"\s+", "", m[2]))
    marcas = list(re.finditer(r"\b(auc(?:\d+|∞)?|cmax|cmin)\b", normal))
    for i, marca in enumerate(marcas):
        esperados = medidas_tabela.get(marca[1], set())
        if len(esperados) != 1:
            continue
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(normal)
        trecho = re.split(r"[;!?]|\.(?!\d)", normal[marca.end():fim], maxsplit=1)[0]
        valor = re.search(r"\d+(?:[.,]\d+)?\s*%", trecho)
        if valor and re.sub(r"\s+", "", valor[0]) not in esperados:
            raise RevisaoDaReescrita("Percentual associado à medida errada: " + marca[1] + ". Preserve separadamente os valores da tabela.")
    perfil = r"\b(?:um|uma)\s+(?:medicamento|farmaco)\s+para\b"
    if re.search(perfil, normal) and not re.search(perfil, fonte):
        raise RevisaoDaReescrita("Não acrescente um perfil do alvo ('um medicamento para...'); descreva somente a interação informada.")
    oral = r"\b(?:oral|orais|pela boca)\b"
    if re.search(oral, normal) and not re.search(oral, fonte):
        raise RevisaoDaReescrita("Não acrescente via oral ou uso pela boca quando a fonte não informa essa via.")
    if re.search(r"\bcontraindic\w*|\bproibid\w*", normal) and not re.search(r"\bcontraindic\w*|\bproibid\w*", fonte):
        raise RevisaoDaReescrita("Precaução foi transformada em contraindicação.")
    # Grupos de redação comuns, sem tentar inferir conduta clínica. Uma nova
    # orientação precisa ter apoio explícito na referência, mesmo sendo útil.
    orientacoes = [r"\b(?:monitor\w*|acompanh\w*)\b", r"\b(?:suspend\w*|interromp\w*)\b",
                   r"\b(?:substitu\w*|troc\w*)\b", r"\b(?:ajust\w*|alter\w*)\s+(?:a\s+)?dose\w*\b",
                   r"\brecomend\w*\b"]
    for padrao in orientacoes:
        if re.search(padrao, normal) and not re.search(padrao, fonte):
            raise RevisaoDaReescrita("Orientação nova sem suporte na referência.")
    for termo in GRAVIDADE.finditer(normal):
        if termo.group(1)[:5] not in fonte:
            raise RevisaoDaReescrita("Gravidade sem suporte na referência: " + termo.group(1))
    for termo in re.findall(r"\b(?:tontur\w*|nause\w*|vomit\w*|convuls\w*|toxic\w*|"
                            r"sangr\w*|hemorrag\w*|tromb\w*|arritmi\w*|morte\w*|sonolenc\w*|analgesic\w*)\b", normal):
        if termo[:5] not in fonte:
            raise RevisaoDaReescrita("Efeito ou sintoma sem suporte: " + termo)
    numeros = _numeros_clinicos
    if set(numeros(normal)) - set(numeros(fonte)):
        raise RevisaoDaReescrita("Quantidade ou dose sem suporte na referência.")
    if _subtipos_receptores(normal) - _subtipos_receptores(fonte):
        raise RevisaoDaReescrita("Subtipo de receptor sem suporte na referência.")
    assinaturas = _assinaturas_relacionais(texto, entrada)
    apoio = _assinaturas_relacionais(referencia, entrada)
    for agente, afetado, direcao, negado in assinaturas - apoio:
        if any({a, b} == {agente, afetado} for a, b, _, _ in apoio):
            raise RevisaoDaReescrita("Direção do efeito ou de quem afeta quem diverge da referência.")
    if preservar:
        # Uma associação explícita não pode virar o efeito apenas do
        # medicamento entre parênteses, omitindo a substância da própria bula.
        if re.search(r"\(\s*associad[oa]\b", referencia, re.I):
            proprios = {p for p in principios if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", fonte)}
            if proprios and not any(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", normal)
                                    for p in proprios | {origem}):
                raise RevisaoDaReescrita("Preserve o medicamento da bula na associação: " +
                                        ", ".join(sorted(proprios)) + "; não atribua o efeito apenas ao outro componente.")
        estudo = r"\b(?:estudos?|ensaios?|pesquisas?)\b"
        if re.search(estudo, normal) and not re.search(estudo, fonte):
            raise RevisaoDaReescrita("A reescrita acrescentou contexto de estudo que não estava no resumo original.")
        if preservar_estudo and re.search(estudo, fonte) and not re.search(estudo, normal):
            raise RevisaoDaReescrita("Preserve o contexto de estudo: descreva o resultado observado, sem transformar em orientação de uso.")
        if re.search(r"\bentre\b", fonte) and re.search(r"\bhoras?\b", fonte):
            for ordem in ("antes", "depois", "apos"):
                if re.search(r"\b" + ordem + r"\b", normal) and not re.search(r"\b" + ordem + r"\b", fonte):
                    raise RevisaoDaReescrita("Preserve o intervalo entre os medicamentos sem inventar qual vem antes ou depois.")
        indicacao = r"\b(?:usad\w*|utilizad\w*|indicad\w*|prescrit\w*|empregad\w*)\s+(?:para|no|na|em|nos|nas)\s+(?:o\s+|a\s+|os\s+|as\s+)?(?:tratar|tratament\w*|combater|controlar|prevenir)\b"
        if re.search(indicacao, normal) and not re.search(indicacao, fonte):
            raise RevisaoDaReescrita("A reescrita acrescentou uma indicação de tratamento ausente do original.")
        if RESTRICAO_USO.search(fonte) and not RESTRICAO_USO.search(normal):
            raise RevisaoDaReescrita("A reescrita alterou uma restrição explícita do original.")
        if set(_frequencias(fonte)) - set(_frequencias(normal)):
            esperadas = list(dict.fromkeys(m.group() for m in FREQUENCIA.finditer(fonte)))
            raise RevisaoDaReescrita("Frequência de administração omitida ou alterada. Preserve: " + ", ".join(esperadas) + ".")
        dose_unica = r"\b(?:doses?|administrac(?:ao|oes)) unicas?\b|\buma unica (?:dose|administracao)\b"
        if re.search(dose_unica, fonte) and not re.search(dose_unica, normal):
            raise RevisaoDaReescrita("Preserve o regime de dose única; não generalize o resultado para uso contínuo.")
        if set(_limites_numericos(fonte)) - set(_limites_numericos(normal)):
            raise RevisaoDaReescrita("Limite numérico de dose ou condição omitido ou alterado.")
        medidas = {
            "Cmax": r"\bcmax\b|\b(?:concentrac\w*|quantidade|nive\w*)\s+(?:\w+\s+){0,2}maxim\w*",
            "Cmin": r"\bcmin\b|\b(?:concentrac\w*|quantidade|nive\w*)\s+(?:\w+\s+){0,2}minim\w*",
            "AUC": r"\bauc\b|\barea sob a curva\b",
        }
        for medida, padrao in medidas.items():
            if re.search(padrao, fonte) and not re.search(padrao, normal):
                # A própria evidência pode definir a sigla por extenso.
                # Preservar "exposição sistêmica" nesse caso não omite AUC
                # nem autoriza substituir Cmax/Cmin por exposição genérica.
                if (medida == "AUC" and re.search(r"\bexposicao\s+sistemica\s*\(\s*auc\s*\)", fonte)
                        and re.search(r"\bexposicao\s+sistemica\b", normal)):
                    continue
                raise RevisaoDaReescrita("Medida farmacocinética omitida ou generalizada: " + medida)
        quantidade_sangue = r"\b(?:concentrac\w*|niveis)\b|\bquantidade\b.{0,60}\bsangue\b"
        if re.search(quantidade_sangue, fonte):
            if not re.search(quantidade_sangue, normal):
                raise RevisaoDaReescrita("A informação sobre concentração ou níveis foi substituída ou omitida.")
            clinico = r"\b(?:eficacia|efeito|acao|atividade)\b"
            if re.search(clinico, normal) and not re.search(clinico, fonte):
                raise RevisaoDaReescrita("Aumento de concentração não sustenta acrescentar efeito ou eficácia.")
        indicio = r"\b(?:parece|parecem|pareceu|pareceram|aparentemente|sugere|sugerem|sugeriu|sugeriram)\b"
        if re.search(indicio, fonte) and not re.search(indicio, normal):
            raise RevisaoDaReescrita("Uma observação incerta foi transformada em afirmação geral.")
        raro = r"\b(?:raros?|raras?|raramente|incomu\w*|esporadic\w*)\b|\bpoucas? (?:vezes|ocorrencias)\b"
        if re.search(raro, fonte) and not re.search(raro, normal):
            raise RevisaoDaReescrita("A frequência rara do efeito foi omitida.")
        for conceito in ("pressao arterial baixa", "dor de cabeca"):
            if conceito in fonte and conceito not in normal:
                raise RevisaoDaReescrita("Efeito descrito no original foi omitido: " + conceito)
        equilibrio = r"\b(?:steady[ -]state|estado (?:estacionario|estavel|de equilibrio))\b"
        if re.search(equilibrio, fonte) and not re.search(equilibrio, normal):
            raise RevisaoDaReescrita("A condição de estado de equilíbrio foi omitida.")
        faltantes = set(numeros(fonte)) - set(numeros(normal))
        if faltantes:
            raise RevisaoDaReescrita("A reescrita omite quantidade, dose ou frequência do original: " + ", ".join(sorted(faltantes)) + ".")
        if re.search(r"\b(?:pode|podem|possivel|possibilidade)\b", fonte) and not re.search(
                r"\b(?:pode|podem|possivel|possibilidade)\b", normal):
            raise RevisaoDaReescrita("A reescrita perdeu a incerteza do original.")
        if re.search(r"\b(?:nao|sem|exceto|salvo|nem)\b", fonte) and not re.search(
                r"\b(?:nao|sem|exceto|salvo|nem)\b", normal):
            raise RevisaoDaReescrita("A reescrita perdeu uma negação ou exceção.")
        for padrao in [r"\b(?:exceto|salvo)\b", r"\b(?:somente|apenas)\b", r"\bnao[ -]+seletiv\w*"]:
            if re.search(padrao, fonte) and not re.search(padrao, normal):
                raise RevisaoDaReescrita("A reescrita generalizou uma exceção, restrição ou qualificador de classe: " +
                                        re.search(padrao, fonte).group() + ".")
        condicoes = r"\b(?:insuficiencia renal|insuficiencia hepatica|insuficiencia do figado|doses? elevadas?|altas? doses?|uso prolongado|diabetic\w*|mulheres|gravidas|idosos)\b"
        for condicao in re.findall(condicoes, fonte):
            if condicao not in normal:
                raise RevisaoDaReescrita("Condição relevante omitida: " + condicao)
        for via in VIA.finditer(mapear_texto(referencia).texto):
            termo = next((g for g in via.groups() if g), "")
            if termo and termo not in normal:
                raise RevisaoDaReescrita("Via de administração omitida: " + termo)
        for direcao, padrao in DIRECOES.items():
            if padrao.search(efeito_fonte) and not padrao.search(efeito):
                raise RevisaoDaReescrita("A reescrita perdeu a indicação de " + direcao)
        # Somente as doses explícitas da referência; não elimina dados para
        # caber em um card. Falhar é preferível a publicar uma omissão.
        for dose in QUANTIDADE.finditer(fonte):
            if FREQUENCIA.fullmatch(dose.group()):
                continue
            if dose.group() not in normal:
                raise RevisaoDaReescrita("Dose ou frequência alterada: " + dose.group())
    # Listas novas de exemplos não são uma paráfrase da evidência. A regra
    # restringe-se a enumerações de palavras únicas, sem interpretar nomes
    # por semelhança nem tratar uma oração ('como filtros, retiram ...') como lista.
    exemplos = (r"\b(?:como|por exemplo)\s*[:,]?\s*"
                r"((?:[^\W\d_]{4,}(?:\s*[,;]\s*(?:(?:e|ou)\s+)?|\s+(?:e|ou)\s+)){1,}[^\W\d_]{4,})"
                r"(?!\w)(?=\s*(?:[.,;:)]|$))")
    apoio_nomes = fonte + " " + _normal_simples(" ".join(e['texto'] for e in entrada['evidencias']))
    apoio_nomes += " " + _normal_simples(entrada.get('alvo') or '')
    for lista in re.finditer(exemplos, normal):
        for nome in re.findall(r"\b[^\W\d_]{4,}\b", lista[1]):
            if nome in {'entre', 'pode', 'podem', 'deve', 'devem', 'pois', 'quando', 'assim', 'ainda'}:
                continue
            if not re.search(r"(?<!\w)" + re.escape(nome) + r"(?!\w)", apoio_nomes):
                raise RevisaoDaReescrita('Exemplo novo sem suporte na evidência: ' + nome + '. Use apenas os exemplos da própria referência.')


def validar_reescrita_original(entrada):
    """Confere o original antes da inferência, preservando inconsistências para revisão."""
    from pipelines.identificacao_interacoes.validacao import oracoes_do_alvo
    from pipelines.identificacao_interacoes.resolucao import substancias, forma_sem_sal

    original = entrada["resumo_original"]
    if re.search(r"\bX\b", original) and not any(re.search(r"\bX\b", e["texto"]) for e in entrada["evidencias"]):
        raise RevisaoDaReescrita("O primeiro resumo usa o marcador X sem correspondência na evidência.")
    if entrada["tipo"] == "geral":
        normal_original = _normal_simples(original)
        principios = {alias for p in substancias(entrada.get("principios_ativos")) for alias in (p, forma_sem_sal(p)) if alias}
        for lista in re.finditer(r"\binterac\w*\b([^.;!?\n]{0,70}?)\bcom\b([^.;!?\n]+)", normal_original):
            if re.search(r"[,;:]|\b(?:estudos?|ensaios?|individuos|pacientes|voluntarios|tratad\w*)\b", lista[1]):
                continue
            if re.search(r"\befeitos?\s+(?:da\s+)?$", normal_original[max(0, lista.start() - 25):lista.start()]):
                continue
            if re.search(r"\b(?:que|pode|podem|aument\w*|diminu\w*|redu[cz]\w*)\b", lista[1]):
                continue
            # Só a enumeração de alvos: não confunde uma condição ou o efeito
            # sobre o componente da bula com uma interação contra ele próprio.
            trecho = lista[2].lstrip(" :")
            trecho = re.sub(r"^(?:(?:os|as|estes|estas)\s+)?(?:seguintes\s+)?(?:medicamentos|substancias|farmacos)\s*:\s*", "", trecho)
            alvos = re.split(r":|\b(?:que|quando|pode|podem|aument\w*|diminu\w*|redu[cz]\w*)\b|\bem (?:mulheres|pacientes|idosos|uso)\b", trecho)[0]
            if any(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", alvos) for p in principios):
                raise RevisaoDaReescrita("O geral apresenta um componente do próprio medicamento como alvo; confira o original.")
    fonte = "\n".join(preparar_fonte_do_alvo(e["texto"], entrada["origem"], entrada.get("alvo"))
                      for e in entrada["evidencias"])
    if entrada.get("alvo"):
        # Um resumo que substitui o medicamento identificado na fonte por
        # "outros medicamentos" amplia a relação. Volta à fonte em vez de
        # pedir ao modelo para preservar essa generalização.
        nomes_origem = [entrada["origem"]] + list(substancias(entrada.get("principios_ativos")))
        citado = lambda nome, texto: bool(nome and re.search(r"(?<!\w)" + re.escape(_normal_simples(nome)) + r"(?!\w)", _normal_simples(texto)))
        if (re.search(r"\boutros medicamentos\b", _normal_simples(original))
                and any(citado(n, fonte) for n in nomes_origem)
                and not any(citado(n, original) for n in nomes_origem)):
            raise RevisaoDaReescrita("O resumo anterior generaliza a relação com outros medicamentos; use a combinação identificada na fonte.")
        alvo = mapear_texto(entrada["alvo"], unir_hifenizacao=True).texto
        if alvo not in mapear_texto(fonte, unir_hifenizacao=True).texto:
            raise RevisaoDaReescrita("O alvo não foi encontrado na evidência literal.")
        # Nos blocos tabulares a lista e seu efeito são uma unidade. Recortar
        # só a oração que cita o alvo descartaria "Efeitos da interação".
        sem_marcador = lambda t: re.sub(r"^\s*[-–•*]\s+(?=[^\W\d_])", "", t)
        literal = _normal_simples(sem_marcador(original)) == _normal_simples(sem_marcador(fonte))
        if not efeito_do_bloco(fonte) and not literal:
            # Um ponto de extração antes de uma continuação modal sem
            # sujeito ("lista de medicamentos. pode retardar...") não
            # pode separar a lista do efeito. Só a referência de comparação
            # é preparada; o trecho literal armazenado continua intacto.
            referencia = re.sub(r"\.[ \t]*(?:\r?\n[ \t]*)?(?=(?:pode|podem)\b)", " ", sem_rotulo_intercalado(fonte))
            oracoes = oracoes_do_alvo(mapear_texto(referencia, unir_hifenizacao=True).texto, alvo)
            if oracoes:
                fonte = " ".join(oracoes)
    try:
        # Esta etapa não acrescenta contexto de estudo que o resumo anterior
        # já omitia. Na saída, o contexto presente no texto a reescrever é
        # preservado; uma reconstrução usa a fonte literal como referência.
        conferir_fidelidade(original, fonte, entrada, preservar=entrada["tipo"] == "card", preservar_estudo=False)
    except RevisaoDaReescrita as erro:
        raise RevisaoDaReescrita("Resumo original precisa de revisão: " + str(erro)) from erro


def ler_saida_reescrita(resposta, entrada):
    if resposta.truncada:
        raise ReescritaTruncada("Reescrita truncada: a resposta atingiu o limite de saída antes de terminar; nenhuma frase parcial será publicada.")
    try:
        objeto = json.loads(resposta.conteudo)
    except (ValueError, TypeError) as erro:
        raise ValueError("Resposta da reescrita não é JSON válido.") from erro
    if not isinstance(objeto, dict) or set(objeto) != {"status", "texto", "motivo"} or not all(
            isinstance(objeto[k], str) for k in objeto):
        raise ValueError("Resposta da reescrita fora do esquema.")
    if objeto["status"] == "REVISAO":
        raise RevisaoDaReescrita("Modelo sinalizou inconsistência: " + objeto["motivo"][:500])
    if objeto["status"] != "REESCRITO" or objeto["motivo"].strip():
        raise ValueError("Status ou motivo incompatível com reescrita concluída.")
    texto = re.sub(r"^\s*(?:(?:\d{1,2}\s*[-.)]\s*)?(?:INTERAÇÕES MEDICAMENTOSAS|Interações medicamento-medicamento)\s*[.:]?\s*)+",
                   "", objeto["texto"].strip(), flags=re.I)
    # Acabamento editorial solicitado para a interface: remove apenas
    # atribuições isoladas à fonte conhecida, sem tocar em dados clínicos
    # nem em remissões como "vide Contraindicações".
    origem = re.escape(entrada["origem"])
    nome_fonte = r"(?:\s+d[aeo]\s+(?:medicamento\s+)?" + origem + r")?"
    texto = re.sub(r"^(?:a|esta|essa)\s+bula" + nome_fonte +
                   r"\s+(?:descreve|informa|relata|menciona|diz|afirma)(?:\s+que)?\s+", "", texto, flags=re.I)
    texto = re.sub(r"^(?:nesta|nessa|na)\s+bula\s*,\s*", "", texto, flags=re.I)
    texto = re.sub(r",?\s*(?:segundo|conforme(?:\s+(?:descrito|informado|relatado|mencionado))?|de acordo com)\s+(?:a|na)\s+bula" + nome_fonte +
                   r"(?=\s*[.!?]?\s*$)", "", texto, flags=re.I).strip()
    texto = texto[:1].upper() + texto[1:]
    if not texto or len(texto) < 20:
        raise ValueError("Reescrita vazia ou sem conteúdo suficiente.")
    if entrada["tipo"] == "geral" and len([p for p in re.split(r"\n\s*\n", texto) if p.strip()]) != 1:
        raise ValueError("Esta chamada deve retornar um único parágrafo; os demais são tratados separadamente.")
    if re.search(r"[{}\[\]`]|<\|.*?\|>", texto) or any(t in mapear_texto(texto).texto for t in ECO_DO_PROMPT):
        raise ValueError("A reescrita contém marcação ou instruções.")
    palavras = _normal_simples(texto).split()
    sequencias = [tuple(palavras[i:i + 6]) for i in range(max(0, len(palavras) - 5))]
    if any(sequencias.count(s) >= 3 for s in set(sequencias)):
        raise ValueError("Repetição excessiva na reescrita.")
    return texto


def preservar_modalidade_do_cuidado(texto, referencia):
    """Restaura uma palavra da referência somente com equivalência do texto todo.

    Não completa uma resposta, não escolhe medicamentos e não cria orientação.
    Qualquer outra diferença mantém a saída sem correção para os filtros.
    """
    from .prompts import guia_redacao

    fonte = _normal_simples(guia_redacao(referencia))
    atual = _normal_simples(guia_redacao(texto))
    cuidado = r"\b(?:deve|pode)-se ter cuidado\b"
    if len(re.findall(cuidado, fonte)) != 1 or len(re.findall(cuidado, atual)) != 1:
        return texto
    if not re.search(r"\bdeve-se ter cuidado\b", fonte):
        return texto
    corrigido = re.sub(r"\bpode-se(?=\s+ter\s+cuidado\b)",
                       lambda m: "Deve-se" if m[0][:1].isupper() else "deve-se",
                       texto, count=1, flags=re.I)
    if corrigido != texto and _normal_simples(guia_redacao(corrigido)) == fonte:
        return corrigido
    return texto


def validar_saida_reescrita(resposta, entrada):
    texto = ler_saida_reescrita(resposta, entrada)
    conferir_fidelidade(texto, entrada["resumo_original"], entrada, preservar=True)
    assinaturas_originais = _assinaturas_relacionais(entrada["resumo_original"], entrada)
    if not assinaturas_originais and _assinaturas_relacionais(texto, entrada):
        raise RevisaoDaReescrita("A reescrita atribui papéis causais que o original não explicita; preserve a descrição da bula.")
    if _subtipos_receptores(_normal_simples(entrada["resumo_original"])) - _subtipos_receptores(_normal_simples(texto)):
        raise RevisaoDaReescrita("A reescrita omite um subtipo de receptor do original.")
    conferir_fidelidade(texto, "\n".join(e["texto"] for e in entrada["evidencias"]), entrada)
    alvo = mapear_texto(entrada.get("alvo") or "").texto
    if alvo and alvo in mapear_texto(entrada["resumo_original"]).texto and alvo not in mapear_texto(texto).texto:
        raise RevisaoDaReescrita("O alvo foi perdido ou trocado na reescrita.")
    validar_citacoes(texto, "\n".join(e["texto"] for e in entrada["evidencias"]))
    return texto


def validar_saida_reconstrucao(resposta, entrada):
    """A bula é a referência; o resumo incorreto anterior permanece armazenado."""
    texto = ler_saida_reescrita(resposta, entrada)
    alvo = mapear_texto(entrada.get("alvo") or "").texto
    if alvo and not re.search(r"(?<!\w)" + re.escape(alvo) + r"(?!\w)", mapear_texto(texto).texto):
        raise RevisaoDaReescrita("A reconstrução precisa mencionar o alvo da interação.")
    try:
        validar_reescrita_original({**entrada, "resumo_original": texto})
    except RevisaoDaReescrita as erro:
        detalhe = str(erro).removeprefix("Resumo original precisa de revisão: ")
        raise RevisaoDaReescrita("Resposta reconstruída não preserva a fonte: " + detalhe) from erro
    fonte = "\n".join(dict.fromkeys(e["texto"] for e in entrada["evidencias"]))
    conferir_fidelidade(texto, fonte, entrada, preservar=True)
    validar_citacoes(texto, fonte)
    return texto


def validar_consolidacao(texto, entrada):
    """Recusa repetição de redação e perda de remissões ao reunir o mesmo alvo."""
    fontes = list(dict.fromkeys(e["texto"] for e in entrada["evidencias"]))
    if len(fontes) < 2 or len({e["alvo"] for e in entrada["evidencias"]}) != 1:
        return
    from pipelines.identificacao_interacoes.validacao import DIRECOES

    normal = _normal_simples(texto)
    palavras = re.findall(r"\w+", normal)
    frases = Counter((tuple(palavras[i:i + 4]), any(p in {"nao", "sem"} for p in palavras[max(0, i - 2):i]))
                     for i in range(len(palavras) - 3)
                     if any(p.fullmatch(palavras[i]) for p in DIRECOES.values())
                     and palavras[i + 3] not in {"de", "do", "da", "dos", "das", "com", "por", "no", "na"}
                     and len(" ".join(palavras[i:i + 4])) >= 28)
    erros = []
    if any(n > 1 for n in frases.values()):
        erros.append("O mesmo efeito foi repetido: escreva-o UMA só vez e reúna as condições na mesma frase.")
    remissoes = ["contraindicacoes" in _normal_simples(f) for f in fontes]
    if any(remissoes) and "contraindicacoes" not in normal:
        erros.append("Mantenha a remissão a Contraindicações, vinculada apenas à condição do trecho que a contém.")
    if any(remissoes) and not all(remissoes) and re.search(r"\b(?:ambas|todas)\b[^.]{0,120}\bcontraindicacoes\b", normal):
        erros.append("Não aplique a ambas as condições a remissão a Contraindicações: ela consta só em um trecho.")
    if erros:
        raise RevisaoDaReescrita(" ".join(erros))


def validar_linguagem_simples(texto, *, abertura=None):
    """Pede nova redação quando conserva jargões com equivalência curta conhecida."""
    # As citações já passaram pela conferência literal. O título de uma
    # seção deve continuar exato mesmo quando contém termos técnicos.
    sem_remissoes, _ = proteger_remissoes(texto)
    normal = mapear_texto(sem_remissoes).texto
    if re.search(r"[↓↑]|\b(?:auc|cmax)\)?\s*:", normal):
        raise RevisaoDaReescrita("Escreva os resultados da tabela em frases completas: explique a redução ou aumento da medida, sem setas nem rótulos de células.")
    if re.search(r"(?:^|[.!?]\s+)INTERAÇÕES MEDICAMENTOSAS(?:[.:]\s*|$)", texto):
        raise RevisaoDaReescrita("Retire o título de seção INTERAÇÕES MEDICAMENTOSAS; escreva apenas a explicação, sem repetir o mesmo efeito.")
    if re.match(r"^ANTI-INFECCIOSOS\s+Antimicobacterianos\b", texto):
        raise RevisaoDaReescrita("Retire os cabeçalhos de classe ANTI-INFECCIOSOS e Antimicobacterianos. Comece pela combinação com o medicamento e seu regime, preservando os valores e as condições.")
    termos = [(r"\bconcomitant\w*", "ao mesmo tempo"),
              (r"\badministracao simultanea\b", "uso ao mesmo tempo"),
              (r"\bcoadministr\w*", "utilização em conjunto"),
              (r"\bconjuntamente\b", "juntos"),
              (r"\bpotencializ\w*", "aumento do efeito"),
              (r"\bfarmacos?\b", "medicamentos"),
              (r"\binibicao do efeito\b", "redução do efeito"),
              (r"\bhipotensao\b", "pressão arterial baixa"),
              (r"\bhipercalemia\b", "níveis altos de potássio no sangue"),
              (r"\bfarmacodinamic\w*", "efeitos do medicamento no organismo"),
              (r"\bcefaleias?\b", "dor de cabeça"),
              (r"\bclearance(?:\s+plasmatic\w*)?", "explique a retirada do medicamento do sangue pelo organismo"),
              (r"\bexcrecao renal\b|\bdepuracao renal\b", "eliminação do medicamento pelos rins"),
              (r"\bsitios de ligacao\b", "locais onde esses medicamentos se ligam"),
              (r"\btoxicidade\b", "efeitos prejudiciais ao organismo"),
              (r"\bhematologic\w*", "no sangue"),
              (r"\binotropic\w*[ -]+negativ\w*", "efeitos que enfraquecem as contrações do coração")]
    explicados = set()
    if re.search(r"\bpotassio\b[^.;!?]{0,50}\bsangue\b", normal):
        explicados.add(r"\bhipercalemia\b")
    if re.search(r"\befeitos?\b[^.;!?]{0,60}\borganismo\b", normal):
        explicados.add(r"\bfarmacodinamic\w*")
    if re.search(r"\befeitos? prejudicia(?:l|is)\b", normal):
        explicados.add(r"\btoxicidade\b")
    if re.search(r"\b(?:retir\w*|remov\w*|elimin\w*)\b[^.;!?]{0,100}\bsangue\b", normal):
        explicados.add(r"\bclearance(?:\s+plasmatic\w*)?")
    if re.search(r"\belimin\w*\b[^.;!?]{0,60}\brins\b", normal):
        explicados.add(r"\bexcrecao renal\b|\bdepuracao renal\b")
    if re.search(r"\bsangue\b", normal):
        explicados.add(r"\bhematologic\w*")
    if re.search(r"\benfraquec\w*\b[^.;!?]{0,60}\bcontrac\w*\b[^.;!?]{0,30}\bcoracao\b", normal):
        explicados.add(r"\binotropic\w*[ -]+negativ\w*")
    pendentes = [re.search(padrao, normal).group() + " → " + simples
                 for padrao, simples in termos if re.search(padrao, normal) and padrao not in explicados]
    if pendentes:
        raise RevisaoDaReescrita("Simplifique os termos: " + "; ".join(pendentes) + ". Preserve as condições da frase.")
    if re.match(r"^(?:(?:a|na|nesta|nessa|esta|essa)\s+bula\b|segundo\s+(?:a|esta)\s+bula\b|o\s+texto\s+(?:informa|descreve)\b)", normal):
        raise RevisaoDaReescrita("Comece diretamente pelo efeito, combinação ou condição. Retire a introdução sobre a bula; a interface já identifica a fonte.")
    # A abertura é uma sugestão do prompt. Uma redação correta como 'Ao usar'
    # não deve consumir tentativas nem ser recusada por não iniciar com 'Com'.
