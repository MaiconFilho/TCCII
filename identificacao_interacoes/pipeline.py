from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Protocol

from .erros import (
    ErroBancoError,
    FonteAlteradaError,
    MedicamentoEmAndamentoError,
    ProcessamentoJaExisteError,
)
from .metricas import agregar
from .modelos import (
    FonteInteracoes,
    ResultadoIdentificacao,
    StatusExecucao,
    StatusProcessamento,
    TipoAlvo,
    Vinculo,
)
from .resolucao import CatalogoMedicamentos


LOGGER = logging.getLogger(__name__)
# Um princípio ativo comum liga centenas de bulas; o detalhe mostra uma amostra.
LIMITE_VINCULOS_DETALHES = 5


class RepositorioProtocolo(Protocol):
    def bloquear_medicamento(self, nome_normalizado: str) -> bool: ...
    def liberar_medicamento(self, nome_normalizado: str) -> None: ...
    def processamento_registrado(self, nome_normalizado: str) -> dict | None: ...
    def gravar_resultado(self, fonte, resultado, vinculos, **kwargs) -> tuple[int, int]: ...
    def registrar_falha(self, fonte, resultado, **kwargs) -> int: ...
    def interacoes_do_processamento(self, processamento_id: int) -> list[dict]: ...
    def atualizar_vinculos(self, processamento_id, medicamento_origem, vinculos_por_interacao) -> dict: ...


class ServicoProtocolo(Protocol):
    versao_processamento: str
    modelo: str

    def identificar(self, fonte: FonteInteracoes) -> ResultadoIdentificacao: ...


class RelatorioProtocolo(Protocol):
    def registrar(self, dados: dict, detalhes: dict | None = None) -> None: ...


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _linha(
    fonte: FonteInteracoes,
    status: StatusExecucao,
    resultado: ResultadoIdentificacao | None,
    *,
    versao: str,
    processamento_id: int | None = None,
    vinculos_criados: int = 0,
    tempo_banco: float = 0.0,
    tempo_total: float = 0.0,
    detalhe: str = "",
) -> dict[str, Any]:
    r = resultado
    return {
        "nome_normalizado": fonte.nome_normalizado,
        "status": status.value,
        "processamento_id": processamento_id or "",
        "versao_processamento": versao,
        "caracteres_trecho": len(fonte.trecho_interacoes or ""),
        "quantidade_janelas": r.quantidade_janelas if r else 0,
        "janelas_concluidas": r.janelas_concluidas if r else 0,
        "divisoes_por_limite": r.divisoes_por_limite if r else 0,
        "releituras": r.releituras if r else 0,
        "chamadas_llm": r.chamadas_llm if r else 0,
        "tokens_entrada_total": r.tokens_entrada_total if r else 0,
        "tokens_saida_total": r.tokens_saida_total if r else 0,
        "relacoes_extraidas": len(r.relacoes) if r and r.status != StatusProcessamento.FALHA else 0,
        "duplicatas_consolidadas": r.duplicatas_consolidadas if r else 0,
        "rejeicoes": len(r.rejeicoes) if r else 0,
        "rejeicoes_pendentes": len(r.rejeicoes_pendentes) if r else 0,
        "propostas_brutas": r.propostas_brutas if r else 0,
        "aceitas_antes_consolidacao": r.aceitas_antes_consolidacao if r else 0,
        "resumos_gerados": r.resumos_gerados if r else 0,
        "resumos_aceitos": r.resumos_aceitos if r else 0,
        "resumos_rejeitados": r.resumos_rejeitados if r else 0,
        "chamadas_adicionais": sum(bool(c['motivo_adicional']) for c in r.metricas_chamadas) if r else 0,
        "vinculos_criados": vinculos_criados,
        "tempo_inferencia_segundos": round(r.tempo_inferencia_segundos, 3) if r else 0,
        "tempo_banco_segundos": round(tempo_banco, 3),
        "tempo_total_segundos": round(tempo_total, 3),
        "memoria_antes_mb": round(r.memoria_antes_mb, 1) if r else "",
        "pico_memoria_mb": round(r.pico_memoria_mb, 1) if r else "",
        "memoria_depois_mb": round(r.memoria_depois_mb, 1) if r else "",
        "detalhe_erro": detalhe or (r.detalhe_erro if r else ""),
    }


