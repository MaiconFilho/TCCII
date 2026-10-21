"""Validação semântica mínima das relações propostas pelo modelo.

A existência literal da citação não comprova a fidelidade do resumo. Estas
regras determinísticas rejeitam os erros mais perigosos de interpretação:

- evidência fora do trecho analisado;
- relação apoiada em frase de ausência ("não foram realizados estudos",
  "não foram observadas interações", "não se sabe");
- alvo que não aparece na evidência (mera coocorrência ou citação errada);
- resumo que omite negação, dose/quantidade/frequência ou via presentes na
  oração do alvo;
- resumo que acrescenta gravidade ou direção de efeito (aumento/redução)
  ausentes da evidência;
- alvo igual ao próprio medicamento de origem.

São verificações conservadoras e não substituem revisão humana.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .modelos import MotivoRejeicao, RelacaoIdentificada, Rejeicao, TipoAlvo
from .normalizacao import (
    contem_termo,
    mapear_texto,
    normalizar_alvo,
    normalizar_citacao,
    palavras_relevantes,
)
from .resolucao import chaves_do_principio


# Todos os padrões abaixo operam sobre texto já normalizado (sem acentos e
# em caixa baixa).
NEGACAO = re.compile(r"\b(?:nao|nem|nunca|jamais|sem|exceto|salvo)\b")
RESTRICAO_NO_RESUMO = re.compile(
    r"\b(?:nao|nem|nunca|jamais|sem|exceto|salvo)\b"
    r"|\bevit\w*|\bcontraindic\w*|\bproib\w*|\bdesaconselh\w*"
)
AUSENCIA = re.compile(
    r"\bnao\s+(?:foi\s+)?(?:estudad\w*|avaliad\w*|investigad\w*|testad\w*)\b"
    r"|"
    r"\bnao\s+(?:foram|foi|sao|e|ha|houve|existem|se\s+(?:observ\w+|conhec\w+|sabe|esper\w+|identific\w+))"
    r"(?:\s+\w+){0,4}?\s*(?:realizad\w*|observad\w*|identificad\w*|conhecid\w*|relatad\w*"
    r"|descrit\w*|esperad\w*|avaliad\w*|estudad\w*|encontrad\w*|notificad\w*|evidenciad\w*|demonstrad\w*)"
    r"|\bnao\s+se\s+(?:sabe|conhece|observ\w+|esper\w+)"
    r"|\bnao\s+(?:parec\w*\s+|deve\w*\s+|sao\s+esperad\w*\s+)?"
    r"(?:alter\w+|afet\w+|modific\w+|influenci\w+|interfer\w+)"
    r"|\bsem\s+(?:nenhum\w*\s+)?(?:impacto|alteracao|efeito|interferencia)\b"
    r"|\bnenhuma\s+interac\w+|\bdesconhec\w+|\bnao\s+ha\s+(?:relatos|dados|estudos|informac\w+)"
    r"|\bnao\s+existem\s+(?:dados|estudos|relatos)"
)
RECOMENDACAO_FORTE = re.compile(
    r"\bnao\s+(?:deve|devem|se\s+recomenda|e\s+recomendad\w*|sao\s+recomendad\w*)"
    r"|\bevit\w*|\bcontraindic\w*|\bcautela\b|\bmonitor\w*|\bdeve(?:m)?\s+ser\b"
    r"|\brecomenda-se\b|\brecomendad\w*|\bdeve-se\b|\bajust\w*\s+(?:de\s+|da\s+)?dose"
    r"|\bsuspen\w*|\bdescontinu\w*|\binterromp\w*"
)
QUANTIDADE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:mg|g|mcg|ug|µg|μg|ml|l|ui|u\.i\.|meq|mmol|gotas?|comprimidos?|capsulas?)"
    r"(?:\s*/\s*(?:kg|dia|m2|m²|h|hora|semana))?(?![a-z])"
    r"|(\d+(?:[.,]\d+)?)\s+vezes\s+(?:ao|por)\s+(?:dia|semana)"
    r"|a\s+cada\s+(\d+(?:[.,]\d+)?)\s+(?:horas|h|dias)"
)
DOSE_QUALITATIVA = re.compile(
    r"\b(?:altas?\s+doses?|doses?\s+(?:altas?|elevadas?|superiores|acima|maiores|terapeuticas|baixas?))\b"
    r"|\buso\s+(?:prolongado|cronico)\b|\blongo\s+prazo\b"
)
VIA = re.compile(
    r"\b(?:via|uso|forma|apresentacao|administracao|solucao|suspensao)\s+"
    r"(oral|intravenos\w*|endovenos\w*|intramuscular\w*|subcutane\w*|topic\w*|oftalmic\w*"
    r"|inalatori\w*|intratecal|retal|vaginal|transdermic\w*|sublingual|nasal|injetave\w*)"
    r"|\b(intravenos\w*|endovenos\w*|intramuscular\w*|subcutane\w*|injetave\w*|intratecal)\b"
)
# Direção do efeito: o resumo não pode afirmar aumento ou redução que a
# evidência não afirma ("afetem a concentração" não é "aumenta").
DIRECOES = {
    "aumento": re.compile(
        r"\b(?:aument\w*|elev\w*|potencializ\w*|intensific\w*|acentu\w*|maior\w*)"
    ),
    "redução": re.compile(
        r"\b(?:diminu\w*|reduz\w*|reduc\w*|baix\w*|atenu\w*|menor\w*|inibi\w*|antagoni\w*)"
    ),
}
GRAVIDADE = re.compile(
    r"\b(grave\w*|gravidade|leve\w*|moderad\w*|sever\w*|seri[oa]s?|fata\w*|leta\w*|perigos\w*)\b"
)
SEPARADOR_ORACOES = re.compile(
    r";\s+|\.\s+|,\s+(?:mas|porem|entretanto|contudo|todavia|no\s+entanto)\s+"
    r"|\s+(?:porem|entretanto|contudo|todavia|no\s+entanto),?\s+"
)
CARACTERES_ALVO = re.compile(r"[a-z]", re.I)

# Muda sempre que uma regra muda; compõe a versão do processamento.
# validacao/5: reconhece também ausências diretas como "azitromicina não estudado".
# validacao/4: uma pendência só é resolvida por relação aceita do mesmo alvo
# com a mesma evidência (antes bastava o mesmo alvo em qualquer frase).
VERSAO_VALIDACAO = "validacao/5"

# Palavras que, sozinhas ou no início do alvo, descrevem efeito, dose ou
# fisiologia — não uma substância, medicamento, classe ou alimento. Observadas
# no piloto: "drogas", "testes", "mucosa gastrintestinal", "função renal",
# "fluxo sanguíneo renal", "doses anti-inflamatórias". Não se aplica a
# EXAME_LABORATORIAL ("testes da função tireoidiana" é um exame).
CABECAS_GENERICAS = frozenset(
    {
        "droga", "drogas", "medicamento", "medicamentos", "farmaco", "farmacos",
        "substancia", "substancias", "produto", "produtos", "agente", "agentes",
        "alteracao", "alteracoes", "teste", "testes", "exame", "exames", "efeito",
        "efeitos", "sistema", "tratamento", "paciente", "pacientes", "dose", "doses",
        "funcao", "fluxo", "retencao", "mucosa", "nivel", "niveis", "tempo", "risco",
        "absorcao", "excrecao", "eliminacao", "concentracao", "concentracoes",
        "toxicidade", "sangramento", "hemostasia", "uso", "administracao",
    }
)
NEGACAO_ORIGINAL = re.compile(r"\b(?:não|nao|nem|nunca|jamais|sem|exceto|salvo)\b(?:\s+\S+){0,5}", re.I)


def relacao_nao_positiva(linha: dict) -> bool:
    """Filtro compartilhado pela consulta e pela apresentação de resumos."""
    evidencia = mapear_texto(linha.get("evidencia") or "", unir_hifenizacao=True).texto
    alvo = normalizar_alvo(linha.get("alvo") or "")
    oracoes = oracoes_do_alvo(evidencia, alvo)
    return any(AUSENCIA.search(oracao) for oracao in oracoes) and not any(
        RECOMENDACAO_FORTE.search(oracao) for oracao in oracoes
    )


def alvo_generico(alvo_normalizado: str, tipo_alvo: TipoAlvo) -> bool:
    if tipo_alvo == TipoAlvo.EXAME_LABORATORIAL:
        return False
    palavras = re.findall(r"[a-z0-9]+", alvo_normalizado)
    return bool(palavras) and palavras[0] in CABECAS_GENERICAS

# Palavras de ligação ou de recomendação genérica, comuns em paráfrases fiéis
# ("não deve ser usado com" → "evitar o uso conjunto"); não contam no apoio.
PALAVRAS_GENERICAS = frozenset(
    {
        "evitar", "evite", "evita", "usar", "utilizar", "utilizado", "utilizados", "uso",
        "usado", "usados", "usada", "administrar", "administrado", "administrados",
        "administracao", "conjunto", "conjunta", "concomitante", "concomitantemente",
        "associacao", "associado", "associados", "juntos", "junto", "pode", "podem",
        "tratamento", "medicamento", "medicamentos", "deve", "devem", "quando",
        "durante", "pacientes", "paciente", "outros", "outras", "este", "esta",
        "isso", "essa", "esse", "como", "pelo", "pela", "entre", "sobre", "seus",
        "suas", "tambem", "ainda", "mesmo", "mesma", "presenca", "possivel",
        "recomenda", "recomendado", "recomendada", "recomendacao", "orienta",
    }
)
LIMIAR_APOIO_RESUMO = 0.5


@dataclass(frozen=True, slots=True)
class ContextoValidacao:
    """Identificação da origem, vinda do banco, e o trecho completo."""

    chaves_origem: frozenset[str]
    texto: str
    max_caracteres_resumo: int = 300


def chaves_da_origem(
    nome_normalizado: str, nome_exibicao: str, principios_ativos: str | None
) -> frozenset[str]:
    chaves = {normalizar_alvo(nome_normalizado), normalizar_alvo(nome_exibicao)}
    for principio in (principios_ativos or "").split(";"):
        chaves.update(chaves_do_principio(principio))
    return frozenset(c for c in chaves if c)


def e_a_origem(alvo_normalizado: str, chaves_origem: frozenset[str]) -> bool:
    """Alvo igual à origem, ou com todas as palavras contidas num nome dela.

    "varfarina" na bula do Marevan ("varfarina sódica") é a própria origem.
    A inclusão de palavras serve só para **descartar** autorreferência; nunca
    cria vínculo nem equivalência.
    """
    if alvo_normalizado in chaves_origem:
        return True
    palavras = set(re.findall(r"[a-z0-9]+", alvo_normalizado))
    return bool(palavras) and any(
        palavras <= set(re.findall(r"[a-z0-9]+", chave)) for chave in chaves_origem
    )


def _numeros(texto: str) -> set[str]:
    encontrados: set[str] = set()
    for correspondencia in QUANTIDADE.finditer(texto):
        numero = next(grupo for grupo in correspondencia.groups() if grupo)
        encontrados.add(numero.replace(".", ","))
    return encontrados


def _vias(texto: str) -> set[str]:
    raizes: set[str] = set()
    for correspondencia in VIA.finditer(texto):
        palavra = next(grupo for grupo in correspondencia.groups() if grupo)
        raizes.add(palavra[:6])
    return raizes


def _oracoes(texto_normalizado: str) -> list[str]:
    return [parte for parte in SEPARADOR_ORACOES.split(texto_normalizado) if parte.strip()]


def mencoes_do_alvo(evidencia_normalizada: str, alvo_normalizado: str) -> bool:
    if contem_termo(evidencia_normalizada, alvo_normalizado):
        return True
    palavras = palavras_relevantes(alvo_normalizado)
    return bool(palavras) and all(contem_termo(evidencia_normalizada, p) for p in palavras)


def oracoes_do_alvo(evidencia_normalizada: str, alvo_normalizado: str) -> list[str]:
    """Orações que citam o alvo; se nenhuma isolar o alvo, a evidência toda."""
    selecionadas = [
        oracao for oracao in _oracoes(evidencia_normalizada)
        if mencoes_do_alvo(oracao, alvo_normalizado)
    ]
    return selecionadas or [evidencia_normalizada]


def apoio_lexical(
    resumo_normalizado: str, evidencia_normalizada: str, ignorar: Iterable[str] = ()
) -> float | None:
    """Fração das palavras de conteúdo do resumo que aparecem na evidência.

    Compara radicais de cinco letras para tolerar flexões ("monitore" e
    "monitorar"). ``None`` quando o resumo só tem palavras genéricas.
    """
    ignoradas = set(PALAVRAS_GENERICAS)
    for termo in ignorar:
        ignoradas.update(re.findall(r"[a-z0-9]+", termo))
    palavras = [
        palavra for palavra in re.findall(r"[a-z]+", resumo_normalizado)
        if len(palavra) >= 4 and palavra not in ignoradas
    ]
    if not palavras:
        return None
    radicais = {p[:5] for p in re.findall(r"[a-z]+", evidencia_normalizada) if len(p) >= 4}
    return sum(1 for palavra in palavras if palavra[:5] in radicais) / len(palavras)


def verificar_resumo(
    oracoes: Iterable[str],
    evidencia_normalizada: str,
    resumo: str,
    chaves_origem: Iterable[str] = (),
) -> tuple[MotivoRejeicao, str] | None:
    """Confere condições que alteram o significado, gravidade e apoio do resumo."""
    resumo_normalizado = mapear_texto(resumo).texto
    oracoes = list(oracoes)
    alvo_texto = " ".join(oracoes)

    if NEGACAO.search(alvo_texto) and not RESTRICAO_NO_RESUMO.search(resumo_normalizado):
        return (
            MotivoRejeicao.RESUMO_OMITE_NEGACAO,
            "A evidência contém negação ou exceção que não aparece no resumo.",
        )

    faltantes = sorted(_numeros(alvo_texto) - {
        n.replace(".", ",") for n in re.findall(r"\d+(?:[.,]\d+)?", resumo_normalizado)
    })
    if faltantes:
        return (
            MotivoRejeicao.RESUMO_OMITE_QUANTIDADE,
            "O resumo omite dose, quantidade ou frequência: " + ", ".join(faltantes) + ".",
        )
    if DOSE_QUALITATIVA.search(alvo_texto) and not re.search(
        r"\bdos\w*|prolongad\w*|cronic\w*|longo\s+prazo", resumo_normalizado
    ):
        return (
            MotivoRejeicao.RESUMO_OMITE_QUANTIDADE,
            "O resumo omite a condição de dose ou duração descrita na evidência.",
        )

    vias_faltantes = sorted(
        via for via in _vias(alvo_texto) if via not in resumo_normalizado
    )
    if vias_faltantes:
        return (
            MotivoRejeicao.RESUMO_OMITE_VIA,
            "O resumo omite a via ou apresentação: " + ", ".join(vias_faltantes) + ".",
        )

    for correspondencia in GRAVIDADE.finditer(resumo_normalizado):
        raiz = correspondencia.group(1)[:5]
        if raiz not in evidencia_normalizada:
            return (
                MotivoRejeicao.RESUMO_INVENTA_GRAVIDADE,
                f"O resumo usa '{correspondencia.group(1)}', ausente da evidência.",
            )

    for nome, padrao in DIRECOES.items():
        if padrao.search(resumo_normalizado) and not padrao.search(evidencia_normalizada):
            return (
                MotivoRejeicao.RESUMO_INVENTA_EFEITO,
                f"O resumo afirma {nome} que a evidência não afirma.",
            )

    apoio = apoio_lexical(resumo_normalizado, evidencia_normalizada, chaves_origem)
    if apoio is not None and apoio < LIMIAR_APOIO_RESUMO:
        return (
            MotivoRejeicao.RESUMO_SEM_APOIO_NA_EVIDENCIA,
            f"Só {apoio:.0%} das palavras do resumo aparecem na evidência; "
            "o resumo deve descrever o que esta evidência afirma.",
        )
    return None


def validar_alvo(
    contexto: ContextoValidacao,
    *,
    alvo: str,
    tipo_alvo: TipoAlvo,
    resumo: str,
    inicio_evidencia: int,
    fim_evidencia: int,
    indice_janela: int,
    conferir_resumo: bool = True,
) -> RelacaoIdentificada | Rejeicao:
    """Valida um alvo contra a frase indicada, que é a evidência literal."""
    alvo_limpo = re.sub(r"\s+", " ", (alvo or "").strip())
    resumo_limpo = re.sub(r"\s+", " ", (resumo or "").strip())

    valida = 0 <= inicio_evidencia < fim_evidencia <= len(contexto.texto)
    evidencia = contexto.texto[inicio_evidencia:fim_evidencia] if valida else ""

    def rejeitar(motivo: MotivoRejeicao, detalhe: str) -> Rejeicao:
        return Rejeicao(
            motivo=motivo,
            alvo=alvo_limpo,
            tipo_alvo=str(tipo_alvo),
            resumo=resumo_limpo,
            evidencia_informada=evidencia,
            detalhe=detalhe,
            indice_janela=indice_janela,
        )

    alvo_normalizado = normalizar_citacao(alvo_limpo)
    if (
        not alvo_normalizado
        or len(alvo_limpo) > 150
        or not CARACTERES_ALVO.search(alvo_limpo)
        or (conferir_resumo and (not resumo_limpo or len(resumo_limpo) > contexto.max_caracteres_resumo))
    ):
        return rejeitar(
            MotivoRejeicao.ALVO_INVALIDO,
            "Alvo ou resumo vazio, sem letras ou acima do limite de caracteres.",
        )
    if e_a_origem(normalizar_alvo(alvo_limpo), contexto.chaves_origem):
        return rejeitar(
            MotivoRejeicao.ALVO_E_ORIGEM,
            "O alvo é o próprio medicamento de origem ou um de seus princípios ativos.",
        )
    if alvo_generico(alvo_normalizado, tipo_alvo):
        return rejeitar(
            MotivoRejeicao.ALVO_GENERICO,
            "O alvo descreve efeito, dose ou fisiologia, não uma substância, medicamento, "
            "classe, alimento ou exame.",
        )

    if not evidencia.strip():
        return rejeitar(
            MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA,
            "A frase indicada não existe no trecho de interações.",
        )

    evidencia_normalizada = mapear_texto(evidencia, unir_hifenizacao=True).texto
    if not mencoes_do_alvo(evidencia_normalizada, alvo_normalizado):
        return rejeitar(
            MotivoRejeicao.ALVO_AUSENTE_NA_EVIDENCIA,
            "O alvo não está escrito nesta frase; indique a frase que o cita.",
        )

    oracoes = oracoes_do_alvo(evidencia_normalizada, alvo_normalizado)
    if any(AUSENCIA.search(o) for o in oracoes) and not any(
        RECOMENDACAO_FORTE.search(o) for o in oracoes
    ):
        return rejeitar(
            MotivoRejeicao.RELACAO_NAO_POSITIVA,
            "A evidência declara ausência de estudos, de efeito ou de interação.",
        )

    problema = verificar_resumo(
        oracoes, evidencia_normalizada, resumo_limpo, contexto.chaves_origem
    ) if conferir_resumo else None
    if problema is not None:
        motivo, detalhe = problema
        if motivo == MotivoRejeicao.RESUMO_OMITE_NEGACAO:
            trecho = NEGACAO_ORIGINAL.search(re.sub(r"\s+", " ", evidencia))
            if trecho:
                detalhe += f" Inclua no resumo a restrição: \"{trecho.group(0).strip(' ,;.')}\"."
        return rejeitar(motivo, detalhe)

    return RelacaoIdentificada(
        alvo=alvo_limpo,
        tipo_alvo=tipo_alvo,
        resumo=resumo_limpo,
        evidencia=evidencia,
        inicio_evidencia=inicio_evidencia,
        fim_evidencia=fim_evidencia,
        indice_janela=indice_janela,
    )


def consolidar(relacoes: Iterable[RelacaoIdentificada]) -> tuple[list[RelacaoIdentificada], int]:
    """Deduplica por origem implícita, alvo normalizado, tipo e evidência.

    O resumo nunca entra na identidade. Repetições das janelas sobrepostas são
    consolidadas; quando uma evidência do mesmo alvo e tipo contém a outra, a
    mais completa é mantida. Evidências distintas permanecem separadas.
    """
    mantidas: list[RelacaoIdentificada] = []
    descartadas = 0
    for relacao in relacoes:
        chave = (normalizar_alvo(relacao.alvo), relacao.tipo_alvo)
        texto = mapear_texto(relacao.evidencia).texto
        substituida = False
        repetida = False
        for posicao, existente in enumerate(mantidas):
            if (normalizar_alvo(existente.alvo), existente.tipo_alvo) != chave:
                continue
            if texto == mapear_texto(existente.evidencia).texto or (
                existente.inicio_evidencia <= relacao.inicio_evidencia
                and relacao.fim_evidencia <= existente.fim_evidencia
            ):
                repetida = True
                break
            if (
                relacao.inicio_evidencia <= existente.inicio_evidencia
                and existente.fim_evidencia <= relacao.fim_evidencia
            ):
                mantidas[posicao] = relacao
                substituida = True
                break
        if repetida or substituida:
            descartadas += 1
            continue
        mantidas.append(relacao)
    mantidas.sort(key=lambda r: (r.inicio_evidencia, normalizar_alvo(r.alvo), r.tipo_alvo))
    return mantidas, descartadas
