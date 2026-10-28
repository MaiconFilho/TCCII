from __future__ import annotations

from typing import Annotated

from uuid import UUID
from fastapi import APIRouter, Query, Request, Response
from pipelines.inclusao_medicamentos.estado import resposta

from .contratos import (Erro, RespostaInteracoes, RespostaPar, RespostaSugestoes, Saude,
                        PedidoPreparacao, SelecaoAnvisa, RespostaPreparacao)
from .servico import ServicoConsulta
from pipelines.inclusao_medicamentos.repositorio import ErroSolicitacao


VERSAO_API = "1.0.0"
ERROS_COMUNS = {
    422: {"model": Erro, "description": "Parâmetro inválido."},
    503: {"model": Erro, "description": "Serviço de consulta indisponível ou ainda não preparado."},
}

roteador = APIRouter(prefix="/api")


def _servico(request: Request) -> ServicoConsulta:
    return request.app.state.servico


@roteador.get(
    "/saude",
    response_model=Saude,
    tags=["saúde"],
    summary="Verificação de saúde",
    description="Confere a conexão com o PostgreSQL e a presença das tabelas, sem modelos.",
)
def saude(request: Request) -> Saude:
    banco, esquema = request.app.state.repositorio.verificar()
    return Saude(
        status="ok" if banco and esquema else "degradado",
        banco="ok" if banco else "indisponivel",
        esquema=("ok" if esquema else "ausente") if banco else "desconhecido",
        versao_api=VERSAO_API,
    )


@roteador.get(
    "/medicamentos/sugestoes",
    response_model=RespostaSugestoes,
    responses=ERROS_COMUNS,
    tags=["medicamentos"],
    summary="Autocomplete de medicamentos",
    description=(
        "Sugestões por nome, sem LLM. Caixa, acentos e espaços seguem a mesma "
        "normalização de bulas.nome_normalizado. Nomes que começam pelo termo vêm primeiro."
    ),
)
def sugestoes(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=100, description="Termo digitado.")],
    limite: Annotated[int | None, Query(ge=1, le=50, description="Padrão: API_AUTOCOMPLETE_LIMITE (6).")] = None,
) -> RespostaSugestoes:
    servico = _servico(request)
    maximo = servico.configuracao.limite_sugestoes_maximo
    return servico.sugerir(q, min(limite, maximo) if limite else None)


@roteador.get(
    "/interacoes",
    response_model=RespostaInteracoes,
    responses={**ERROS_COMUNS, 404: {"model": Erro, "description": "Medicamento inexistente."}},
    tags=["interações"],
    summary="Interações de um medicamento",
    description=(
        "Somente relações descritas na bula do medicamento pesquisado, inclusive classes, "
        "alimentos e alvos sem medicamento associado, ordenadas por id. Traz o resumo geral e "
        "os resumos de card já gravados pela etapa offline; a API não executa LLM."
    ),
)
def interacoes(
    request: Request,
    medicamento: Annotated[str, Query(min_length=1, max_length=200, description="nome_normalizado.")],
    pagina: Annotated[int, Query(ge=1, le=10000)] = 1,
    tamanho_pagina: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> RespostaInteracoes:
    servico = _servico(request)
    tamanho = tamanho_pagina or servico.configuracao.tamanho_pagina_padrao
    return servico.interacoes(medicamento, pagina, tamanho)


@roteador.get(
    "/interacoes/par",
    response_model=RespostaPar,
    responses={**ERROS_COMUNS, 404: {"model": Erro, "description": "Medicamento inexistente."}},
    tags=["interações"],
    summary="Relações entre dois medicamentos",
    description=(
        "Considera os dois sentidos e agrupa itens que compartilham a mesma evidência. "
        "Alertas sobre classes ficam separados e não confirmam interação específica."
    ),
)
def par(
    request: Request,
    medicamento_a: Annotated[str, Query(min_length=1, max_length=200)],
    medicamento_b: Annotated[str, Query(min_length=1, max_length=200)],
) -> RespostaPar:
    return _servico(request).par(medicamento_a, medicamento_b)


@roteador.post('/processamentos', status_code=202, response_model=RespostaPreparacao,
               responses={**ERROS_COMUNS, 200: {'model': RespostaPreparacao, 'description': 'Medicamento já cadastrado.'}}, tags=['preparação'])
def solicitar_processamento(pedido: PedidoPreparacao, request: Request, response: Response):
    _verificar_inclusao(request)
    dados = request.app.state.solicitacoes.solicitar(pedido.nome)
    if dados['estado'] == 'CONCLUIDO':
        response.status_code = 200
    _garantir_trabalhador(request, dados)
    return dados


@roteador.get('/processamentos/{identificador}', response_model=RespostaPreparacao, responses=ERROS_COMUNS, tags=['preparação'])
def andamento_processamento(identificador: UUID, request: Request):
    _verificar_inclusao(request)
    dados = resposta(request.app.state.solicitacoes.obter(identificador))
    _garantir_trabalhador(request, dados)
    return dados


@roteador.post('/processamentos/{identificador}/selecao', status_code=202, response_model=RespostaPreparacao, responses=ERROS_COMUNS, tags=['preparação'])
def selecionar_medicamento_anvisa(identificador: UUID, pedido: SelecaoAnvisa, request: Request):
    _verificar_inclusao(request)
    dados = request.app.state.solicitacoes.selecionar(identificador, pedido.nome_normalizado)
    _garantir_trabalhador(request, dados)
    return dados


def _verificar_inclusao(request: Request):
    if not request.app.state.configuracao.inclusao_habilitada:
        raise ErroSolicitacao('A inclusão de novos medicamentos está desativada. Consulte um medicamento já disponível.')


def _garantir_trabalhador(request: Request, dados: dict):
    trabalhador = request.app.state.trabalhador
    if trabalhador is not None and dados['estado'] in ('AGUARDANDO', 'EM_PROCESSAMENTO'):
        trabalhador.garantir()
