"""Orquestra inferência serial, grava cada card e então consolida o resumo geral."""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from time import perf_counter
from types import SimpleNamespace

from pipelines.identificacao_interacoes.normalizacao import mapear_texto, normalizar_alvo
from pipelines.identificacao_interacoes.validacao import DIRECOES, oracoes_do_alvo
from pipelines.identificacao_interacoes.contexto_evidencia import efeito_do_bloco

from .prompts import (ESQUEMA, SEM_DETALHE, agrupar_cards, extrativo, grupos_por_efeito,
                      mensagens, mensagens_paragrafo, VERSAO_PROMPT, VERSAO_PROMPT_PILOTO)
from .repositorio import completo, preenchido, necessita_correcao, necessita_correcao_geral
from .validacao import limpar, validar_resposta, validar_fundamentacao, validar_contexto_geral
from .contexto import validar_entrada

LOGGER = logging.getLogger(__name__)

# Artigo ou contração antes do nome vira preposição simples, para o molde servir a
# qualquer medicamento: "pela amiodarona" → "por {alvo}", "A amiodarona" → "{alvo}".
PREPOSICAO = {"da": "de", "do": "de", "das": "de", "dos": "de", "pela": "por", "pelo": "por",
              "pelas": "por", "pelos": "por", "na": "em", "no": "em", "nas": "em", "nos": "em",
              "à": "a", "ao": "a", "às": "a", "aos": "a", "o": "", "os": ""}


@lru_cache(maxsize=8192)
def _oracoes(evidencia: str, alvo: str) -> tuple[str, ...]:
    return tuple(oracoes_do_alvo(mapear_texto(evidencia, unir_hifenizacao=True).texto, normalizar_alvo(alvo)))


def oracao_do_alvo(item: dict) -> tuple:
    """Trecho e orações que citam o alvo; alvos com a mesma chave estão na mesma frase da bula."""
    return item["evidencia"], _oracoes(item["evidencia"], item["alvo"])


def _direcao_antes(oracoes: tuple[str, ...], alvo: str) -> str | None:
    """Última palavra de efeito (aumento/redução) antes do alvo na oração."""
    texto = " ".join(oracoes)
    posicao = re.search(r"\b" + re.escape(normalizar_alvo(alvo)) + r"\b", texto)
    if not posicao:
        return None
    antes = texto[:posicao.start()]
    achados = [(m.end(), direcao) for direcao, padrao in DIRECOES.items() for m in padrao.finditer(antes)]
    return max(achados)[1] if achados else None


def chave_efeito(item: dict) -> tuple:
    """Mesma oração e mesmo efeito. Na oração "podem aumentar (ex.: ciclosporina) ou
    diminuir (ex.: lamotrigina)" cada alvo vem depois de um efeito diferente; já em
    "inibidores como paroxetina, fluoxetina... podem levar a aumento" todos compartilham."""
    evidencia, oracoes = oracao_do_alvo(item)
    return evidencia, oracoes, _direcao_antes(oracoes, item["alvo"])


class CitaOutros(ValueError):
    """Card válido, rejeitado só por citar outros medicamentos do trecho; guarda o texto."""

    def __init__(self, mensagem: str, texto: str):
        super().__init__(mensagem)
        self.texto = texto


def cita(texto: str, nome: str) -> bool:
    return re.search(r"\b" + re.escape(nome) + r"\b", texto, re.I) is not None


def molde(texto: str, alvo: str) -> str | None:
    """Card com o nome trocado por {alvo}; None se o nome não aparece exatamente uma vez."""
    achados = list(re.finditer(r"\b" + re.escape(alvo) + r"\b", texto, re.I))
    if len(achados) != 1:
        return None
    inicio, fim = achados[0].span()
    antes = texto[:inicio]
    anterior = re.search(r"(\w+)\s+$", antes)
    if anterior:
        palavra, resto = anterior.group(1), antes[:anterior.start()].rstrip()
        troca = PREPOSICAO.get(palavra.lower())
        # "a"/"as" só são artigo no início da frase ou após com/e/ou/que/como; senão, preposição.
        if palavra.lower() in ("a", "as") and (not resto or resto.endswith((".", "!", "?", ":"))
                                               or re.search(r"\b(?:com|e|ou|que|como)$", resto, re.I)):
            troca = ""
        if troca is not None:
            if troca and palavra[0].isupper():
                troca = troca.capitalize()
            antes = antes[:anterior.start()] + (troca + " " if troca else "")
    return antes + "{alvo}" + texto[fim:]


