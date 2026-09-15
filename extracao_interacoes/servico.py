from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .erros import (
    OCRIndisponivelError,
    ErroOCRError,
    PdfComAnexosError,
    ErroInferenciaError,
    LimiteMemoriaError,
    PdfInvalidoError,
    PdfSemTextoError,
    RespostaInvalidaError,
    SecaoSomenteTituloError,
)
from .leitor_pdf import ler_pdf
from .esquemas_llm import esquema_para_geracao
from .memoria import MedidorMemoria
from .modo_rapido import preparar_confirmacoes
from .modelos import (
    CandidatoSecao,
    ConfiguracaoLLM,
    DocumentoPdf,
    JanelaDocumento,
    MetodoExtracao,
    RespostaClassificacao,
    RespostaConfirmacao,
    RespostaDesempate,
    RespostaLimite,
    ResultadoExtracao,
    StatusExtracao,
    TipoOcorrencia,
)
from .prompts import (
    mensagens_classificacao,
    mensagens_desempate,
    mensagens_limite,
)
from .provedor_llm import Mensagem, ProvedorLLM
from .segmentacao import criar_janelas
from .validacao import (
    analisar_json,
    copiar_e_validar_trecho,
    eh_limite_principal,
    ids_das_linhas,
    localizar_fallback_estrutural,
    localizar_titulos_interacoes,
    normalizar_para_comparacao,
    numero_linha,
    validar_candidato,
)


LOGGER = logging.getLogger(__name__)
TEsquema = TypeVar("TEsquema", bound=BaseModel)


