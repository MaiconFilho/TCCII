"""Barreiras de formato e de tom. Não conferem fidelidade clínica: o card é uma
explicação do LLM com as próprias palavras, e a evidência literal fica em "Ver evidência"."""

import json
import re

from pipelines.identificacao_interacoes.normalizacao import mapear_texto
from pipelines.identificacao_interacoes.validacao import GRAVIDADE

from .prompts import SEM_DETALHE
from pipelines.identificacao_interacoes.contexto_evidencia import efeito_do_bloco

FALTA_INFO = re.compile(
    r"\bdesconhecid\w*|\bnao\s+(?:se\s+sabe|e\s+conhecid\w*|ha\s+(?:\w+\s+){0,2}informac\w*|"
    r"(?:tem|ha)\s+sido\s+relatad\w*|(?:foi|foram|e|sao)\s+(?:estudad\w*|investigad\w*|relatad\w*|documentad\w*))"
    r"|\bnao\s+(?:ha|existem|tem|dispomos\s+de)\s+(?:\w+\s+){0,2}(?:dados|informacoes|estudos|evidencias|relatos)"
    r"|\bsem\s+(?:dados|informacoes|estudos)|\bfalt\w*\s+(?:dados|informacoes|estudos)")
SEM_INTERACAO = re.compile(
    r"\b(?:nao\s+(?:ha|existe\w*|ocorre\w*)|sem|ausencia\s+de)\s+interac\w*"
    r"|\bnao\s+(?:afet\w*|interag\w*|influenci\w*)|\b(?:segur\w*|sem\s+riscos?)\b")
EFEITO = re.compile(
    r"\b(?:aument\w*|elev\w*|redu[cz]\w*|diminu\w*|potencializ\w*|inibi\w*|"
    r"risco\w*|efeito\w*\s+advers\w*|ritmo\s+cardiaco\s+irregular)\b")


def validar_fundamentacao(texto: str, evidencia: str) -> None:
    """Ausência de contexto nunca vira uma afirmação de ausência de interação."""
    normal = mapear_texto(texto).texto.lower()
    fonte = mapear_texto(evidencia).texto.lower()
    efeito = mapear_texto(efeito_do_bloco(evidencia)).texto.lower()
    if texto.strip() == SEM_DETALHE:
        if EFEITO.search(fonte):
            raise ValueError("A evidência descreve um efeito; não afirme falta de informação. Resuma o efeito descrito.")
        return
    if FALTA_INFO.search(normal) and not FALTA_INFO.search(fonte):
        raise ValueError("Falta de estudos ou interação desconhecida não consta na evidência. Resuma somente o efeito descrito.")
    if SEM_INTERACAO.search(normal) and not SEM_INTERACAO.search(fonte):
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
    if set(re.findall(r"[\"'“”‘’]", texto)) - set(evidencia):
        raise ValueError("Resumo contém citação entre aspas ausente da evidência.")
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
