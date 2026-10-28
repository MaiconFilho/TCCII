"""Aplicação FastAPI de consulta das interações persistidas e solicitação de preparação.

Não carrega modelos, não abre Selenium e não executa inferência nem
migrações. O único repositório de escrita é a fila de solicitações.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
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


class TrabalhadorAutomatico:
    """Inicia o processo existente; a reserva no PostgreSQL impede trabalho duplicado.

    O filho continua ao fechar a página ou a API. Uma nova consulta ou reinício da
    API relança um filho encerrado, respeitando um intervalo mínimo entre partidas.
    Nenhum módulo de coleta ou modelo é importado no processo da API.
    """

    def __init__(self, configuracao: ConfiguracaoApi):
        self.configuracao = configuracao
        self.processo = None
        self.proxima_partida = 0.0
        self.lock = threading.Lock()

    def garantir(self):
        if not self.configuracao.inclusao_habilitada or not self.configuracao.trabalhador_automatico:
            return
        with self.lock:
            if self.processo is not None and self.processo.poll() is None:
                return
            if time.monotonic() < self.proxima_partida:
                return
            self.proxima_partida = time.monotonic() + 30
            raiz = BASE_MODULO.parents[1]
            base = raiz / 'pipelines/inclusao_medicamentos'
            padrao = raiz / 'pipelines/extracao_interacoes/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            python = self.configuracao.python_trabalhador or (str(padrao) if padrao.is_file() else sys.executable)
            ambiente = os.environ.copy()
            # Permissões completas dos pipelines podem ser distintas das da fila.
            valores = dotenv_values(base / '.env')
            ambiente['WORKER_DATABASE_URL'] = (self.configuracao.dsn_trabalhador
                or valores.get('WORKER_DATABASE_URL') or valores.get('DATABASE_URL')
                or self.configuracao.dsn_solicitacoes or self.configuracao.dsn)
            ambiente['EMBEDDING_MODEL_ID'] = self.configuracao.modelo_vetores
            try:
                logs = base / 'logs'
                logs.mkdir(exist_ok=True)
                with (logs / 'inicializacao.log').open('a', encoding='utf-8') as log:
                    self.processo = subprocess.Popen(
                        [python, '-u', '-m', 'pipelines.inclusao_medicamentos'],
                        cwd=raiz, env=ambiente, stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT, shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    )
            except OSError:
                LOGGER.exception('Não foi possível iniciar o trabalhador automático. Confira API_WORKER_PYTHON e as dependências dos pipelines.')


def _erro(status: int, codigo: str, mensagem: str, detalhes: list | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"erro": codigo, "mensagem": mensagem, "detalhes": detalhes or []},
    )


def criar_app(configuracao: ConfiguracaoApi | None = None, repositorio=None, solicitacoes=None, trabalhador=None) -> FastAPI:
    """``repositorio`` injetável nos testes; por padrão usa o PostgreSQL."""
    if configuracao is None:
        load_dotenv(BASE_MODULO / ".env")
        configuracao = ConfiguracaoApi.do_ambiente()
    # Repositórios simulados não iniciam processos reais nos testes.
    if trabalhador is None and solicitacoes is None and repositorio is None:
        trabalhador = TrabalhadorAutomatico(configuracao)
    if repositorio is None:
        from .repositorio import RepositorioConsulta

        repositorio = RepositorioConsulta(configuracao)

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI):
        abrir = getattr(repositorio, "abrir", None)
        if abrir:
            abrir()
        if configuracao.inclusao_habilitada and trabalhador is not None:
            trabalhador.garantir()
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
    app.state.trabalhador = trabalhador
    app.state.repositorio = repositorio
    app.state.servico = ServicoConsulta(repositorio, configuracao)
    from pipelines.inclusao_medicamentos.repositorio import RepositorioSolicitacoes, ErroSolicitacao
    app.state.solicitacoes = solicitacoes if solicitacoes is not None else RepositorioSolicitacoes(
        configuracao.dsn_solicitacoes or configuracao.dsn, configuracao.modelo_vetores)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configuracao.origens_cors),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.exception_handler(ErroSolicitacao)
    async def tratar_solicitacao(_request: Request, erro: ErroSolicitacao) -> JSONResponse:
        LOGGER.warning('Solicitação indisponível: %s', erro, exc_info=erro.status_http == 503)
        return _erro(erro.status_http, 'erro_solicitacao', erro.mensagem)

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