class ServicoExtracaoInteracoes:
    """Caso de uso independente de CLI e banco, com ProvedorLLM injetado."""

    def __init__(
        self,
        provedor: ProvedorLLM,
        configuracao: ConfiguracaoLLM,
        leitor: Callable[[Path], DocumentoPdf] = ler_pdf,
        fabrica_medidor: Callable[[int], MedidorMemoria] = MedidorMemoria,
    ) -> None:
        self.provedor = provedor
        self.configuracao = configuracao
        self.leitor = leitor
        self.fabrica_medidor = fabrica_medidor
        self._travas: dict[str, threading.Lock] = {}
        self._trava_dicionario = threading.Lock()

    def _trava_para(self, chave: str) -> threading.Lock:
        with self._trava_dicionario:
            return self._travas.setdefault(chave, threading.Lock())

    def extrair(
        self,
        nome_normalizado: str,
        numero_registro: str,
        expediente: str | None,
        caminho_pdf: Path,
    ) -> ResultadoExtracao:
        # Registro e expediente seguem no contrato para o fluxo web futuro,
        # mas nunca são inferidos nem alterados pela LLM.
        _ = (numero_registro, expediente)
        chave = nome_normalizado or str(caminho_pdf.resolve())
        with self._trava_para(chave):
            return self._extrair_sem_concorrencia(caminho_pdf)

    def _extrair_sem_concorrencia(self, caminho_pdf: Path) -> ResultadoExtracao:
        resultado = ResultadoExtracao(status=StatusExtracao.RESPOSTA_INVALIDA)
        medidor = self.fabrica_medidor(self.configuracao.limite_memoria_mb)
        resultado.memoria_antes_mb = medidor.memoria_antes_mb
        inicio_leitura = time.perf_counter()
        try:
            documento = self.leitor(caminho_pdf)
            resultado.quantidade_paginas = documento.quantidade_paginas
            resultado.paginas_ocr = documento.paginas_ocr
            resultado.tempo_ocr_segundos = documento.tempo_ocr_segundos
            resultado.acertos_cache_ocr = documento.acertos_cache_ocr
            if documento.paginas_ocr:
                resultado.avisos.append(
                    "Texto reconhecido por OCR: copia literal do reconhecimento, "
                    "sujeita a erros de caracteres; conferir contra a imagem."
                )
        except (OCRIndisponivelError, ErroOCRError, PdfComAnexosError) as erro:
            LOGGER.exception("Leitura nao concluida: %r", erro)
            if isinstance(erro, PdfComAnexosError):
                resultado.status = StatusExtracao.REVISAO_MANUAL
                resultado.metodo = MetodoExtracao.REVISAO_MANUAL
            else:
                resultado.status = (
                    StatusExtracao.OCR_INDISPONIVEL if isinstance(erro, OCRIndisponivelError)
                    else StatusExtracao.ERRO_OCR
                )
            resultado.detalhe_erro = str(erro)
            resultado.tempo_leitura_segundos = time.perf_counter() - inicio_leitura
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            return resultado
        except PdfSemTextoError as erro:
            LOGGER.exception("PDF sem texto: %r", erro)
            resultado.status = StatusExtracao.PDF_SEM_TEXTO
            resultado.detalhe_erro = repr(erro)
            resultado.tempo_leitura_segundos = time.perf_counter() - inicio_leitura
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            return resultado
        except PdfInvalidoError as erro:
            LOGGER.exception("PDF inválido: %r", erro)
            resultado.status = StatusExtracao.PDF_INVALIDO
            resultado.detalhe_erro = repr(erro)
            resultado.tempo_leitura_segundos = time.perf_counter() - inicio_leitura
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            return resultado
        except Exception as erro:
            LOGGER.exception("Falha inesperada na leitura do PDF: %r", erro)
            resultado.status = StatusExtracao.PDF_INVALIDO
            resultado.detalhe_erro = repr(erro)
            resultado.tempo_leitura_segundos = time.perf_counter() - inicio_leitura
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            return resultado
        resultado.tempo_leitura_segundos = time.perf_counter() - inicio_leitura

        try:
            if self.configuracao.modo_extracao == "rapido":
                if self._tentar_modo_rapido(documento, resultado, medidor):
                    return resultado
                if not localizar_titulos_interacoes(documento):
                    resultado.status = StatusExtracao.SEM_SECAO_INTERACOES
                    resultado.quantidade_janelas = 0
                    resultado.avisos.append(
                        "Nenhum titulo explicito de interacoes foi encontrado."
                    )
                    return resultado
                # Nao transformar uma duvida local em centenas de inferencias.
                resultado.status = StatusExtracao.REVISAO_MANUAL
                resultado.metodo = MetodoExtracao.REVISAO_MANUAL
                resultado.detalhe_erro = (
                    "Titulo localizado, mas corpo/limites nao confirmados. "
                    "O modo rapido nao inicia varredura completa automaticamente."
                )
                return resultado
            janelas = criar_janelas(
                documento,
                self.provedor.contar_tokens,
                self.configuracao.tokens_janela,
                self.configuracao.sobreposicao_tokens,
            )
            resultado.quantidade_janelas = len(janelas)
            candidatos, erros_classificacao = self._classificar_todas(
                documento, janelas, resultado, medidor
            )
            if not candidatos:
                status = (
                    StatusExtracao.RESPOSTA_INVALIDA
                    if erros_classificacao
                    else StatusExtracao.SEM_SECAO_INTERACOES
                )
                return self._usar_fallback(
                    documento,
                    resultado,
                    status,
                    "; ".join(erros_classificacao),
                )

            candidato = self._escolher_candidato(
                candidatos, documento, janelas, resultado, medidor
            )
            linha_fim, proximo_titulo, usou_retentativa = self._localizar_fim(
                candidato, documento, janelas, resultado, medidor
            )
            trecho = copiar_e_validar_trecho(
                documento,
                candidato.resposta.linha_titulo or "",
                linha_fim,
                candidato.resposta.titulo_encontrado or "",
                proximo_titulo,
            )
            resultado.status = StatusExtracao.CONCLUIDO
            resultado.metodo = (
                MetodoExtracao.LLM_SEGUNDA_TENTATIVA
                if candidato.segunda_tentativa or usou_retentativa
                else MetodoExtracao.LLM
            )
            resultado.titulo_encontrado = candidato.resposta.titulo_encontrado or ""
            resultado.trecho_interacoes = trecho
            resultado.linha_inicio = candidato.resposta.linha_titulo or ""
            resultado.linha_fim_exclusiva = linha_fim
            return resultado
        except LimiteMemoriaError as erro:
            LOGGER.exception("Limite de memória atingido: %r", erro)
            resultado.status = StatusExtracao.LIMITE_MEMORIA
            resultado.detalhe_erro = repr(erro)
            return resultado
        except SecaoSomenteTituloError as erro:
            LOGGER.exception("Seção sem corpo válido: %r", erro)
            return self._usar_fallback(
                documento,
                resultado,
                StatusExtracao.SECAO_SOMENTE_TITULO,
                repr(erro),
            )
        except RespostaInvalidaError as erro:
            LOGGER.exception("Resposta da LLM inválida: %r", erro)
            return self._usar_fallback(
                documento,
                resultado,
                StatusExtracao.RESPOSTA_INVALIDA,
                repr(erro),
            )
        except ErroInferenciaError as erro:
            LOGGER.exception("Erro de inferência: %r", erro)
            resultado.status = StatusExtracao.ERRO_INFERENCIA
            resultado.detalhe_erro = repr(erro)
            return resultado
        except Exception as erro:
            LOGGER.exception("Erro inesperado na extração: %r", erro)
            resultado.status = StatusExtracao.ERRO_INFERENCIA
            resultado.detalhe_erro = repr(erro)
            return resultado
        finally:
            if resultado.paginas_ocr and resultado.status == StatusExtracao.SEM_SECAO_INTERACOES:
                resultado.status = StatusExtracao.REVISAO_MANUAL
                resultado.metodo = MetodoExtracao.REVISAO_MANUAL
                resultado.detalhe_erro = (
                    "OCR realizado, mas titulo de interacoes nao confirmado. "
                    "Conferir imagem antes de declarar ausencia."
                )
            resultado.memoria_depois_mb = medidor.ler_atual_mb()
            resultado.pico_memoria_mb = medidor.pico_mb

    def _tentar_modo_rapido(self, documento, resultado, medidor) -> bool:
        confirmacoes = preparar_confirmacoes(documento)
        if confirmacoes is None:
            return False
        resultado.requer_confirmacao_semantica = True
        trechos: list[str] = []
        titulos: list[str] = []
        inicios: list[str] = []
        fins: list[str] = []

        def marcar_revisao(detalhe: str) -> bool:
            resultado.status = StatusExtracao.REVISAO_MANUAL
            resultado.metodo = MetodoExtracao.REVISAO_MANUAL
            resultado.detalhe_erro = detalhe
            resultado.avisos.append(
                "Texto parcial descartado; pendencia sera registrada para revisao."
            )
            return True

        for indice, (inicio, fim, titulo, mensagens) in enumerate(
            confirmacoes, start=1
        ):
            tokens_prompt = sum(
                self.provedor.contar_tokens(m["content"]) for m in mensagens
            )
            if (
                tokens_prompt + min(self.configuracao.max_tokens_saida, 32) + 128
                > self.configuracao.tamanho_contexto
            ):
                detalhe = (
                    f"Contexto da secao {indice}/{len(confirmacoes)} excede o limite."
                )
                return marcar_revisao(detalhe)
            try:
                def criar_mensagens(erro, base=mensagens):
                    if not erro:
                        return base
                    return base + [{
                        "role": "user",
                        "content": f"Resposta invalida: {erro}. Corrija o JSON.",
                    }]

                confirmado, _ = self._chamar_json(
                    criar_mensagens, RespostaConfirmacao, resultado, medidor
                )
            except RespostaInvalidaError:
                return marcar_revisao(
                    f"Confirmacao invalida na secao {indice}/{len(confirmacoes)}."
                )
            if not confirmado.confirmado:
                return marcar_revisao(
                    f"Limites nao confirmados na secao {indice}/{len(confirmacoes)}."
                )
            trechos.append(
                copiar_e_validar_trecho(documento, inicio, fim, titulo)
            )
            titulos.append(titulo)
            inicios.append(inicio)
            fins.append(fim)

        # Um unico registro por nome. Cada parte foi validada literalmente antes
        # da composicao; a linha em branco apenas separa apresentacoes distintas.
        resultado.trecho_interacoes = "\n\n".join(trechos)
        resultado.requer_confirmacao_semantica = False
        resultado.status = StatusExtracao.CONCLUIDO
        resultado.metodo = MetodoExtracao.HIBRIDO_LLM
        resultado.titulo_encontrado = " | ".join(titulos)
        resultado.linha_inicio = ";".join(inicios)
        resultado.linha_fim_exclusiva = ";".join(fins)
        resultado.quantidade_janelas = len(confirmacoes)
        return True

    def _chamar_json(
        self,
        criar_mensagens: Callable[[str | None], list[Mensagem]],
        esquema: type[TEsquema],
        resultado: ResultadoExtracao,
        medidor: MedidorMemoria,
        validar: Callable[[TEsquema], None] | None = None,
        esquema_geracao: dict | None = None,
    ) -> tuple[TEsquema, bool]:
        ultimo_erro: RespostaInvalidaError | None = None
        if esquema_geracao is None:
            esquema_geracao = esquema_para_geracao(esquema)
        for tentativa in range(2):
            mensagens = criar_mensagens(str(ultimo_erro) if ultimo_erro else None)
            resposta, tempo = medidor.executar(
                lambda: self.provedor.analisar(mensagens, esquema=esquema_geracao)
            )
            resultado.tempo_inferencia_segundos += tempo
            resultado.quantidade_chamadas_llm += 1
            resultado.tokens_entrada_total += resposta.tokens_entrada
            resultado.tokens_saida_total += resposta.tokens_saida
            try:
                analisada = analisar_json(
                    resposta.conteudo,
                    esquema,
                    resposta_truncada=resposta.truncada,
                )
                if validar is not None:
                    validar(analisada)
                return analisada, tentativa == 1
            except RespostaInvalidaError as erro:
                ultimo_erro = erro
                LOGGER.warning(
                    "Resposta JSON rejeitada na tentativa %s: %r",
                    tentativa + 1,
                    erro,
                )
        assert ultimo_erro is not None
        raise ultimo_erro

    def _contexto_adjacente(
        self, janelas: list[JanelaDocumento], indice: int
    ) -> str:
        partes: list[str] = []
        orcamento = max(
            50, min(250, self.configuracao.sobreposicao_tokens or 50)
        )

        def selecionar(linhas, reverso: bool = False) -> list:
            candidatas = list(reversed(linhas)) if reverso else list(linhas)
            escolhidas = []
            total = 0
            for linha in candidatas:
                tokens = max(1, self.provedor.contar_tokens(linha.texto_prompt))
                if total + tokens > orcamento:
                    break
                escolhidas.append(linha)
                total += tokens
            if reverso:
                escolhidas.reverse()
            return escolhidas

        if indice > 0:
            linhas = selecionar(janelas[indice - 1].linhas, reverso=True)
            if linhas:
                partes.append(
                    "<FINAL_JANELA_ANTERIOR>\n"
                    + "\n".join(linha.texto_prompt for linha in linhas)
                    + "\n</FINAL_JANELA_ANTERIOR>"
                )
        if indice + 1 < len(janelas):
            linhas = selecionar(janelas[indice + 1].linhas)
            if linhas:
                partes.append(
                    "<INICIO_JANELA_SEGUINTE>\n"
                    + "\n".join(linha.texto_prompt for linha in linhas)
                    + "\n</INICIO_JANELA_SEGUINTE>"
                )
        return "\n".join(partes)

    def _classificar_todas(
        self,
        documento: DocumentoPdf,
        janelas: list[JanelaDocumento],
        resultado: ResultadoExtracao,
        medidor: MedidorMemoria,
    ) -> tuple[list[CandidatoSecao], list[str]]:
        candidatos: list[CandidatoSecao] = []
        erros: list[str] = []
        for janela in janelas:
            contexto = self._contexto_adjacente(janelas, janela.indice)
            try:
                resposta, repetiu = self._chamar_json(
                    lambda erro, j=janela, c=contexto: mensagens_classificacao(
                        j, erro, c if erro else ""
                    ),
                    RespostaClassificacao,
                    resultado,
                    medidor,
                    validar=lambda valor, j=janela: (
                        validar_candidato(
                            valor, documento, ids_das_linhas(j.linhas)
                        )
                        if valor.tipo_ocorrencia == TipoOcorrencia.SECAO_CORPO
                        else None
                    ),
                )
                if resposta.tipo_ocorrencia == TipoOcorrencia.SECAO_CORPO:
                    candidatos.append(
                        CandidatoSecao(resposta, janela.indice, repetiu)
                    )
            except RespostaInvalidaError as erro:
                LOGGER.exception(
                    "Classificação inválida após retry na janela %s: %r",
                    janela.indice,
                    erro,
                )
                erros.append(f"janela {janela.indice}: {erro}")
        return candidatos, erros

    def _escolher_candidato(
        self,
        candidatos: list[CandidatoSecao],
        documento: DocumentoPdf,
        janelas: list[JanelaDocumento],
        resultado: ResultadoExtracao,
        medidor: MedidorMemoria,
    ) -> CandidatoSecao:
        # A sobreposição pode apresentar o mesmo título em duas janelas.
        unicos: dict[str, CandidatoSecao] = {}
        for candidato in candidatos:
            chave = candidato.resposta.linha_titulo or ""
            anterior = unicos.get(chave)
            if anterior is None or candidato.resposta.confianca.peso > anterior.resposta.confianca.peso:
                unicos[chave] = candidato
        candidatos = list(unicos.values())
        if len(candidatos) == 1:
            return candidatos[0]

        contextos = []
        for candidato in candidatos:
            inicio = numero_linha(candidato.resposta.linha_titulo or "") - 1
            linhas = documento.linhas[max(0, inicio - 4) : inicio + 12]
            contextos.append("\n".join(linha.texto_prompt for linha in linhas))
        ids_candidatos = {
            candidato.resposta.linha_titulo for candidato in candidatos
        }

        def validar_desempate(valor: RespostaDesempate) -> None:
            if valor.linha_titulo_escolhida not in ids_candidatos:
                raise RespostaInvalidaError(
                    "O desempate não escolheu um candidato conhecido."
                )

        resposta, repetiu = self._chamar_json(
            lambda erro: mensagens_desempate(candidatos, contextos, erro),
            RespostaDesempate,
            resultado,
            medidor,
            validar=validar_desempate,
        )
        escolhidos = [
            candidato
            for candidato in candidatos
            if candidato.resposta.linha_titulo == resposta.linha_titulo_escolhida
        ]
        if len(escolhidos) != 1:
            raise RespostaInvalidaError(
                "O desempate não escolheu exatamente um candidato conhecido."
            )
        escolhido = escolhidos[0]
        if repetiu and not escolhido.segunda_tentativa:
            escolhido = CandidatoSecao(
                escolhido.resposta, escolhido.indice_janela, True
            )
        return escolhido

    def _localizar_fim(
        self,
        candidato: CandidatoSecao,
        documento: DocumentoPdf,
        janelas: list[JanelaDocumento],
        resultado: ResultadoExtracao,
        medidor: MedidorMemoria,
    ) -> tuple[str, str | None, bool]:
        inicio = numero_linha(candidato.resposta.linha_titulo or "")
        repetiu_alguma = False
        for janela in janelas[candidato.indice_janela :]:
            if numero_linha(janela.linha_fim_inclusiva) < inicio:
                continue
            # O inicio ja foi decidido: a etapa de limite so precisa do texto
            # a partir dele, sem secoes anteriores da mesma janela.
            linhas_limite = []
            tokens_limite = 0
            primeiro = max(inicio, janela.linhas[0].numero)
            for linha in documento.linhas[primeiro - 1:]:
                tokens = max(1, self.provedor.contar_tokens(linha.texto_prompt))
                if linhas_limite and tokens_limite + tokens > self.configuracao.tokens_janela:
                    break
                linhas_limite.append(linha)
                tokens_limite += tokens
            janela = JanelaDocumento(janela.indice, tuple(linhas_limite), tokens_limite)
            esquema_limite = esquema_para_geracao(RespostaLimite)
            linhas_finais = [l for l in janela.linhas if l.numero > inicio]
            ramos = []
            for ramo in esquema_limite["anyOf"]:
                campos = ramo["properties"]
                campos["linha_inicio"] = {"const": candidato.resposta.linha_titulo}
                campos["titulo_encontrado"] = {"const": candidato.resposta.titulo_encontrado}
                if campos["linha_fim_exclusiva"].get("type") == "string":
                    # Todas as linhas sao opcoes, sem pre-selecao por palavras.
                    # A LLM escolhe o texto; o ID correspondente fica vinculado.
                    for linha_final in linhas_finais:
                        opcao = deepcopy(ramo)
                        propriedades = opcao["properties"]
                        propriedades["proximo_titulo"] = {"const": linha_final.texto_original}
                        propriedades["linha_fim_exclusiva"] = {"const": linha_final.identificador}
                        opcao["properties"] = {
                            nome: propriedades[nome] for nome in (
                                "encontrou_fim", "linha_inicio", "titulo_encontrado",
                                "proximo_titulo", "linha_fim_exclusiva", "fim_documento",
                            )
                        }
                        ramos.append(opcao)
                    continue
                if campos["fim_documento"].get("const") is True and janela.indice != len(janelas) - 1:
                    continue
                ramos.append(ramo)
            esquema_limite["anyOf"] = ramos
            contexto = self._contexto_adjacente(janelas, janela.indice)
            def validar_limite(valor: RespostaLimite, j=janela) -> None:
                if valor.linha_inicio != candidato.resposta.linha_titulo:
                    raise RespostaInvalidaError(
                        "A resposta alterou a linha inicial da seção."
                    )
                if normalizar_para_comparacao(
                    valor.titulo_encontrado
                ) != normalizar_para_comparacao(
                    candidato.resposta.titulo_encontrado or ""
                ):
                    raise RespostaInvalidaError(
                        "A resposta alterou o título encontrado."
                    )
                if not valor.encontrou_fim:
                    return
                if valor.fim_documento:
                    if j.indice != len(janelas) - 1:
                        raise RespostaInvalidaError(
                            "fim_documento foi informado antes da última janela."
                        )
                    return
                linha_fim_validada = valor.linha_fim_exclusiva or ""
                numero_fim_validado = numero_linha(
                    linha_fim_validada, len(documento.linhas)
                )
                if numero_fim_validado <= inicio:
                    raise RespostaInvalidaError(
                        "A LLM informou um fim anterior ao início da seção."
                    )
                if linha_fim_validada not in ids_das_linhas(j.linhas):
                    raise RespostaInvalidaError(
                        "A linha final não pertence à janela analisada."
                    )
                if not eh_limite_principal(
                    documento.linhas[inicio - 1].texto_original,
                    documento.linhas[numero_fim_validado - 1].texto_original,
                ):
                    raise RespostaInvalidaError(
                        "Esse limite e subtitulo ou corpo. Continue ate o proximo topico principal."
                    )
                if valor.proximo_titulo:
                    linha_validada = documento.obter_linha(linha_fim_validada)
                    if len(valor.proximo_titulo.strip()) < 4 or linha_validada is None or normalizar_para_comparacao(
                        valor.proximo_titulo
                    ) != normalizar_para_comparacao(
                        linha_validada.texto_original
                    ):
                        raise RespostaInvalidaError(
                            "O próximo título não corresponde à linha final."
                        )

            resposta, repetiu = self._chamar_json(
                lambda erro, j=janela, c=contexto: mensagens_limite(
                    j,
                    candidato.resposta.linha_titulo or "",
                    candidato.resposta.titulo_encontrado or "",
                    erro,
                    c if erro else "",
                ),
                RespostaLimite,
                resultado,
                medidor,
                validar=validar_limite,
                esquema_geracao=esquema_limite,
            )
            repetiu_alguma = repetiu_alguma or repetiu
            if not resposta.encontrou_fim:
                continue
            if resposta.fim_documento:
                return f"L{len(documento.linhas) + 1:06d}", None, repetiu_alguma
            linha_fim = resposta.linha_fim_exclusiva or ""
            return linha_fim, resposta.proximo_titulo, repetiu_alguma

        raise RespostaInvalidaError(
            "A LLM nao confirmou o proximo titulo nem o fim do documento."
        )

    def _usar_fallback(
        self,
        documento: DocumentoPdf,
        resultado: ResultadoExtracao,
        status_sem_fallback: StatusExtracao,
        detalhe: str,
    ) -> ResultadoExtracao:
        candidatos = localizar_fallback_estrutural(documento)
        if candidatos and resultado.requer_confirmacao_semantica:
            # Uma regex nao pode aprovar de novo o que a triagem nao confirmou.
            resultado.status = StatusExtracao.REVISAO_MANUAL
            resultado.metodo = MetodoExtracao.REVISAO_MANUAL
            resultado.detalhe_erro = (
                f"{detalhe}; titulo/corpo nao confirmados semanticamente. "
                "Fallback automatico desabilitado para esta ocorrencia."
            ).strip("; ")
            return resultado
        if len(candidatos) == 1:
            inicio, fim, titulo = candidatos[0]
            resultado.status = StatusExtracao.CONCLUIDO
            resultado.metodo = MetodoExtracao.FALLBACK_ESTRUTURAL
            resultado.titulo_encontrado = titulo
            resultado.trecho_interacoes = copiar_e_validar_trecho(
                documento, inicio, fim, titulo
            )
            resultado.linha_inicio = inicio
            resultado.linha_fim_exclusiva = fim
            resultado.avisos.append(
                "Extração concluída por fallback; revisar se a frequência for significativa."
            )
            resultado.detalhe_erro = detalhe
        elif len(candidatos) > 1:
            resultado.status = StatusExtracao.REVISAO_MANUAL
            resultado.metodo = MetodoExtracao.REVISAO_MANUAL
            resultado.detalhe_erro = (
                f"{detalhe}; fallback encontrou {len(candidatos)} seções possíveis."
            ).strip("; ")
        else:
            resultado.status = status_sem_fallback
            resultado.detalhe_erro = detalhe
        return resultado
