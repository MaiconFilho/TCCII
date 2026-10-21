"""Identificação por nomes do banco: medicamento × medicamento, sem modelo.

Para cada bula, procura no tópico de interações os nomes que existem no banco
(princípios ativos, também sem sal, e nomes de medicamentos) e registra uma
relação por nome citado em cada trecho, ligada aos medicamentos do banco que
têm esse nome. Classes, alimentos e substâncias sem bula no banco ficam de
fora por construção.

Regras, todas determinísticas:

- trechos: frases e itens de lista do texto original; a evidência é sempre um
  recorte literal dele, com o começo da frase quando possível (ver
  ``recortar_evidencia``);
- o nome mais longo vence ("ácido acetilsalicílico" antes de "ácido");
- descartes: a própria origem, também por outro nome ou sigla da mesma
  substância; oração que nega a interação ("não foram observadas
  interações", "não alterou a farmacocinética"); nome citado como exceção
  ("exceto a hidrocortisona"); termos de ``termos_ignorados.txt``; minerais e
  substâncias do organismo citados como medida ou mecanismo ("níveis séricos
  de potássio", "recaptação de dopamina"); avisos de excipientes; nomes sem
  nenhum medicamento do banco além da origem;
- até ``MAX_EVIDENCIAS_POR_ALVO`` trechos distintos por nome, na ordem do texto;
- resumo: o que vem depois de "Nome:" quando o item é estruturado; senão, a
  própria evidência. Nenhum texto é gerado.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

from .modelos import (
    FonteInteracoes,
    MotivoRejeicao,
    RelacaoIdentificada,
    Rejeicao,
    ResultadoIdentificacao,
    StatusProcessamento,
    TipoAlvo,
)
from .normalizacao import mapear_texto, normalizar_alvo
from .contexto_evidencia import blocos_estruturados, efeito_do_bloco
from .resolucao import CatalogoMedicamentos, palavras_chave, substancias
from .validacao import AUSENCIA, SEPARADOR_ORACOES, VERSAO_VALIDACAO, chaves_da_origem, e_a_origem


VERSAO = "dicionario/1"
MODELO = "nenhum (busca por nomes do banco)"
ARQUIVO_IGNORADOS = Path(__file__).with_name("termos_ignorados.txt")
MAX_EVIDENCIA = 1200
JANELA_EVIDENCIA = 400
JANELA_DEPOIS = 200
MAX_RESUMO = 300
MAX_EVIDENCIAS_POR_ALVO = 3

# Fronteiras de trecho no texto original: linha em branco, item de lista e
# fim de frase seguido de maiúscula, número ou marcador.
MARCADORES = "-•▪●◦*"
SEPARADOR_TRECHOS = re.compile(
    r"\n[ \t]*\n"
    rf"|\n(?=[ \t]*[{re.escape(MARCADORES)}])"
    rf"|(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ\d{re.escape(MARCADORES)}])"
)
# Nome citado como exceção, até o fim da oração: "exceto a hidrocortisona ...:",
# "(mas não lansoprazol ou pantoprazol)".
EXCECAO = re.compile(
    r"\b(?:exceto|salvo|excluindo|com excecao d[aeo]s?|a excecao d[aeo]s?|mas\s+nao)\b[^:;.()]*"
)
# Ausência de interação além das formas já reconhecidas pela validação.
AUSENCIA_EXTRA = re.compile(
    r"\bnao\s+houve\s+(?:\w+\s+){0,3}?(?:alterac|interac|efeito|mudanc|diferenc|aument|reduc|diminuic)\w*"
    r"|\bnao\s+interag\w*"
    r"|\bnao\s+(?:apresent|demonstr|mostr|produz|caus|result|revel)\w*\s+(?:\w+\s+){0,3}?"
    r"(?:interac|alterac|efeito|mudanc|diferenc)\w*"
    r"|\bnenhum\w*\s+(?:\w+\s+){0,2}?(?:interac|alterac|efeito)\w*"
    r"|\bnao\s+(?:sao|foram|e|foi)\s+(?:\w+\s+){0,2}?(?:clinicamente\s+)?(?:significativ|relevant)\w*"
    r"|\bnao\s+(?:ha|existem|estao\s+disponiveis|foram\s+encontrad\w+)\s+(?:\w+\s+){0,3}?"
    r"(?:dados|estudos|relatos|informac\w*|evidencias)"
    r"|\b(?:dados|estudos|informac\w+)\s+(?:\w+\s+){0,3}?nao\s+(?:estao\s+)?disponive\w*"
    r"|\bnao\s+(?:teve|tem|tiveram|possui\w*|exerce\w*|provoc\w*)\s+(?:\w+\s+){0,3}?"
    r"(?:efeito|interac|alterac|impacto|influencia|mudanc)\w*"
    r"|\bnao\s+(?:inib|induz|potencializ|aument|diminu|reduz|alter|modific|afet)\w*\s+(?:\w+\s+){0,2}?"
    r"(?:metabolismo|efeito|concentrac|absorc|depurac|eliminac|farmacocinetica|exposicao|atividade"
    r"|nivel|niveis|biodisponibilidade|ligacao)\w*"
    r"|\bnao\s+(?:foram|foi)\s+(?:\w+\s+){0,2}?(?:conduzid|efetuad|investigad|testad|examinad)\w*"
    r"|\bnao\s+(?:havia|haviam|houveram|existia|existiam)\s+(?:\w+\s+){0,3}?"
    r"(?:interac|alterac|efeito|diferenc|mudanc)\w*"
)
# Enantiômero e racemato são a mesma substância ativa: "cetirizina" na bula da
# levocetirizina, "omeprazol" na do esomeprazol.
PREFIXOS_ENANTIOMERO = ("levo", "lev", "dextro", "dex", "es", "ar")


def mesma_substancia(termo: str, substancias_origem: frozenset[str]) -> bool:
    for substancia in substancias_origem:
        for maior, menor in ((substancia, termo), (termo, substancia)):
            if maior != menor and maior.endswith(menor) and maior[: -len(menor)] in PREFIXOS_ENANTIOMERO:
                return True
    return False
# Substâncias que também são parâmetros do organismo ou mecanismos: valem como
# medicamento ("suplementos de potássio", "epinefrina"), não como medida ou
# mecanismo ("níveis séricos de potássio", "recaptação de dopamina").
CONTEXTUAIS = frozenset({
    "calcio", "magnesio", "potassio", "ferro", "zinco",
    "dopamina", "norepinefrina", "epinefrina", "noradrenalina", "adrenalina", "serotonina", "histamina",
})
CONTEXTO_ANTES = frozenset({
    "nivel", "niveis", "concentracao", "concentracoes", "serico", "serica", "sericos", "sericas",
    "plasmatico", "plasmatica", "plasmaticos", "plasmaticas", "sanguineo", "sanguinea", "urinario",
    "urinaria", "excrecao", "eliminacao", "perda", "perdas", "reabsorcao", "retencao", "deplecao",
    "ligacao", "ligado", "ligada", "ligados", "dosagem", "determinacao", "teste", "testes", "exame",
    "exames", "valor", "valores", "taxa", "taxas", "metabolismo", "homeostase", "equilibrio",
    "balanco", "poupador", "poupadores", "depletor", "depletores", "espoliador", "espoliadores",
    "canal", "canais", "antagonista", "antagonistas", "agonista", "agonistas", "bloqueador",
    "bloqueadores", "receptor", "receptores", "recaptacao", "liberacao", "transportador",
    "transportadores", "influxo", "fluxo", "mobilizacao", "transporte", "oxido", "dioxido",
})
CONTEXTO_DEPOIS = frozenset({
    "serico", "serica", "sericos", "sericas", "plasmatico", "plasmatica", "sanguineo", "sanguinea",
    "urinario", "urinaria", "ionico", "ionizado", "corporal", "intracelular", "extracelular",
    "estriatal", "endogena", "endogeno",
})
JANELA_CONTEXTO = 4
JANELA_NEGACAO = 250
# Aviso de excipientes ou composição dentro do tópico: não cita interação.
EXCIPIENTES = re.compile(r"^atencao\s*:?\s*contem\b|\bexcipientes?\b|\bcorantes?\b")
INVISIVEIS = dict.fromkeys(map(ord, "­​‌‍﻿"))


def carregar_ignorados(caminho: Path = ARQUIVO_IGNORADOS) -> frozenset[str]:
    """Uma entrada por linha; ``#`` inicia comentário. Comparação em palavras."""
    termos = set()
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        termo = palavras_chave(normalizar_alvo(linha.split("#", 1)[0]))
        if termo:
            termos.add(termo)
    return frozenset(termos)


