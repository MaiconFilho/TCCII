"""Aplicação FastAPI de consulta das interações persistidas.

Não carrega modelos, não abre Selenium e não executa inferência nem
migrações: apenas lê o PostgreSQL. Inicie com ``python -m api_interacoes``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .configuracao import ConfiguracaoApi
from .erros import ErroConsulta
from .rotas import VERSAO_API, roteador
from .servico import ServicoConsulta


BASE_MODULO = Path(__file__).resolve().parent
LOGGER = logging.getLogger("api_interacoes")


def _erro(status: int, codigo: str, mensagem: str, detalhes: list | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"erro": codigo, "mensagem": mensagem, "detalhes": detalhes or []},
    )


def criar_app(configuracao: ConfiguracaoApi | None = None, repositorio=None) -> FastAPI:
    """``repositorio`` injetável nos testes; por padrão usa o PostgreSQL."""
    if configuracao is None:
        load_dotenv(BASE_MODULO / ".env")
        configuracao = ConfiguracaoApi.do_ambiente()
    if repositorio is None:
        from .repositorio import RepositorioConsulta

        repositorio = RepositorioConsulta(configuracao)

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI):
        abrir = getattr(repositorio, "abrir", None)
        if abrir:
            abrir()
        try:
            yield
        finally:
            fechar = getattr(repositorio, "fechar", None)
            if fechar:
                fechar()

    app = FastAPI(
        title="InteraMed — API de interações",
        version=VERSAO_API,
        description=(
            "Consulta as interações identificadas nos tópicos de interações das bulas. "
            "As relações vêm das fontes analisadas e não são exaustivas enquanto o corpus "
            "não estiver integralmente processado. Gravidade é sempre 'desconhecida'."
        ),
        lifespan=ciclo_de_vida,
    )
    app.state.configuracao = configuracao
    app.state.repositorio = repositorio
    app.state.servico = ServicoConsulta(repositorio, configuracao)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configuracao.origens_cors),
        allow_methods=["GET"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.exception_handler(ErroConsulta)
    async def tratar_erro_consulta(_request: Request, erro: ErroConsulta) -> JSONResponse:
        return _erro(erro.status_http, erro.codigo, erro.mensagem)

    @app.exception_handler(RequestValidationError)
    async def tratar_validacao(_request: Request, erro: RequestValidationError) -> JSONResponse:
        detalhes = [
            {"campo": ".".join(str(p) for p in item.get("loc", ())[1:]), "mensagem": item.get("msg", "")}
            for item in erro.errors()
        ]
        return _erro(422, "parametro_invalido", "Parâmetros inválidos.", detalhes)

    @app.exception_handler(Exception)
    async def tratar_inesperado(_request: Request, erro: Exception) -> JSONResponse:
        # Detalhes (credenciais, caminhos, SQL) ficam só no log do servidor.
        LOGGER.exception("Erro inesperado: %r", erro)
        return _erro(500, "erro_interno", "Erro interno ao consultar os dados.")

    app.include_router(roteador)
    return app