def _detalhes(
    fonte: FonteInteracoes,
    status: StatusExecucao,
    resultado: ResultadoIdentificacao | None,
    vinculos: Sequence[Sequence[Vinculo]] = (),
) -> dict[str, Any]:
    if resultado is None:
        return {"nome_normalizado": fonte.nome_normalizado, "status": status.value}
    relacoes = []
    for indice, relacao in enumerate(resultado.relacoes):
        dados = asdict(relacao)
        lista = vinculos[indice] if indice < len(vinculos) else []
        dados["vinculos"] = [asdict(v) for v in lista[:LIMITE_VINCULOS_DETALHES]]
        dados["total_vinculos"] = len(lista)
        relacoes.append(dados)
    return {
        "tipo": "resultado",
        "nome_normalizado": fonte.nome_normalizado,
        "status": status.value,
        "relacoes": relacoes,
        "rejeicoes": [asdict(rejeicao) for rejeicao in resultado.rejeicoes],
        "rejeicoes_pendentes": [asdict(rejeicao) for rejeicao in resultado.rejeicoes_pendentes],
        "avisos": resultado.avisos,
        "hash_trecho": fonte.hash_trecho,
        "metricas_chamadas": resultado.metricas_chamadas,
        "chamadas_por_finalidade": agregar(resultado.metricas_chamadas),
        "rejeicoes_por_etapa": resultado.rejeicoes_por_etapa,
        "propostas_brutas": resultado.propostas_brutas,
        "aceitas_antes_consolidacao": resultado.aceitas_antes_consolidacao,
        "resumos_gerados": resultado.resumos_gerados,
        "resumos_aceitos": resultado.resumos_aceitos,
        "resumos_rejeitados": resultado.resumos_rejeitados,
    }


def fonte_coerente(fonte: FonteInteracoes) -> tuple[bool, str]:
    if not fonte.identificadores_coerentes:
        return False, "Registro/expediente de bulas_interacoes diferem de bulas."
    if fonte.hash_trecho_banco and fonte.hash_trecho_banco != fonte.hash_trecho:
        return False, "Hash do texto calculado no PostgreSQL difere do calculado no Python."
    return True, ""


def processar_lote(
    fontes: Sequence[FonteInteracoes],
    servico: ServicoProtocolo,
    catalogo: CatalogoMedicamentos,
    repositorio: RepositorioProtocolo,
    relatorio: RelatorioProtocolo,
    *,
    reprocessar: bool = False,
    agora: Callable[[], datetime] = _agora,
) -> dict[str, int]:
    """Sequencial: um medicamento por vez, um modelo por processo."""
    contagens: Counter[str] = Counter()
    versao = servico.versao_processamento

    for indice, fonte in enumerate(fontes, start=1):
        prefixo = f"[{indice}/{len(fontes)}] {fonte.nome_normalizado}"
        inicio = time.perf_counter()
        iniciado_em = agora()
        resultado: ResultadoIdentificacao | None = None
        vinculos: list[list[Vinculo]] = []
        processamento_id: int | None = None
        vinculos_criados = 0
        tempo_banco = 0.0
        detalhe = ""

        coerente, motivo = fonte_coerente(fonte)
        if not coerente:
            status = StatusExecucao.FONTE_INCOERENTE
            detalhe = motivo
        else:
            try:
                obtido = repositorio.bloquear_medicamento(fonte.nome_normalizado)
            except Exception as erro:  # noqa: BLE001
                LOGGER.exception("Falha ao bloquear %s: %r", fonte.nome_normalizado, erro)
                obtido = None
                status = StatusExecucao.ERRO_BANCO
                detalhe = f"{type(erro).__name__}: {erro}"
            if obtido is False:
                status = StatusExecucao.EM_ANDAMENTO_OUTRO_PROCESSO
            elif obtido:
                try:
                    status, resultado, vinculos, processamento_id, vinculos_criados, tempo_banco, detalhe = (
                        _processar_bloqueado(
                            fonte, servico, catalogo, repositorio, versao, reprocessar,
                            iniciado_em, inicio, prefixo,
                        )
                    )
                finally:
                    try:
                        repositorio.liberar_medicamento(fonte.nome_normalizado)
                    except Exception as erro:  # noqa: BLE001 - a sessão libera ao fechar
                        LOGGER.exception("Falha ao liberar %s: %r", fonte.nome_normalizado, erro)

        tempo_total = time.perf_counter() - inicio
        relatorio.registrar(
            _linha(
                fonte, status, resultado, versao=versao, processamento_id=processamento_id,
                vinculos_criados=vinculos_criados, tempo_banco=tempo_banco,
                tempo_total=tempo_total, detalhe=detalhe,
            ),
            _detalhes(fonte, status, resultado, vinculos),
        )
        LOGGER.info(
            "%s status=%s relacoes=%s rejeicoes=%s vinculos=%s tempo=%.1fs pico=%sMB",
            fonte.nome_normalizado, status.value,
            len(resultado.relacoes) if resultado else 0,
            len(resultado.rejeicoes) if resultado else 0,
            vinculos_criados, tempo_total,
            round(resultado.pico_memoria_mb) if resultado else "-",
        )
        contagens[status.value] += 1
        extra = ""
        if resultado is not None and status != StatusExecucao.FALHA:
            extra = (
                f" ({len(resultado.relacoes)} relação(ões), {len(resultado.rejeicoes)} rejeição(ões), "
                f"{vinculos_criados} vínculo(s), {tempo_total:.1f} s)"
            )
        print(f"{prefixo} — {status.value}{extra}.")
    return dict(sorted(contagens.items()))


