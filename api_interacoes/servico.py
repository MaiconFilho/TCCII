"""Regras de consulta: estados explícitos e conclusões limitadas às fontes.

- O indicador binário só é ``false`` quando a análise está concluída sem
  pendências; ausência de registros nunca vira conclusão negativa.
- A mensagem negativa é sempre "não foi identificada interação nas fontes
  analisadas", sem afirmar inexistência.
- A lista não é exaustiva enquanto o corpus não estiver todo analisado.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import OrderedDict

from scraping_anvisa.anvisa_scraper.normalizacao import normalizar_nome
from identificacao_interacoes.normalizacao import mapear_texto, normalizar_alvo
from identificacao_interacoes.validacao import AUSENCIA, RECOMENDACAO_FORTE, oracoes_do_alvo
from .configuracao import ConfiguracaoApi
from .contratos import (
    AnaliseBula,
    Cobertura,
    EstadoAnalise,
    EvidenciaPar,
    FonteBula,
    ItemPar,
    Medicamento,
    MedicamentoRelacionado,
    Paginacao,
    Processamento,
    Relacao,
    RespostaInteracoes,
    RespostaPar,
    RespostaSugestoes,
    Sentido,
    SentidoPar,
    Sugestao,
    TotaisPorSentido,
)
from .erros import MedicamentoNaoEncontradoError, ParametroInvalidoError


def chave_citacao(texto: str) -> str:
    """Mesmo trecho com quebras/espaços ou caixa diferentes no PDF."""
    return re.sub(r"\s+", "", texto).casefold()


def relacao_nao_positiva(linha: dict) -> bool:
    """Descarta citações que apenas declaram ausência de estudo/efeito/interação."""
    evidencia = mapear_texto(linha.get("evidencia") or "", unir_hifenizacao=True).texto
    alvo = normalizar_alvo(linha.get("alvo") or "")
    oracoes = oracoes_do_alvo(evidencia, alvo)
    return any(AUSENCIA.search(oracao) for oracao in oracoes) and not any(
        RECOMENDACAO_FORTE.search(oracao) for oracao in oracoes
    )


MENSAGEM_POSITIVA = "Interação identificada nas fontes analisadas."
MENSAGEM_NEGATIVA = "Não foi identificada interação nas fontes analisadas."
MENSAGEM_INCOMPLETA = (
    "Análise incompleta: não é possível concluir sobre interações com as fontes disponíveis."
)
MENSAGENS_ESTADO = {
    EstadoAnalise.CONCLUIDO_COM_RELACOES: "Bula analisada; relações identificadas.",
    EstadoAnalise.CONCLUIDO_SEM_RELACOES: (
        "Bula analisada; não foi identificada interação nas fontes analisadas."
    ),
    EstadoAnalise.REVISAO_NECESSARIA: (
        "Bula analisada com pendências de revisão; as relações dessa análise não são exibidas "
        "como conclusão até a revisão."
    ),
    EstadoAnalise.EM_PROCESSAMENTO: "A bula está sendo analisada agora.",
    EstadoAnalise.NAO_PROCESSADO: "A bula ainda não foi analisada.",
    EstadoAnalise.DESATUALIZADO: (
        "Há análise apenas de uma versão anterior do texto ou da bula; ela não é exibida."
    ),
    EstadoAnalise.RETIRADO: (
        "A análise da versão atual foi retirada após erro reconhecido; nenhum resultado é exibido."
    ),
    EstadoAnalise.FALHA: "A última análise da versão atual falhou; nenhum resultado concluído disponível.",
    EstadoAnalise.FONTE_INCOERENTE: (
        "Os identificadores da extração não correspondem à bula atual; a análise não é exibida."
    ),
    EstadoAnalise.SEM_TOPICO_INTERACOES: (
        "A extração não localizou o tópico de interações nesta bula; não há conclusão."
    ),
    EstadoAnalise.EXTRACAO_INDISPONIVEL: "O tópico de interações desta bula não está disponível.",
    EstadoAnalise.SEM_BULA: "Não há bula coletada para este medicamento.",
}
# Somente estes estados permitem a conclusão negativa "nas fontes analisadas".
CONCLUSIVOS = {EstadoAnalise.CONCLUIDO_COM_RELACOES, EstadoAnalise.CONCLUIDO_SEM_RELACOES}


MENSAGEM_MENCAO = (
    "Análise sem conclusão: uma bula cita pelo nome uma substância ou medicamento contido nos "
    "nomes do outro, sem vínculo gravado (por exemplo, cadastro atualizado depois da análise). "
    "Confira as menções não vinculadas."
)


def _palavras(texto: str) -> frozenset[str]:
    return frozenset(p for p in re.findall(r"[a-z0-9]+", normalizar_nome(texto)) if len(p) >= 3)


def palavras_do_medicamento(situacao: dict) -> list[frozenset[str]]:
    """Conjuntos de palavras do nome e de cada princípio ativo cadastrado."""
    conjuntos = [_palavras(situacao["nome_normalizado"]), _palavras(situacao.get("nome_exibicao") or "")]
    conjuntos += [_palavras(p) for p in (situacao.get("principios_ativos") or "").split(";")]
    return [conjunto for conjunto in conjuntos if conjunto]


def nome_contido(alvo: str, conjuntos: list[frozenset[str]]) -> bool:
    """Só bloqueia a conclusão negativa e sinaliza revisão; nunca cria vínculo."""
    palavras = _palavras(alvo)
    return bool(palavras) and any(palavras <= conjunto for conjunto in conjuntos)


def validar_identificador(valor: str, campo: str) -> str:
    """Aceita apenas nome_normalizado já normalizado (o que o autocomplete devolve)."""
    if not valor or len(valor) > 200 or normalizar_nome(valor) != valor:
        raise ParametroInvalidoError(
            f"{campo} deve ser o nome_normalizado devolvido pelo autocomplete."
        )
    return valor


def estado_da_situacao(situacao: dict, em_processamento: bool) -> EstadoAnalise:
    if situacao.get("processamento_id") is not None:
        return EstadoAnalise(situacao["status_processamento"])
    if em_processamento:
        return EstadoAnalise.EM_PROCESSAMENTO
    if situacao.get("status_bula") != "CONCLUIDO":
        return EstadoAnalise.SEM_BULA
    if not situacao.get("tem_extracao"):
        return EstadoAnalise.EXTRACAO_INDISPONIVEL
    if situacao.get("status_extracao") == "SEM_SECAO_INTERACOES":
        return EstadoAnalise.SEM_TOPICO_INTERACOES
    if situacao.get("status_extracao") != "CONCLUIDO" or not situacao.get("tem_texto"):
        return EstadoAnalise.EXTRACAO_INDISPONIVEL
    if not situacao.get("extracao_coerente"):
        return EstadoAnalise.FONTE_INCOERENTE
    if situacao.get("ultimo_status_fonte_atual") == "FALHA":
        return EstadoAnalise.FALHA
    if situacao.get("ultimo_status_fonte_atual") == "REVISAO_NECESSARIA":
        return EstadoAnalise.REVISAO_NECESSARIA
    if situacao.get("tem_retirada_fonte_atual"):
        return EstadoAnalise.RETIRADO
    if situacao.get("tem_historico"):
        return EstadoAnalise.DESATUALIZADO
    return EstadoAnalise.NAO_PROCESSADO


def _fonte(linha: dict, medicamento: Medicamento) -> FonteBula:
    """Identificação da bula guardada no processamento que gerou a relação."""
    return FonteBula(
        medicamento=medicamento,
        numero_registro=linha["numero_registro"],
        expediente=linha["expediente"],
        sha256_pdf=linha["sha256_pdf"],
        data_publicacao_bula=linha["data_publicacao_anvisa"],
    )


def _fonte_processada(situacao: dict, medicamento: Medicamento) -> FonteBula:
    return FonteBula(
        medicamento=medicamento,
        numero_registro=situacao["registro_processado"],
        expediente=situacao["expediente_processado"],
        sha256_pdf=situacao["sha256_processado"],
        data_publicacao_bula=situacao["publicacao_processada"],
    )


def _processamento(linha: dict) -> Processamento:
    return Processamento(
        id=linha["processamento_id"],
        estado=EstadoAnalise(linha["status_processamento"]),
        data_processamento=linha["concluido_em"],
        versao_processamento=linha["versao_processamento"],
        modelo=linha["modelo"],
    )


class ServicoConsulta:
    def __init__(self, repositorio, configuracao: ConfiguracaoApi) -> None:
        self.repositorio = repositorio
        self.configuracao = configuracao
        self._cache_cobertura: tuple[float, Cobertura] | None = None
        self._bloqueio = threading.Lock()

    # ---------------------------------------------------------- utilidades

    def sugerir(self, termo: str, limite: int | None) -> RespostaSugestoes:
        normalizado = normalizar_nome(termo)
        if not normalizado:
            raise ParametroInvalidoError("Informe ao menos um caractere além de espaços.")
        limite = limite or self.configuracao.limite_sugestoes_padrao
        linhas = self.repositorio.sugerir(normalizado, limite)
        return RespostaSugestoes(termo=normalizado, sugestoes=[Sugestao(**linha) for linha in linhas])

    def cobertura(self) -> Cobertura:
        with self._bloqueio:
            agora = time.monotonic()
            if self._cache_cobertura and agora - self._cache_cobertura[0] < self.configuracao.cache_cobertura_segundos:
                return self._cache_cobertura[1]
            dados = self.repositorio.cobertura()
            elegiveis = int(dados["elegiveis"])
            concluidas = int(dados["concluidas"])
            revisao = int(dados["em_revisao"])
            completo = elegiveis > 0 and concluidas + revisao >= elegiveis
            exaustiva = completo and revisao == 0
            if exaustiva:
                observacao = "Todas as bulas elegíveis foram analisadas sem pendências."
            else:
                observacao = (
                    f"{concluidas + revisao} de {elegiveis} bulas elegíveis analisadas"
                    f" ({revisao} com revisão pendente); a lista não é exaustiva."
                )
            cobertura = Cobertura(
                bulas_elegiveis=elegiveis,
                bulas_analisadas=concluidas + revisao,
                bulas_em_revisao=revisao,
                corpus_completo=completo,
                lista_exaustiva=exaustiva,
                observacao=observacao,
            )
            self._cache_cobertura = (agora, cobertura)
            return cobertura

    def _analises(self, nomes: list[str]) -> dict[str, tuple[Medicamento, AnaliseBula, dict]]:
        situacoes = self.repositorio.situacoes(nomes)
        ausentes = [nome for nome in nomes if nome not in situacoes]
        if ausentes:
            raise MedicamentoNaoEncontradoError(f"Medicamento não encontrado: {ausentes[0]}.")
        ocupados = self.repositorio.em_processamento(nomes)
        saida = {}
        for nome in nomes:
            situacao = situacoes[nome]
            medicamento = Medicamento(
                nome_normalizado=nome,
                nome_exibicao=situacao["nome_exibicao"],
                principios_ativos=[
                    p.strip() for p in (situacao.get("principios_ativos") or "").split(";") if p.strip()
                ],
            )
            estado = estado_da_situacao(situacao, nome in ocupados)
            processamento = _processamento(situacao) if situacao.get("processamento_id") else None
            fonte = _fonte_processada(situacao, medicamento) if processamento else None
            saida[nome] = (
                medicamento,
                AnaliseBula(
                    estado=estado,
                    mensagem=MENSAGENS_ESTADO[estado],
                    em_processamento=nome in ocupados,
                    processamento=processamento,
                    fonte=fonte,
                ),
                situacao,
            )
        return saida

    # ---------------------------------------------------- consulta simples

    def interacoes(self, nome: str, pagina: int, tamanho_pagina: int) -> RespostaInteracoes:
        validar_identificador(nome, "medicamento")
        medicamento, analise, _situacao = self._analises([nome])[nome]
        deslocamento = (pagina - 1) * tamanho_pagina
        linhas, _total_bruto = self.repositorio.relacoes_do_medicamento(nome, tamanho_pagina, deslocamento)
        linhas_contagem = self.repositorio.relacoes_para_contagem(nome)
        validas_contagem = [linha for linha in linhas_contagem if not relacao_nao_positiva(linha)]
        total_nesta = len({
            normalizar_alvo(linha["alvo"])
            for linha in validas_contagem
            if linha["grupo"] == 0
        })
        total_outras = len({
            linha["medicamento_origem"]
            for linha in validas_contagem
            if linha["grupo"] == 1
        })
        total_interacoes = total_nesta + total_outras
        total_registros = len(validas_contagem)
        relacionados = self.repositorio.relacionados(
            [linha["id"] for linha in linhas], self.configuracao.limite_relacionados
        )
        relacoes = []
        for linha in linhas:
            if relacao_nao_positiva(linha):
                continue
            origem = Medicamento(nome_normalizado=linha["medicamento_origem"], nome_exibicao=linha["origem_exibicao"])
            lista, quantidade = relacionados.get(linha["id"], ([], 0))
            relacoes.append(
                Relacao(
                    id=linha["id"],
                    sentido=Sentido.DESCRITA_NA_BULA_PESQUISADA if linha["grupo"] == 0 else Sentido.DESCRITA_EM_OUTRA_BULA,
                    alvo=linha["alvo"],
                    tipo_alvo=linha["tipo_alvo"],
                    resumo=linha["resumo"],
                    evidencia=linha["evidencia"],
                    fonte=_fonte(linha, origem),
                    processamento=_processamento(linha),
                    medicamentos_relacionados=[
                        MedicamentoRelacionado(
                            nome_normalizado=r["medicamento_relacionado"],
                            nome_exibicao=r["nome_exibicao"],
                            criterio_vinculo=r["criterio_vinculo"],
                        )
                        for r in lista
                    ],
                    total_medicamentos_relacionados=quantidade,
                )
            )
        cobertura = self.cobertura()
        if total_interacoes > 0:
            identificada, mensagem = True, MENSAGEM_POSITIVA
        elif analise.estado in CONCLUSIVOS:
            identificada, mensagem = False, MENSAGEM_NEGATIVA
        else:
            identificada, mensagem = None, MENSAGEM_INCOMPLETA
        if not cobertura.lista_exaustiva:
            mensagem += " " + cobertura.observacao[0].upper() + cobertura.observacao[1:]
        return RespostaInteracoes(
            medicamento=medicamento,
            analise_bula=analise,
            interacao_identificada=identificada,
            mensagem=mensagem,
            cobertura=cobertura,
            relacoes=relacoes,
            totais_por_sentido=TotaisPorSentido(
                nesta_bula=total_nesta,
                em_outras_bulas=total_outras,
            ),
            paginacao=Paginacao(
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
                total=total_registros,
                total_paginas=math.ceil(total_registros / tamanho_pagina) if total_registros else 0,
            ),
        )

    # ------------------------------------------------------- consulta do par

    def par(self, nome_a: str, nome_b: str) -> RespostaPar:
        validar_identificador(nome_a, "medicamento_a")
        validar_identificador(nome_b, "medicamento_b")
        if nome_a == nome_b:
            raise ParametroInvalidoError("Informe dois medicamentos diferentes.")
        analises = self._analises([nome_a, nome_b])
        medicamento_a, analise_a, situacao_a = analises[nome_a]
        medicamento_b, analise_b, situacao_b = analises[nome_b]
        medicamentos = {nome_a: medicamento_a, nome_b: medicamento_b}

        especificas: OrderedDict[tuple, EvidenciaPar] = OrderedDict()
        alertas: OrderedDict[tuple, EvidenciaPar] = OrderedDict()
        mencoes: OrderedDict[tuple, EvidenciaPar] = OrderedDict()
        palavras_outro = {
            nome_a: palavras_do_medicamento(situacao_b),
            nome_b: palavras_do_medicamento(situacao_a),
        }
        for linha in self.repositorio.relacoes_do_par(nome_a, nome_b):
            if relacao_nao_positiva(linha):
                continue
            if linha.get("criterio_vinculo") is not None:
                destino = especificas
            elif linha["tipo_alvo"] == "CLASSE_FARMACOLOGICA":
                destino = alertas
            elif nome_contido(linha["alvo"], palavras_outro[linha["medicamento_origem"]]):
                destino = mencoes
            else:
                continue
            # Mesmo sentido, mesma origem e mesma evidência: um grupo, vários itens.
            chave = (linha["sentido_par"], linha["medicamento_origem"],
                     linha["processamento_id"], chave_citacao(linha["evidencia"]))
            if chave not in destino:
                origem = medicamentos[linha["medicamento_origem"]]
                destino[chave] = EvidenciaPar(
                    sentido=SentidoPar(linha["sentido_par"]),
                    evidencia=linha["evidencia"],
                    fonte=_fonte(linha, origem),
                    processamento=_processamento(linha),
                    itens=[],
                )
            item = ItemPar(
                    interacao_id=linha["id"],
                    alvo=linha["alvo"],
                    tipo_alvo=linha["tipo_alvo"],
                    resumo=linha["resumo"],
                    criterio_vinculo=linha.get("criterio_vinculo"),
                    fundamento_vinculo=(
                        linha.get("fundamento_vinculo") if linha.get("criterio_vinculo") is not None else None
                    ),
                )
            # IDs distintos podem conter exatamente a mesma citação, alvo,
            # efeito e vínculo. Preserve efeitos/condições diferentes.
            if not any(
                normalizar_nome(existente.alvo) == normalizar_nome(item.alvo)
                and existente.tipo_alvo == item.tipo_alvo
                and chave_citacao(existente.resumo) == chave_citacao(item.resumo)
                and existente.criterio_vinculo == item.criterio_vinculo
                for existente in destino[chave].itens
            ):
                destino[chave].itens.append(item)

        completa = analise_a.estado in CONCLUSIVOS and analise_b.estado in CONCLUSIVOS
        if especificas:
            identificada, mensagem = True, MENSAGEM_POSITIVA
        elif mencoes:
            identificada, mensagem = None, MENSAGEM_MENCAO
        elif completa:
            identificada, mensagem = False, MENSAGEM_NEGATIVA
        else:
            identificada, mensagem = None, MENSAGEM_INCOMPLETA
        if alertas and not especificas:
            mensagem += (
                " Há alertas sobre classes de medicamentos que não confirmam interação "
                "específica com o outro medicamento."
            )
        return RespostaPar(
            medicamento_a=medicamento_a,
            medicamento_b=medicamento_b,
            analise_a=analise_a,
            analise_b=analise_b,
            interacao_identificada=identificada,
            mensagem=mensagem,
            cobertura_completa=completa,
            evidencias=list(especificas.values()),
            alertas_nao_confirmados=list(alertas.values()),
            mencoes_nao_vinculadas=list(mencoes.values()),
        )
