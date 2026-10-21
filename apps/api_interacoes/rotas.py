from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from .contratos import Erro, RespostaInteracoes, RespostaPar, RespostaSugestoes, Saude
from .servico import ServicoConsulta


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
        "Relações descritas na bula do medicamento e relações de outras bulas vinculadas "
        "explicitamente a ele, inclusive classes, alimentos e alvos sem medicamento associado. "
        "Ordenação estável: bula pesquisada primeiro, depois as demais por origem e id."
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