def preencher(molde_texto: str, alvo: str) -> str:
    antes = molde_texto[:molde_texto.index("{alvo}")].rstrip()
    nome = alvo[:1].upper() + alvo[1:] if not antes or antes.endswith((".", "!", "?")) else alvo
    return molde_texto.replace("{alvo}", nome, 1)


class CardsDoMesmoTrecho:
    """Decide de onde vem cada card de uma bula, para qualquer medicamento:

    - "literal": trecho curto, sem lista, com um só medicamento na frase; o card é a
      própria frase da bula, sem LLM (pedir ao 1.5B que copiasse falhava fora da Marevan).
    - "reaproveitado": outro alvo da mesma oração e do mesmo efeito já tem card; o texto
      é o mesmo, trocando só o nome do medicamento.
    - "llm": o modelo explica a interação com as próprias palavras.
    """

    def __init__(self, interacoes: list[dict], *, reaproveitar_gravados=True):
        self.na_oracao = Counter(oracao_do_alvo(i) for i in interacoes)
        self.nomes: dict[tuple, set[str]] = {}
        self.no_trecho: dict[str, set[str]] = {}
        for item in interacoes:
            self.nomes.setdefault(chave_efeito(item), set()).add(item["alvo"])
            self.no_trecho.setdefault(item["evidencia"], set()).add(item["alvo"])
        self.moldes: dict[tuple, str] = {}
        self.exemplos: dict[tuple, tuple[str, str]] = {}
        for item in interacoes if reaproveitar_gravados else []:
            if (preenchido(item.get("resumo_card")) and not necessita_correcao(item)
                    and not self.literal(item)):
                self.registrar(item, item["resumo_card"].strip())

    def literal(self, item: dict) -> bool:
        return extrativo(item["evidencia"]) and self.na_oracao[oracao_do_alvo(item)] == 1

    def card(self, item: dict, gerador: "GeradorResumos", nome: str) -> tuple[str, str]:
        """(texto, origem), com origem "literal", "reaproveitado" ou "llm"."""
        validar_entrada(item, nome)
        if self.literal(item):
            texto = limpar(item["evidencia"])
            return texto[:1].upper() + texto[1:], "literal"
        texto = self.moldes.get(chave_efeito(item))
        # Molde que já cita este alvo ("carbidopa com {alvo}" para levodopa) não serve.
        if texto and not cita(texto, item["alvo"]):
            return preencher(texto, item["alvo"]), "reaproveitado"
        dados = [{k: item[k] for k in ("alvo", "tipo_alvo", "evidencia")}]
        dados[0]["efeito"] = efeito_do_bloco(item["evidencia"])
        exemplo = self.exemplos.get(chave_efeito(item))
        outros = tuple(sorted(self.no_trecho.get(item["evidencia"], set()) - {item["alvo"]}))
        return self.registrar(item, gerador.gerar(nome, dados, exemplo=exemplo, outros=outros)), "llm"

    def registrar(self, item: dict, texto: str) -> str:
        """Guarda o card como molde dos irmãos e devolve o texto final já nesse formato."""
        chave = chave_efeito(item)
        outros = self.nomes.get(chave, set()) - {item["alvo"]}
        if texto == SEM_DETALHE or not outros:
            return texto
        self.exemplos.setdefault(chave, (item["alvo"], texto))
        texto_molde = molde(texto, item["alvo"])
        # Texto que cita outro alvo do grupo ("A e B podem...") não vira molde.
        if texto_molde is None or any(cita(texto_molde, outro) for outro in outros):
            return texto
        self.moldes.setdefault(chave, texto_molde)
        return preencher(self.moldes[chave], item["alvo"])