def simular_lote(
    fontes: Sequence[FonteInteracoes],
    servico: ServicoProtocolo,
    catalogo: CatalogoMedicamentos,
    relatorio: RelatorioProtocolo,
) -> dict[str, int]:
    """Identifica e resolve vínculos sem gravar nada nem bloquear o banco.

    Serve para conferir uma versão antes de publicá-la: o relatório tem o
    mesmo formato do processamento real, sem ``processamento_id``.
    """
    contagens: Counter[str] = Counter()
    versao = servico.versao_processamento
    for fonte in fontes:
        inicio = time.perf_counter()
        coerente, motivo = fonte_coerente(fonte)
        resultado, vinculos, detalhe = None, [], ""
        if not coerente:
            status, detalhe = StatusExecucao.FONTE_INCOERENTE, motivo
        else:
            resultado = servico.identificar(fonte)
            status = StatusExecucao(resultado.status.value)
            if resultado.status != StatusProcessamento.FALHA:
                vinculos = [
                    catalogo.resolver(relacao.alvo, relacao.tipo_alvo, fonte.nome_normalizado)
                    for relacao in resultado.relacoes
                ]
        relatorio.registrar(
            _linha(
                fonte, status, resultado, versao=versao,
                vinculos_criados=sum(len(lista) for lista in vinculos),
                tempo_total=time.perf_counter() - inicio, detalhe=detalhe,
            ),
            _detalhes(fonte, status, resultado, vinculos),
        )
        contagens[status.value] += 1
    return dict(sorted(contagens.items()))


