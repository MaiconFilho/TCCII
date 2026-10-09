"""Inicia a API: ``python -m api_interacoes`` a partir da raiz do projeto."""

from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .app import criar_app
from .configuracao import ConfiguracaoApi


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    try:
        configuracao = ConfiguracaoApi.do_ambiente()
    except ValueError as erro:
        print(f"Configuração inválida: {erro}")
        return 2
    if not configuracao.dsn:
        print("DATABASE_URL não configurada. Ajuste api_interacoes/.env.")
        return 4
    uvicorn.run(criar_app(configuracao), host=configuracao.host, port=configuracao.porta)
    return 0


raise SystemExit(main())