class GeradorResumos:
    def __init__(self, provedor, config, *, tokens_card=192, tokens_geral=320, prompt_simplificado=False):
        self.provedor = provedor
        self.config = config
        self.tempo_inferencia = 0.0
        self.chamadas = 0
        self.tokens_card = tokens_card
        self.tokens_geral = tokens_geral
        self.registros = []
        self.prompt_simplificado = prompt_simplificado
        self.versao_prompt = VERSAO_PROMPT_PILOTO if prompt_simplificado else VERSAO_PROMPT

    def gerar(self, nome: str, dados: list[dict], *, geral=False, exemplo=None, outros=()) -> str:
        """Card: dados = [interação]; `exemplo` = (alvo, card já escrito) do mesmo trecho;
        `outros` = demais medicamentos do trecho. Geral: dados = cards gravados ({alvo, resumo})."""
        if not geral:
            return self._tentar(nome, dados, paragrafo=False, exemplo=exemplo, outros=outros)
        grupos = grupos_por_efeito(agrupar_cards(dados))
        paragrafos = []
        for indice, linhas in enumerate(grupos, 1):
            print(f"  Resumo geral: parágrafo {indice}/{len(grupos)} ({len(linhas)} linhas de cards)", flush=True)
            try:
                paragrafos.append(self._tentar(nome, linhas, paragrafo=True))
            except Exception as erro:
                # Os cards do grupo já foram escritos pelo LLM (ou são a frase da bula):
                # um parágrafo que falha duas vezes não deixa a bula sem resumo geral.
                LOGGER.warning("Parágrafo %s de %s montado com os cards após falha: %s", indice, nome, erro)
                print(f"  Parágrafo {indice}: LLM falhou ({erro}); usando os textos dos cards do grupo.", flush=True)
                bula = nome[:1].upper() + nome[1:]
                fallback = f"A bula de {bula} descreve estas interações: " + " ".join(limpar(linha) for linha in linhas)
                validar_contexto_geral(fallback, nome)
                validar_fundamentacao(fallback, "\n".join(linhas))
                paragrafos.append(fallback)
        texto = "\n\n".join(paragrafos)
        validar_contexto_geral(texto, nome)
        return texto

    def _tentar(self, nome, dados, **opcoes):
        try:
            return self._gerar(nome, dados, **opcoes)
        except ValueError as erro:
            if "excedem o contexto" in str(erro):
                raise
            LOGGER.warning("Resposta rejeitada para %s; única nova tentativa: %s", nome, erro)
            print(f"  Resposta rejeitada: {erro} Nova tentativa (2/2).", flush=True)
            try:
                return self._gerar(nome, dados, correcao=str(erro), **opcoes)
            except ValueError as segundo:
                # A primeira só citava outros medicamentos: melhor que deixar o card sem
                # texto (com temperatura zero, a bula falharia sempre do mesmo jeito).
                if isinstance(erro, CitaOutros):
                    LOGGER.warning("Nova tentativa falhou (%s); mantida a primeira resposta.", segundo)
                    print(f"  Nova tentativa falhou ({segundo}); mantida a primeira resposta.", flush=True)
                    return erro.texto
                raise

    def _gerar(self, nome: str, dados: list, *, paragrafo=False, exemplo=None, outros=(), correcao=None) -> str:
        if paragrafo:
            entrada, evidencia = mensagens_paragrafo(dados, nome), "\n".join(dados)
        else:
            if not dados[0].get("efeito"):
                dados = [{**dados[0], "efeito": efeito_do_bloco(dados[0]["evidencia"])}]
            entrada = mensagens(nome, dados, exemplo, outros, simplificado=self.prompt_simplificado)
            evidencia = dados[0]["evidencia"]
        if correcao:
            # Curta e no fim do pedido: assim o 1.5B reescreveu "A interação com racecadotrila
            # aumenta o risco de angioedema." No prompt de sistema ele a ignorava; como diálogo
            # com a resposta rejeitada, repetia essa resposta. O eco desta frase é barrado
            # na validação (no geral da Aerotrat ele copiou "A tentativa anterior foi rejeitada.").
            entrada[-1] = {**entrada[-1], "content": entrada[-1]["content"] + f"\nAtenção: {correcao}"}
            if not paragrafo and dados[0].get("efeito"):
                entrada[-1]["content"] += f"\nComece o resumo pelo nome {dados[0]['alvo']}."
        tokens = self.provedor.contar_tokens(json.dumps(entrada, ensure_ascii=False))
        limite = self.tokens_geral if paragrafo else self.tokens_card
        if tokens + limite + 256 > self.config.tamanho_contexto:
            raise ValueError("Evidências excedem o contexto configurado; nenhum texto foi cortado. "
                             "Ajuste --contexto antes de tentar novamente.")
        inicio = perf_counter()
        self.chamadas += 1
        try:
            resposta = self.provedor.analisar(entrada, esquema=ESQUEMA, max_tokens=limite)
            LOGGER.info("Resposta bruta %s %s: %s", nome, "parágrafo geral" if paragrafo else dados[0].get("alvo"),
                        resposta.conteudo)
        finally:
            decorrido = perf_counter() - inicio
            self.tempo_inferencia += decorrido
            LOGGER.info("Chamada LLM %s: %.3fs", self.chamadas, decorrido)
            print(f"  Chamada LLM {self.chamadas}: {decorrido:.1f}s", flush=True)
        texto = validar_resposta(resposta, evidencia, paragrafo=paragrafo)
        if paragrafo:
            validar_contexto_geral(texto, nome)
            return texto
        if texto == SEM_DETALHE:
            return texto
        normal = normalizar_alvo(texto)
        alvo = normalizar_alvo(dados[0]["alvo"])
        if alvo not in normal and dados[0].get("efeito"):
            # O modelo às vezes resume só o efeito compartilhado. O rótulo identifica
            # o integrante confirmado na lista, sem inventar uma nova frase clínica.
            lista = evidencia[:evidencia.find(dados[0]["efeito"])]
            if cita(normalizar_alvo(lista), alvo):
                texto = dados[0]["alvo"][:1].upper() + dados[0]["alvo"][1:] + ": " + texto
                texto = validar_resposta(SimpleNamespace(conteudo=json.dumps({"texto": texto}), truncada=False), evidencia)
                normal = normalizar_alvo(texto)
        if alvo not in normal:
            raise ValueError("Resumo não menciona o alvo da interação.")
        if normalizar_alvo(nome) + " (" + alvo in normal:
            raise ValueError("Resumo associa indevidamente a origem ao princípio ativo alvo.")
        # Só na primeira tentativa: a nova recebe os nomes na correção e, se ainda os
        # citar, é aceita — o card não fica sem texto por isso.
        citados = [outro for outro in outros if cita(texto, outro)] if correcao is None else []
        if citados:
            raise CitaOutros(f"O card cita outros medicamentos do trecho ({', '.join(citados)}); "
                             f"escreva só sobre {dados[0]['alvo']}, sem citar esses nomes.", texto)
        return texto