def trechos(texto: str) -> list[tuple[int, int]]:
    """Faixas [início, fim) de frases e itens, sem os espaços das bordas."""
    faixas, inicio = [], 0
    blocos = blocos_estruturados(texto)
    for separador in SEPARADOR_TRECHOS.finditer(texto):
        if any(a < separador.start() < b for a, b in blocos):
            continue
        faixas.append((inicio, separador.start()))
        inicio = separador.end()
    faixas.append((inicio, len(texto)))
    saida = []
    for a, b in faixas:
        while a < b and texto[a].isspace():
            a += 1
        while b > a and texto[b - 1].isspace():
            b -= 1
        if b > a:
            saida.append((a, b))
    return saida


def recortar_evidencia(texto: str, trecho: tuple[int, int], alvo: tuple[int, int]) -> tuple[int, int]:
    """Recorte literal do trecho que mantém, sempre que possível, o efeito descrito.

    Até ``MAX_EVIDENCIA`` caracteres, o trecho inteiro. Mais longo, do começo
    da frase (onde costuma estar o efeito: "A atividade da varfarina pode ser
    potencializada por ...") até pouco depois do nome; se o nome estiver longe
    do começo, como em tabelas sem pontuação, uma janela em volta dele.
    """
    a, b = trecho
    # Um limite de caracteres não pode separar a lista do efeito que a descreve.
    bloco = next(((x, y) for x, y in blocos_estruturados(texto[a:b])
                  if x <= alvo[0] - a < y), None)
    if bloco:
        return a + bloco[0], a + bloco[1]
    if b - a <= MAX_EVIDENCIA:
        return a, b
    if alvo[1] - a <= MAX_EVIDENCIA - JANELA_DEPOIS:
        inicio, fim = a, min(b, alvo[1] + JANELA_DEPOIS)
    else:
        inicio = max(a, alvo[0] - JANELA_EVIDENCIA // 3)
        fim = min(b, max(alvo[1], inicio + JANELA_EVIDENCIA))
        quebra = texto.rfind("\n", inicio, alvo[0])
        if quebra >= 0:
            inicio = quebra + 1
        elif inicio > a:
            espaco = texto.find(" ", inicio, alvo[0])
            inicio = espaco + 1 if espaco >= 0 else inicio
    if fim < b:
        espaco = texto.rfind(" ", alvo[1], fim)
        fim = espaco if espaco >= 0 else fim
    while inicio < fim and texto[inicio].isspace():
        inicio += 1
    while fim > inicio and texto[fim - 1].isspace():
        fim -= 1
    return inicio, fim


def compactar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().strip(MARCADORES + " ").strip()


def resumir(evidencia: str, inicio_alvo: int) -> str:
    """Recorte literal: depois de "Nome:" em item estruturado, senão a evidência."""
    texto = evidencia
    dois_pontos = evidencia.find(":")
    if 0 <= dois_pontos <= 160 and inicio_alvo < dois_pontos:
        depois = evidencia[dois_pontos + 1:]
        if len(re.sub(r"\W", "", depois)) >= 10:
            texto = depois
    resumo = compactar(texto) or compactar(evidencia)
    if len(resumo) > MAX_RESUMO:
        corte = resumo.rfind(" ", 0, MAX_RESUMO - 1)
        resumo = resumo[: corte if corte > 0 else MAX_RESUMO - 1].rstrip(" ,;:") + "…"
    return resumo


def nome_exibido(original: str, termo: str) -> str:
    """O nome como está no texto, sem quebra nem hifenização de linha."""
    nome = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", original.translate(INVISIVEIS))
    nome = re.sub(r"\s+", " ", nome).strip()
    return nome if palavras_chave(normalizar_alvo(nome)) == termo else termo


class ServicoDicionario:
    """Mesma interface do serviço com LLM: ``versao_processamento``, ``modelo`` e ``identificar``."""

    def __init__(self, catalogo: CatalogoMedicamentos, ignorados: frozenset[str] | None = None) -> None:
        self.catalogo = catalogo
        self.ignorados = carregar_ignorados() if ignorados is None else ignorados
        self.termos = {
            termo: tipo for termo, tipo in catalogo.termos().items()
            if len(termo) >= 3 and not termo.replace(" ", "").isdigit()
        }
        comprimentos: dict[str, set[int]] = defaultdict(set)
        for termo in self.termos:
            palavras = termo.split()
            comprimentos[palavras[0]].add(len(palavras))
        self._por_primeira = {p: sorted(c, reverse=True) for p, c in comprimentos.items()}
        self.modelo = MODELO

    @property
    def versao_processamento(self) -> str:
        codigo = hashlib.sha256()
        for arquivo in (Path(__file__), Path(__file__).with_name("resolucao.py"),
                        Path(__file__).with_name("contexto_evidencia.py"), ARQUIVO_IGNORADOS):
            codigo.update(arquivo.read_bytes())
        codigo.update("\n".join(sorted(self.ignorados)).encode("utf-8"))
        banco = hashlib.sha256("\n".join(
            f"{t}\t{tipo.value}" for t, tipo in sorted(self.termos.items())
        ).encode("utf-8"))
        return f"{VERSAO}#{codigo.hexdigest()[:12]}|banco={banco.hexdigest()[:12]}|{VERSAO_VALIDACAO}"

    # ------------------------------------------------------------- busca

    def _encontrar(self, palavras: list[re.Match]) -> list[tuple[int, int, str]]:
        """(primeira, última+1, termo): o mais longo primeiro, sem sobreposição."""
        achados, i = [], 0
        while i < len(palavras):
            for tamanho in self._por_primeira.get(palavras[i].group(), ()):
                if i + tamanho > len(palavras):
                    continue
                termo = " ".join(p.group() for p in palavras[i:i + tamanho])
                if termo in self.termos:
                    achados.append((i, i + tamanho, termo))
                    i += tamanho
                    break
            else:
                i += 1
        return achados

    def identificar(self, fonte: FonteInteracoes) -> ResultadoIdentificacao:
        resultado = ResultadoIdentificacao(
            status=StatusProcessamento.FALHA, nome_normalizado=fonte.nome_normalizado
        )
        texto = fonte.trecho_interacoes or ""
        if not texto.strip():
            resultado.detalhe_erro = "trecho_interacoes vazio."
            return resultado
        origem = chaves_da_origem(fonte.nome_normalizado, fonte.nome_exibicao, fonte.principios_ativos)
        substancias_origem = substancias(fonte.principios_ativos)
        faixas = trechos(texto)
        relacoes: list[RelacaoIdentificada] = []
        rejeicoes: dict[tuple, Rejeicao] = {}
        evidencias_por_termo: dict[str, set[str]] = defaultdict(set)
        omitidas = 0

        def rejeitar(motivo, termo, tipo, trecho, detalhe):
            chave = (motivo, termo, detalhe)
            if chave not in rejeicoes:
                rejeicoes[chave] = Rejeicao(
                    motivo, termo, tipo.value, "", compactar(trecho)[:200], detalhe, 0
                )

        for a, b in faixas:
            original = texto[a:b]
            efeito_compartilhado = mapear_texto(efeito_do_bloco(original)).texto
            mapa = mapear_texto(original, unir_hifenizacao=True)
            palavras = list(re.finditer(r"[a-z0-9]+", mapa.texto))
            if not palavras:
                continue
            excipientes = EXCIPIENTES.search(mapa.texto) is not None
            excecoes = [m.span() for m in EXCECAO.finditer(mapa.texto)]
            oracoes = [0] + [m.end() for m in SEPARADOR_ORACOES.finditer(mapa.texto)] + [len(mapa.texto) + 1]
            letras = [c for c in original if c.isalpha()]
            caixa_alta = bool(letras) and sum(c.isupper() for c in letras) / len(letras) > 0.6
            for primeira, depois, termo in self._encontrar(palavras):
                tipo = self.termos[termo]
                ini_norm, fim_norm = palavras[primeira].start(), palavras[depois - 1].end()
                ini, fim = mapa.intervalo_original(ini_norm, fim_norm)
                citado = original[ini:fim]
                if len(termo) < 4 and (caixa_alta or not citado.isupper()):
                    continue  # siglas curtas só valem escritas em maiúsculas ("AAS")
                if termo in self.ignorados:
                    rejeitar(MotivoRejeicao.ALVO_GENERICO, termo, tipo, original, "Termo ambíguo da lista de ignorados.")
                    continue
                if e_a_origem(termo, origem):
                    rejeitar(MotivoRejeicao.ALVO_E_ORIGEM, termo, tipo, original, "O nome é da própria origem.")
                    continue
                if mesma_substancia(termo, substancias_origem):
                    rejeitar(MotivoRejeicao.ALVO_E_ORIGEM, termo, tipo, original, "Enantiômero ou racemato da origem.")
                    continue
                if excipientes:
                    rejeitar(MotivoRejeicao.ALVO_GENERICO, termo, tipo, original, "Trecho de excipientes.")
                    continue
                if any(x <= ini_norm < y for x, y in excecoes):
                    rejeitar(MotivoRejeicao.RELACAO_NAO_POSITIVA, termo, tipo, original, "Citado como exceção.")
                    continue
                if termo in CONTEXTUAIS and (
                    CONTEXTO_ANTES.intersection(
                        p.group() for p in palavras[max(0, primeira - JANELA_CONTEXTO):primeira]
                    )
                    or (depois < len(palavras) and palavras[depois].group() in CONTEXTO_DEPOIS)
                ):
                    rejeitar(MotivoRejeicao.ALVO_GENERICO, termo, tipo, original, "Citado como parâmetro do organismo.")
                    continue
                # A oração do nome, limitada a uma janela: em tabelas sem
                # pontuação, um "não" distante não nega todos os nomes.
                oracao_ini = max(max(x for x in oracoes if x <= ini_norm), ini_norm - JANELA_NEGACAO)
                oracao_fim = min(min(x for x in oracoes if x > ini_norm), fim_norm + JANELA_NEGACAO)
                oracao = mapa.texto[oracao_ini:oracao_fim]
                if (AUSENCIA.search(oracao) or AUSENCIA_EXTRA.search(oracao)
                        or AUSENCIA.search(efeito_compartilhado) or AUSENCIA_EXTRA.search(efeito_compartilhado)):
                    rejeitar(MotivoRejeicao.RELACAO_NAO_POSITIVA, termo, tipo, original, "Oração nega a interação.")
                    continue
                alvo = nome_exibido(citado, termo)
                vinculos = self.catalogo.resolver(alvo, tipo, fonte.nome_normalizado)
                if not vinculos:
                    rejeitar(MotivoRejeicao.ALVO_INVALIDO, termo, tipo, original, "Sem medicamento do banco além da origem.")
                    continue
                if substancias_origem and all(
                    self.catalogo.substancias_de(v.medicamento_relacionado)
                    and self.catalogo.substancias_de(v.medicamento_relacionado) <= substancias_origem
                    for v in vinculos
                ):
                    # Outra marca ou sigla da mesma substância ("LUR" na bula da lurasidona).
                    rejeitar(MotivoRejeicao.ALVO_E_ORIGEM, termo, tipo, original, "Outro nome da mesma substância da origem.")
                    continue
                ev_ini, ev_fim = recortar_evidencia(texto, (a, b), (a + ini, a + fim))
                evidencia = texto[ev_ini:ev_fim]
                chave_evidencia = compactar(evidencia).casefold()
                vistas = evidencias_por_termo[termo]
                if chave_evidencia in vistas:
                    continue  # mesmo trecho repetido (outra apresentação no mesmo PDF)
                if len(vistas) >= MAX_EVIDENCIAS_POR_ALVO:
                    omitidas += 1
                    continue
                vistas.add(chave_evidencia)
                relacoes.append(RelacaoIdentificada(
                    alvo=alvo,
                    tipo_alvo=tipo,
                    resumo=resumir(evidencia, a + ini - ev_ini),
                    evidencia=evidencia,
                    inicio_evidencia=ev_ini,
                    fim_evidencia=ev_fim,
                    indice_janela=0,
                ))
        resultado.relacoes = relacoes
        resultado.rejeicoes = list(rejeicoes.values())
        resultado.quantidade_janelas = resultado.janelas_concluidas = max(1, len(faixas))
        resultado.status = (
            StatusProcessamento.CONCLUIDO_COM_RELACOES if relacoes else StatusProcessamento.CONCLUIDO_SEM_RELACOES
        )
        if omitidas:
            resultado.avisos.append(
                f"{omitidas} ocorrência(s) além de {MAX_EVIDENCIAS_POR_ALVO} trechos por nome não foram gravadas."
            )
        return resultado
