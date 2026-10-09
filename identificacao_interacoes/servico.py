from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from extracao_interacoes.memoria import MedidorMemoria

from .erros import (
    ContextoExcedidoError,
    ErroIdentificacaoInteracoes,
    ErroInferenciaError,
    ErroSegmentacaoError,
    RespostaInvalidaError,
    RespostaTruncadaError,
)
from .esquemas_llm import (
    MAX_ALVOS_POR_FRASE,
    MAX_EXEMPLOS_DE_CLASSE,
    FraseInformada,
    RespostaIdentificacao,
    esquema_correcao,
    esquema_exemplos,
    esquema_janela,
    interpretar_exemplos,
    interpretar_resposta,
)
from .modelos import (
    ConfiguracaoIdentificacao,
    FonteInteracoes,
    MotivoRejeicao,
    RelacaoIdentificada,
    Rejeicao,
    ResultadoIdentificacao,
    StatusProcessamento,
    TipoAlvo,
)
from .normalizacao import mapear_texto, normalizar_alvo
from .prompts import (
    VERSAO_PROMPT,
    impressao_digital_prompt,
    mensagens_correcao_json,
    mensagens_correcao_relacoes,
    mensagens_exemplos_de_classe,
    mensagens_identificacao,
    mensagens_releitura,
    renderizar_frases,
)
from .segmentacao import JanelaTexto, UnidadeTexto, criar_janelas, criar_unidades, dividir_janela
from .validacao import (
    VERSAO_VALIDACAO,
    ContextoValidacao,
    chaves_da_origem,
    consolidar,
    validar_alvo,
)

if TYPE_CHECKING:
    from extracao_interacoes.provedor_llm import ProvedorLLM


LOGGER = logging.getLogger(__name__)

# Folga para os marcadores do modelo de chat que envolvem cada mensagem.
TOKENS_POR_MENSAGEM = 16
MAX_CARACTERES_UNIDADE = 1500
# Só decide se vale perguntar ao modelo; os nomes vêm do modelo e da validação.
MARCADOR_EXEMPLOS = re.compile(
    r"\b(?:como|tais como|por exemplo|p\.\s*ex|incluindo|exemplos?|entre eles|entre elas)\b|:\s*\S|\("
)


class _PrecisaDividir(ErroIdentificacaoInteracoes):
    """Sinal interno: a janela deve ser dividida e analisada em partes."""

    def __init__(self, mensagem: str, *, contexto: bool = False) -> None:
        super().__init__(mensagem)
        self.contexto = contexto


@dataclass(slots=True)
class _ResultadoJanela:
    relacoes: list[RelacaoIdentificada] = field(default_factory=list)
    rejeicoes: list[Rejeicao] = field(default_factory=list)
    frases_rejeicoes: list[str] = field(default_factory=list)
    # Rejeições da etapa de exemplos de classe: registradas, sem exigir revisão.
    informativas: list[Rejeicao] = field(default_factory=list)

    def rejeitar(self, id_frase: str, rejeicao: Rejeicao) -> None:
        self.rejeicoes.append(rejeicao)
        self.frases_rejeicoes.append(id_frase)

    def somar(self, outro: "_ResultadoJanela") -> None:
        self.relacoes.extend(outro.relacoes)
        self.rejeicoes.extend(outro.rejeicoes)
        self.frases_rejeicoes.extend(outro.frases_rejeicoes)
        self.informativas.extend(outro.informativas)