def identidade(fonte):
    if fonte is None:
        return None
    return (fonte["processamento_id"], fonte["hash_trecho"],
            [(i["id"], i["alvo"], i["tipo_alvo"], i["evidencia"]) for i in fonte["interacoes"]])


def processar(repositorio, nome: str, gerador: GeradorResumos, *, reprocessar=False,
              limite_interacoes=None, corrigir_inconsistentes=False,
              informar=lambda texto: print(texto, flush=True)) -> dict:
    inicio = perf_counter()
    iniciado_em = datetime.now(timezone.utc)
    gerador.tempo_inferencia = 0.0
    gerador.chamadas = 0
    resultado = {"nome_normalizado": nome, "status": "FALHA", "quantidade_interacoes": 0,
                 "chamadas_llm": 0, "tempo_inferencia_segundos": 0.0,
                 "tempo_total_segundos": 0.0, "erro": "", "gravacao_parcial": False}
    modelo = gerador.config.arquivo_modelo
    with repositorio.bloquear(nome) as obtido:
        if not obtido:
            return {**resultado, "status": "EM_ANDAMENTO"}
        busca = perf_counter()
        fonte = repositorio.fonte(nome)
        informar(f"Busca no banco: {perf_counter() - busca:.3f}s")
        if fonte is None or not fonte["interacoes"]:
            return {**resultado, "status": "SEM_INTERACOES_ELEGIVEIS"}
        informar(f"Medicamento: {nome}; interações encontradas: {len(fonte['interacoes'])}")
        corrigir = {i["id"] for i in fonte["interacoes"] if corrigir_inconsistentes and necessita_correcao(i)}
        corrigir_geral = corrigir_inconsistentes and necessita_correcao_geral(fonte, nome)
        if not reprocessar and not corrigir and not corrigir_geral and completo(fonte):
            informar("Os dois tipos de resumo já estão preenchidos; nenhuma inferência.")
            return {**resultado, "status": "IGNORADO_JA_PROCESSADO"}
        repositorio.iniciar(fonte)
        pendentes = [i for i in fonte["interacoes"]
                     if reprocessar or i["id"] in corrigir
                     or (not corrigir_inconsistentes and not preenchido(i.get("resumo_card")))]
        selecionadas = pendentes[:limite_interacoes] if limite_interacoes else pendentes
        erros = []
        gravados = 0
        # Ao retomar, os cards já gravados servem de molde; --reprocessar não os reaproveita.
        cards = CardsDoMesmoTrecho(fonte["interacoes"], reaproveitar_gravados=not reprocessar)
        for indice, item in enumerate(selecionadas, 1):
            chamada = perf_counter()
            informar(f"[{indice}/{len(selecionadas)}] Gerando resumo para alvo: {item['alvo']} (id {item['id']})")
            registro = {"medicamento": nome, "id": item["id"], "alvo": item["alvo"],
                        "evidencia": item["evidencia"], "resumo": None, "erro": None}
            try:
                texto, origem = cards.card(item, gerador, nome)
                validar_fundamentacao(texto, item["evidencia"])
                if origem == "literal":
                    informar("  Trecho curto com um só medicamento: o card é a própria frase da bula.")
                elif origem == "reaproveitado":
                    informar("  Mesma frase e mesmo efeito de um card anterior: mesmo texto, trocando só o medicamento.")
                # resumo_modelo registra que o card literal não passou pelo LLM.
                repositorio.gravar_card(fonte, item, texto, "trecho_literal" if origem == "literal" else modelo,
                                        perf_counter() - chamada, origem=origem)
                registro.update(resumo=texto, origem=origem)
                gravados += 1
                informar(f"[{indice}/{len(selecionadas)}] concluído e gravado em {perf_counter() - chamada:.1f}s")
            except Exception as erro:
                detalhe = f"Interação {item['id']} ({item['alvo']}): {type(erro).__name__}: {erro}"
                LOGGER.exception(detalhe)
                registro["erro"] = detalhe
                erros.append(detalhe)
                informar(f"[{indice}/{len(selecionadas)}] ERRO após {perf_counter() - chamada:.1f}s: {erro}")
                try:
                    repositorio.gravar_card(fonte, item, None, modelo, perf_counter() - chamada, detalhe)
                except Exception:
                    LOGGER.exception("Falha ao registrar erro do card %s", item["id"])
            registro["segundos"] = round(perf_counter() - chamada, 3)
            gerador.registros.append(registro)
        geral = fonte.get("resumo_geral")
        try:
            atual = repositorio.fonte(nome)
            if identidade(atual) != identidade(fonte):
                raise ValueError("Identificação/fonte mudou durante a geração.")
            faltantes = [i for i in atual["interacoes"] if not preenchido(i.get("resumo_card"))]
            if erros:
                resultado["status"] = "FALHA"
            elif faltantes or len(selecionadas) < len(pendentes):
                resultado["status"] = "PARCIAL"
            else:
                if reprocessar or corrigir or corrigir_geral or not preenchido(geral):
                    informar("Gerando resumo geral a partir dos cards gravados...")
                    dados = [{"alvo": i["alvo"], "resumo": i["resumo_card"]} for i in atual["interacoes"]]
                    geral = gerador.gerar(nome, dados, geral=True)
                resultado["status"] = "CONCLUIDO"
        except Exception as erro:
            LOGGER.exception("Falha no resumo geral de %s", nome)
            erros.append(f"Resumo geral: {type(erro).__name__}: {erro}")
            resultado["status"] = "FALHA"
            informar(erros[-1])
        resultado.update(quantidade_interacoes=gravados, chamadas_llm=gerador.chamadas,
                         tempo_inferencia_segundos=round(gerador.tempo_inferencia, 3),
                         tempo_total_segundos=round(perf_counter() - inicio, 3),
                         erro=" | ".join(erros), gravacao_parcial=gravados > 0 and resultado["status"] != "CONCLUIDO")
        repositorio.gravar(fonte, modelo, iniciado_em, resultado, geral, {})
        resultado["tempo_total_segundos"] = round(perf_counter() - inicio, 3)
        informar(f"{nome}: {resultado['status']}; {gravados} cards gravados; {gerador.chamadas} chamadas; "
                 f"gravação parcial: {resultado['gravacao_parcial']}; total {resultado['tempo_total_segundos']:.1f}s")
    return resultado