def _processar_bloqueado(
    fonte, servico, catalogo, repositorio, versao, reprocessar, iniciado_em, inicio, prefixo
):
    resultado = None
    vinculos: list[list[Vinculo]] = []
    if not reprocessar:
        try:
            # Inclui resultados em revisão: repetir a mesma versão daria o mesmo
            # resultado. Só --reprocessar força uma nova execução.
            registrado = repositorio.processamento_registrado(fonte.nome_normalizado)
        except Exception as erro:  # noqa: BLE001
            LOGGER.exception("Falha ao consultar vigência de %s: %r", fonte.nome_normalizado, erro)
            return StatusExecucao.ERRO_BANCO, None, [], None, 0, 0.0, f"{type(erro).__name__}: {erro}"
        if registrado and registrado["versao_processamento"] == versao:
            return StatusExecucao.IGNORADO_JA_PROCESSADO, None, [], registrado["id"], 0, 0.0, ""

    print(f"{prefixo} — identificando interações...")
    resultado = servico.identificar(fonte)
    inicio_banco = time.perf_counter()
    comum = {"versao": versao, "modelo": servico.modelo, "iniciado_em": iniciado_em}
    try:
        if resultado.status == StatusProcessamento.FALHA:
            processamento_id = repositorio.registrar_falha(
                fonte, resultado, tempo_total_segundos=time.perf_counter() - inicio,
                detalhe=resultado.detalhe_erro, **comum,
            )
            return (StatusExecucao.FALHA, resultado, [], processamento_id, 0,
                    time.perf_counter() - inicio_banco, "")
        vinculos = [
            catalogo.resolver(relacao.alvo, relacao.tipo_alvo, fonte.nome_normalizado)
            for relacao in resultado.relacoes
        ]
        processamento_id, criados = repositorio.gravar_resultado(
            fonte, resultado, vinculos, tempo_total_segundos=time.perf_counter() - inicio,
            reprocessar=reprocessar, **comum,
        )
        return (StatusExecucao(resultado.status.value), resultado, vinculos, processamento_id,
                criados, time.perf_counter() - inicio_banco, "")
    except FonteAlteradaError as erro:
        LOGGER.warning("Fonte alterada durante %s: %s", fonte.nome_normalizado, erro)
        detalhe = f"Fonte alterada durante o processamento; nada gravado. {erro}"
        processamento_id = None
        try:
            processamento_id = repositorio.registrar_falha(
                fonte, resultado, tempo_total_segundos=time.perf_counter() - inicio,
                detalhe=detalhe, **comum,
            )
        except ErroBancoError as segundo:
            LOGGER.exception("Falha ao registrar fonte alterada: %r", segundo)
        return (StatusExecucao.FONTE_ALTERADA, resultado, vinculos, processamento_id, 0,
                time.perf_counter() - inicio_banco, detalhe)
    except ProcessamentoJaExisteError as erro:
        return (StatusExecucao.IGNORADO_JA_PROCESSADO, resultado, vinculos, None, 0,
                time.perf_counter() - inicio_banco, str(erro))
    except Exception as erro:  # noqa: BLE001 - rollback já ocorreu na transação
        LOGGER.exception("Falha de banco em %s: %r", fonte.nome_normalizado, erro)
        return (StatusExecucao.ERRO_BANCO, resultado, vinculos, None, 0,
                time.perf_counter() - inicio_banco, f"{type(erro).__name__}: {erro}")


def atualizar_vinculos_lote(
    processamentos: Sequence[dict],
    catalogo: CatalogoMedicamentos,
    repositorio: RepositorioProtocolo,
    relatorio: RelatorioProtocolo,
) -> dict[str, int]:
    """Recalcula vínculos dos processamentos vigentes sem chamar o modelo."""
    contagens: Counter[str] = Counter()
    for indice, processamento in enumerate(processamentos, start=1):
        origem = processamento["medicamento_origem"]
        prefixo = f"[{indice}/{len(processamentos)}] {origem}"
        inicio = time.perf_counter()
        fonte = FonteInteracoes(origem, origem, None, "", None, None)
        contagem: dict[str, int] = {}
        detalhe = ""
        try:
            interacoes = repositorio.interacoes_do_processamento(processamento["id"])
            desejados = {
                linha["id"]: catalogo.resolver(linha["alvo"], TipoAlvo(linha["tipo_alvo"]), origem)
                for linha in interacoes
            }
            contagem = repositorio.atualizar_vinculos(processamento["id"], origem, desejados)
            status = StatusExecucao.VINCULOS_ATUALIZADOS
        except MedicamentoEmAndamentoError as erro:
            status, detalhe = StatusExecucao.EM_ANDAMENTO_OUTRO_PROCESSO, str(erro)
        except FonteAlteradaError as erro:
            status, detalhe = StatusExecucao.FONTE_ALTERADA, str(erro)
        except Exception as erro:  # noqa: BLE001
            LOGGER.exception("Falha ao atualizar vínculos de %s: %r", origem, erro)
            status, detalhe = StatusExecucao.ERRO_BANCO, f"{type(erro).__name__}: {erro}"
        tempo = time.perf_counter() - inicio
        linha = _linha(
            fonte, status, None, versao="", processamento_id=processamento["id"],
            vinculos_criados=contagem.get("criados", 0), tempo_banco=tempo, tempo_total=tempo,
            detalhe=detalhe,
        )
        linha.update(
            caracteres_trecho="",
            vinculos_reativados=contagem.get("reativados", 0),
            vinculos_atualizados=contagem.get("atualizados", 0),
            vinculos_invalidados=contagem.get("invalidados", 0),
        )
        relatorio.registrar(linha, {"nome_normalizado": origem, "status": status.value, **contagem})
        contagens[status.value] += 1
        print(f"{prefixo} — {status.value} {contagem or detalhe}")
    return dict(sorted(contagens.items()))