class ServicoIdentificacaoInteracoes:
    """Caso de uso independente da CLI e do banco, com o provedor já carregado.

    Nenhum método instancia pesos: o chamador carrega o modelo uma única vez
    por processo. A inferência é serial (um bloqueio por instância).
    """

    def __init__(
        self,
        provedor: "ProvedorLLM",
        configuracao: ConfiguracaoIdentificacao,
        fabrica_medidor: Callable[[int], Any] = MedidorMemoria,
    ) -> None:
        self.provedor = provedor
        self.configuracao = configuracao
        self.fabrica_medidor = fabrica_medidor
        self._bloqueio = threading.Lock()
        # Frases (início no texto) já perguntadas no medicamento em análise:
        # uma pergunta por frase, também entre janelas sobrepostas.
        self._exemplos_perguntados: set[int] = set()

    @property
    def versao_processamento(self) -> str:
        c = self.configuracao
        if c.estrategia == "duas_etapas":
            from .duas_etapas import versao_fluxo
            return versao_fluxo(c)
        return (
            f"{VERSAO_PROMPT}#{impressao_digital_prompt()}"
            f"|modelo={c.arquivo_modelo}|ctx={c.tamanho_contexto}"
            f"|janela={c.tokens_janela}/{c.sobreposicao_tokens}"
            f"|saida={c.max_tokens_saida}|temperatura={c.temperatura:g}"
            f"|correcoes={c.tentativas_correcao}|exemplos={3 if c.exemplos_de_classe else 0}"
            f"|releitura={2 if c.releitura_de_listas else 0}|{VERSAO_VALIDACAO}"
        )

    @property
    def modelo(self) -> str:
        return f"{self.configuracao.repositorio_modelo}/{self.configuracao.arquivo_modelo}"

    # ----------------------------------------------------------- caso de uso

    def identificar(self, fonte: FonteInteracoes) -> ResultadoIdentificacao:
        with self._bloqueio:
            self._exemplos_perguntados = set()
            return self._identificar(fonte)

    def _identificar(self, fonte: FonteInteracoes) -> ResultadoIdentificacao:
        if self.configuracao.estrategia == "duas_etapas":
            from .duas_etapas import identificar
            return identificar(self, fonte)
        resultado = ResultadoIdentificacao(
            status=StatusProcessamento.FALHA, nome_normalizado=fonte.nome_normalizado
        )
        texto = fonte.trecho_interacoes or ""
        if not texto.strip():
            resultado.detalhe_erro = "trecho_interacoes vazio."
            return resultado

        medidor = self.fabrica_medidor(self.configuracao.limite_memoria_mb)
        resultado.memoria_antes_mb = medidor.memoria_antes_mb
        try:
            unidades = criar_unidades(texto, self.provedor.contar_tokens, MAX_CARACTERES_UNIDADE)
            janelas = criar_janelas(
                texto, unidades, self.configuracao.tokens_janela, self.configuracao.sobreposicao_tokens
            )
        except (ErroSegmentacaoError, ValueError) as erro:
            resultado.detalhe_erro = f"Segmentação: {erro}"
            return self._finalizar_metricas(resultado, medidor)
        except Exception as erro:  # noqa: BLE001 - a tokenização usa o modelo
            LOGGER.exception("Falha ao segmentar %s: %r", fonte.nome_normalizado, erro)
            resultado.detalhe_erro = f"Segmentação: {type(erro).__name__}: {erro}"
            return self._finalizar_metricas(resultado, medidor)

        resultado.quantidade_janelas = len(janelas)
        contexto = ContextoValidacao(
            chaves_origem=chaves_da_origem(
                fonte.nome_normalizado, fonte.nome_exibicao, fonte.principios_ativos
            ),
            texto=texto,
            max_caracteres_resumo=self.configuracao.max_caracteres_resumo,
        )
        acumulado = _ResultadoJanela()
        for janela in janelas:
            try:
                parcial = self._analisar_janela(
                    fonte, texto, unidades, janela, len(janelas), contexto, medidor, resultado, 0
                )
            except Exception as erro:  # noqa: BLE001 - qualquer falha impede a conclusão
                if not isinstance(erro, ErroIdentificacaoInteracoes):
                    LOGGER.exception(
                        "Falha na janela %s de %s: %r", janela.indice, fonte.nome_normalizado, erro
                    )
                resultado.detalhe_erro = (
                    f"Janela {janela.indice + 1}/{len(janelas)}: {type(erro).__name__}: {erro}"
                )
                return self._finalizar_metricas(resultado, medidor)
            acumulado.somar(parcial)
            resultado.janelas_concluidas += 1
            LOGGER.info(
                "%s janela %s/%s concluída: %s relação(ões) aceitas, %s rejeição(ões), "
                "%s chamada(s) acumulada(s), %.1fs de inferência",
                fonte.nome_normalizado, janela.indice + 1, len(janelas), len(parcial.relacoes),
                len(parcial.rejeicoes), resultado.chamadas_llm, resultado.tempo_inferencia_segundos,
            )

        resultado.aceitas_antes_consolidacao = len(acumulado.relacoes)
        relacoes, duplicatas = consolidar(acumulado.relacoes)
        resultado.relacoes = relacoes
        resultado.duplicatas_consolidadas = duplicatas
        resultado.rejeicoes = acumulado.rejeicoes + acumulado.informativas
        resultado.rejeicoes_por_etapa['fluxo_atual'] = dict(Counter(r.motivo.value for r in resultado.rejeicoes))
        resultado.rejeicoes_pendentes = self._pendentes_sem_relacao(acumulado.rejeicoes, relacoes)
        if resultado.rejeicoes_pendentes:
            resultado.status = StatusProcessamento.REVISAO_NECESSARIA
            resultado.detalhe_erro = (
                f"{len(resultado.rejeicoes_pendentes)} pendência(s) não resolvida(s) na correção; "
                "a lista de relações pode estar incompleta."
            )
        elif relacoes:
            resultado.status = StatusProcessamento.CONCLUIDO_COM_RELACOES
        else:
            resultado.status = StatusProcessamento.CONCLUIDO_SEM_RELACOES
        return self._finalizar_metricas(resultado, medidor)

    @staticmethod
    def _pendentes_sem_relacao(
        rejeicoes: list[Rejeicao], relacoes: list[RelacaoIdentificada]
    ) -> list[Rejeicao]:
        """Pendências cujo alvo não terminou aceito em nenhuma janela."""
        aceitos = {(normalizar_alvo(r.alvo), r.tipo_alvo.value, mapear_texto(r.evidencia).texto) for r in relacoes}
        unicos: dict[tuple, Rejeicao] = {}
        for rejeicao in rejeicoes:
            if not rejeicao.exige_revisao:
                continue
            chave = (normalizar_alvo(rejeicao.alvo), rejeicao.tipo_alvo,
                     mapear_texto(rejeicao.evidencia_informada).texto)
            if rejeicao.motivo == MotivoRejeicao.LISTA_DE_ALVOS_NO_LIMITE:
                chave = (rejeicao.motivo.value, rejeicao.evidencia_informada)
            elif chave in aceitos:
                continue
            unicos.setdefault(chave, rejeicao)
        return list(unicos.values())

    @staticmethod
    def _finalizar_metricas(resultado: ResultadoIdentificacao, medidor) -> ResultadoIdentificacao:
        resultado.memoria_depois_mb = medidor.ler_atual_mb()
        resultado.pico_memoria_mb = medidor.pico_mb
        return resultado

    # -------------------------------------------------------------- janelas

    def _analisar_janela(
        self,
        fonte: FonteInteracoes,
        texto: str,
        unidades: list[UnidadeTexto],
        janela: JanelaTexto,
        total: int,
        contexto: ContextoValidacao,
        medidor,
        resultado: ResultadoIdentificacao,
        profundidade: int,
    ) -> _ResultadoJanela:
        try:
            return self._analisar_uma_vez(fonte, texto, janela, total, contexto, medidor, resultado)
        except _PrecisaDividir as motivo:
            if profundidade >= self.configuracao.max_divisoes_janela or len(janela.unidades) < 2:
                classe = ContextoExcedidoError if motivo.contexto else RespostaTruncadaError
                raise classe(
                    f"{motivo} A janela não pode ser dividida mais; nada foi descartado."
                ) from motivo
            resultado.divisoes_por_limite += 1
            resultado.avisos.append(f"Janela {janela.indice + 1} dividida: {motivo}")
            combinado = _ResultadoJanela()
            for parte in dividir_janela(texto, janela, unidades):
                combinado.somar(
                    self._analisar_janela(
                        fonte, texto, unidades, parte, total, contexto, medidor,
                        resultado, profundidade + 1,
                    )
                )
            return combinado

    def _analisar_uma_vez(
        self,
        fonte: FonteInteracoes,
        texto: str,
        janela: JanelaTexto,
        total: int,
        contexto: ContextoValidacao,
        medidor,
        resultado: ResultadoIdentificacao,
    ) -> _ResultadoJanela:
        self._janela_metrica = janela.indice
        numerado, ids = renderizar_frases(
            [(texto[u.inicio:u.fim], u.titulo) for u in janela.unidades]
        )
        if not ids:
            return _ResultadoJanela()  # só títulos: nada a relacionar
        frases = dict(zip(ids, (u for u in janela.unidades if not u.titulo), strict=True))
        mensagens = mensagens_identificacao(
            nome_exibicao=fonte.nome_exibicao,
            principios_ativos=fonte.principios_ativos,
            texto_numerado=numerado,
            indice_janela=janela.indice,
            total_janelas=total,
            contexto_titulo=janela.contexto_titulo,
        )
        esquema = esquema_janela(ids)
        resposta = self._chamar(mensagens, esquema, medidor, resultado)
        if resposta.truncada:
            raise _PrecisaDividir("A resposta atingiu o limite de tokens de saída.")
        conteudo = resposta.conteudo
        try:
            dados = interpretar_resposta(conteudo, ids)
        except RespostaInvalidaError as erro:
            nova = self._chamar(
                mensagens_correcao_json(mensagens, conteudo, str(erro)), esquema, medidor, resultado,
                tentativa=1, finalidade='correcao', motivo='json_invalido',
            )
            if nova.truncada:
                raise _PrecisaDividir("A nova tentativa atingiu o limite de saída.") from erro
            conteudo = nova.conteudo
            try:
                dados = interpretar_resposta(conteudo, ids)
            except RespostaInvalidaError as segundo:
                raise _PrecisaDividir(f"JSON inválido após nova tentativa: {segundo}") from segundo

        saida = self._validar(dados, frases, janela, contexto)
        if self.configuracao.releitura_de_listas:
            self._reler_listas(mensagens, dados, frases, saida, janela, contexto, medidor, resultado)
        pendentes = self._corrigiveis(saida)
        for _tentativa in range(self.configuracao.tentativas_correcao):
            if not pendentes:
                break
            correcao = self._corrigir(mensagens, pendentes, frases, janela, contexto, medidor, resultado, _tentativa+1)
            saida.somar(correcao)
            pendentes = self._corrigiveis(correcao)
        if self.configuracao.exemplos_de_classe:
            self._completar_exemplos(saida, contexto, janela, medidor, resultado)
        return saida

    def _completar_exemplos(self, saida, contexto, janela, medidor, resultado) -> None:
        """Pergunta ao modelo quais integrantes a frase cita para cada classe aceita.

        No piloto, o modelo registrava a classe e omitia os exemplos citados na
        mesma frase. Cada nome devolvido passa pela mesma validação contra a
        frase; nenhum integrante é inferido fora do texto.
        """
        perguntados = self._exemplos_perguntados
        for relacao in list(saida.relacoes):
            if relacao.tipo_alvo != TipoAlvo.CLASSE_FARMACOLOGICA:
                continue
            # Uma pergunta por frase: a resposta lista os nomes citados na frase
            # qualquer que seja a classe, e o modelo às vezes rotula fármacos de
            # uma lista como classes (no Marevan, 41 "classes" geraram 43 chamadas).
            normalizada = mapear_texto(relacao.evidencia).texto
            if relacao.inicio_evidencia in perguntados or not MARCADOR_EXEMPLOS.search(normalizada):
                continue
            perguntados.add(relacao.inicio_evidencia)
            classe = normalizar_alvo(relacao.alvo)
            try:
                resposta = self._chamar(
                    mensagens_exemplos_de_classe(relacao.evidencia, relacao.alvo),
                    esquema_exemplos(), medidor, resultado,
                    motivo='exemplos_de_classe',
                )
                if resposta.truncada:
                    raise RespostaInvalidaError("resposta truncada")
                exemplos = interpretar_exemplos(resposta.conteudo)
                if len(exemplos) >= MAX_EXEMPLOS_DE_CLASSE:
                    saida.rejeitar(
                        "",
                        Rejeicao(
                            motivo=MotivoRejeicao.LISTA_DE_ALVOS_NO_LIMITE,
                            alvo="",
                            tipo_alvo="",
                            resumo="",
                            evidencia_informada=relacao.evidencia,
                            detalhe=f"A lista de exemplos atingiu {MAX_EXEMPLOS_DE_CLASSE} nomes; pode estar cortada.",
                            indice_janela=janela.indice,
                        ),
                    )
            except (RespostaInvalidaError, _PrecisaDividir) as erro:
                resultado.avisos.append(
                    f"Exemplos de '{relacao.alvo}' (janela {janela.indice + 1}) não obtidos: {erro}"
                )
                continue
            for nome, tipo in exemplos:
                if normalizar_alvo(nome) == classe:
                    continue
                validado = validar_alvo(
                    contexto, alvo=nome, tipo_alvo=tipo, resumo=relacao.resumo,
                    inicio_evidencia=relacao.inicio_evidencia, fim_evidencia=relacao.fim_evidencia,
                    indice_janela=janela.indice,
                )
                if isinstance(validado, RelacaoIdentificada):
                    saida.relacoes.append(validado)
                else:
                    saida.informativas.append(validado)

    @staticmethod
    def _corrigiveis(saida: _ResultadoJanela) -> list[tuple[str, Rejeicao]]:
        return [
            (id_frase, rejeicao)
            for id_frase, rejeicao in zip(saida.frases_rejeicoes, saida.rejeicoes, strict=True)
            if rejeicao.corrigivel
        ]

    def _corrigir(
        self, mensagens, pendentes, frases, janela, contexto, medidor, resultado, tentativa=1
    ) -> _ResultadoJanela:
        """Uma rodada: falha da correção não derruba a janela já analisada.

        As pendências continuam registradas e deixam o resultado em revisão.
        """
        ids = list(dict.fromkeys(id_frase for id_frase, _ in pendentes))
        try:
            resposta = self._chamar(
                mensagens_correcao_relacoes(mensagens, pendentes),
                esquema_correcao(ids, len(ids)),
                medidor,
                resultado,
                tentativa=tentativa, finalidade='correcao', motivo='relacoes_rejeitadas',
            )
            if resposta.truncada:
                resultado.avisos.append(f"Correção da janela {janela.indice + 1} truncada.")
                return _ResultadoJanela()
            dados = interpretar_resposta(resposta.conteudo, ids, exigir_todos=False)
        except (RespostaInvalidaError, _PrecisaDividir) as erro:
            resultado.avisos.append(f"Correção da janela {janela.indice + 1} não aplicada: {erro}")
            return _ResultadoJanela()
        return self._validar(dados, frases, janela, contexto)

    def _reler_listas(self, mensagens, dados, frases, saida, janela, contexto, medidor, resultado) -> None:
        """Segunda leitura, uma frase por vez, das frases-lista que voltaram vazias.

        No piloto (Marevan), a frase que lista ~50 fármacos potencializadores
        voltou sem nenhum alvo aceito: o modelo escreveu nela o conteúdo da
        frase seguinte. O gatilho (frase longa com muitas vírgulas e nenhuma
        relação aceita) só decide reler; quem identifica os alvos continua
        sendo o modelo, e cada alvo passa pela validação.
        """
        com_alvos = {
            id_frase for id_frase, unidade in frases.items()
            if any(r.inicio_evidencia == unidade.inicio for r in saida.relacoes)
        }
        for frase in dados.frases:
            unidade = frases[frase.id_frase]
            texto = contexto.texto[unidade.inicio:unidade.fim]
            if frase.id_frase in com_alvos or len(texto) < 200 or texto.count(",") < 4:
                continue
            numerada, ids = renderizar_frases([(texto, False)])
            try:
                resposta = self._chamar(
                    mensagens_releitura(mensagens, numerada), esquema_janela(ids), medidor, resultado,
                    motivo='lista_sem_alvos',
                )
                if resposta.truncada:
                    raise RespostaInvalidaError("resposta truncada")
                relida = interpretar_resposta(resposta.conteudo, ids)
            except (RespostaInvalidaError, _PrecisaDividir) as erro:
                resultado.avisos.append(
                    f"Releitura da frase {frase.id_frase} (janela {janela.indice + 1}) não aplicada: {erro}"
                )
                continue
            resultado.releituras += 1
            remapeada = RespostaIdentificacao(
                tuple(
                    FraseInformada(frase.id_frase, item.alvos, item.quantidade_informada)
                    for item in relida.frases
                ),
                tuple(replace(item, id_frase=frase.id_frase) for item in relida.invalidos),
            )
            saida.somar(self._validar(remapeada, frases, janela, contexto))

    def _chamar(self, mensagens, esquema, medidor, resultado: ResultadoIdentificacao,
                *, janela=None, etapa="identificacao", tentativa=0, finalidade="extracao", motivo=None):
        tokens_prompt = sum(
            self.provedor.contar_tokens(mensagem["content"]) + TOKENS_POR_MENSAGEM
            for mensagem in mensagens
        )
        if tokens_prompt + self.configuracao.max_tokens_saida > self.configuracao.tamanho_contexto:
            # Sem truncamento silencioso: o llama.cpp reduziria a saída sozinho.
            raise _PrecisaDividir(
                f"Prompt de {tokens_prompt} tokens com reserva de "
                f"{self.configuracao.max_tokens_saida} excede o contexto de "
                f"{self.configuracao.tamanho_contexto}.",
                contexto=True,
            )
        try:
            from .metricas import medir
            resposta = medir(self.provedor, medidor, mensagens, esquema, resultado,
                             janela=janela if janela is not None else getattr(self, '_janela_metrica', None),
                             etapa=etapa, tentativa=tentativa, finalidade=finalidade, motivo=motivo)
        except ErroIdentificacaoInteracoes:
            raise
        except Exception as erro:
            raise ErroInferenciaError(f"{type(erro).__name__}: {erro}") from erro
        return resposta

    @staticmethod
    def _validar(
        dados: RespostaIdentificacao,
        frases: dict[str, UnidadeTexto],
        janela: JanelaTexto,
        contexto: ContextoValidacao,
    ) -> _ResultadoJanela:
        saida = _ResultadoJanela()
        for item in dados.invalidos:
            unidade = frases[item.id_frase]
            saida.rejeitar(
                item.id_frase,
                Rejeicao(
                    motivo=MotivoRejeicao.ITEM_FORA_DO_ESQUEMA,
                    alvo=item.alvo,
                    tipo_alvo=item.tipo_alvo,
                    resumo=item.resumo,
                    evidencia_informada=contexto.texto[unidade.inicio:unidade.fim],
                    detalhe=item.detalhe + " Use um dos tipos de alvo listados.",
                    indice_janela=janela.indice,
                ),
            )
        for frase in dados.frases:
            id_frase = frase.id_frase
            unidade = frases[id_frase]
            if frase.no_limite:
                # Pode haver mais alvos que o teto da gramática deixou escrever.
                saida.rejeitar(
                    id_frase,
                    Rejeicao(
                        motivo=MotivoRejeicao.LISTA_DE_ALVOS_NO_LIMITE,
                        alvo="",
                        tipo_alvo="",
                        resumo="",
                        evidencia_informada=contexto.texto[unidade.inicio:unidade.fim],
                        detalhe=f"A frase atingiu {MAX_ALVOS_POR_FRASE} alvos; a lista pode estar cortada.",
                        indice_janela=janela.indice,
                    ),
                )
            for informado in frase.alvos:
                validado = validar_alvo(
                    contexto,
                    alvo=informado.alvo,
                    tipo_alvo=informado.tipo_alvo,
                    resumo=informado.resumo,
                    inicio_evidencia=unidade.inicio,
                    fim_evidencia=unidade.fim,
                    indice_janela=janela.indice,
                )
                if isinstance(validado, RelacaoIdentificada):
                    saida.relacoes.append(validado)
                else:
                    saida.rejeitar(id_frase, validado)
        return saida
