from __future__ import annotations

import os
from dataclasses import dataclass, field


def _inteiro(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    valor = os.getenv(nome, str(padrao)).strip()
    try:
        convertido = int(valor)
    except ValueError as erro:
        raise ValueError(f"{nome} deve conter um inteiro.") from erro
    if not minimo <= convertido <= maximo:
        raise ValueError(f"{nome} deve estar entre {minimo} e {maximo}.")
    return convertido


# O front local abre por localhost ou 127.0.0.1; para o navegador são origens diferentes.
ORIGENS_PADRAO = ("http://localhost:3000", "http://127.0.0.1:3000")


@dataclass(frozen=True, slots=True)
class ConfiguracaoApi:
    dsn: str = ""
    origens_cors: tuple[str, ...] = field(default_factory=lambda: ORIGENS_PADRAO)
    limite_sugestoes_padrao: int = 6
    limite_sugestoes_maximo: int = 20
    tamanho_pagina_padrao: int = 20
    tamanho_pagina_maximo: int = 100
    limite_relacionados: int = 20
    pool_minimo: int = 1
    pool_maximo: int = 4
    tempo_limite_consulta_ms: int = 5000
    cache_cobertura_segundos: int = 60
    host: str = "127.0.0.1"
    porta: int = 8000
    dsn_solicitacoes: str = ''
    modelo_vetores: str = 'intfloat/multilingual-e5-small'
    inclusao_habilitada: bool = False
    trabalhador_automatico: bool = True
    python_trabalhador: str = ''
    dsn_trabalhador: str = ''

    @classmethod
    def do_ambiente(cls) -> "ConfiguracaoApi":
        origens = tuple(
            origem.strip()
            for origem in os.getenv("API_CORS_ORIGINS", ",".join(ORIGENS_PADRAO)).split(",")
            if origem.strip()
        )
        if "*" in origens:
            raise ValueError("API_CORS_ORIGINS deve listar origens explícitas, sem '*'.")
        automatico = os.getenv('API_WORKER_AUTOSTART', 'true').strip().lower()
        if automatico not in ('true', '1', 'false', '0'):
            raise ValueError('API_WORKER_AUTOSTART deve conter true ou false.')
        inclusao = os.getenv('API_INCLUSAO_MEDICAMENTOS', 'false').strip().lower()
        if inclusao not in ('true', '1', 'false', '0'):
            raise ValueError('API_INCLUSAO_MEDICAMENTOS deve conter true ou false.')
        limite_maximo = _inteiro("API_AUTOCOMPLETE_LIMITE_MAXIMO", 20, 1, 50)
        return cls(
            dsn=os.getenv("DATABASE_URL", "").strip(),
            origens_cors=origens,
            limite_sugestoes_padrao=_inteiro("API_AUTOCOMPLETE_LIMITE", 6, 1, limite_maximo),
            limite_sugestoes_maximo=limite_maximo,
            tamanho_pagina_padrao=_inteiro("API_TAMANHO_PAGINA", 20, 1, 100),
            tamanho_pagina_maximo=100,
            limite_relacionados=_inteiro("API_LIMITE_MEDICAMENTOS_RELACIONADOS", 20, 1, 200),
            pool_minimo=_inteiro("API_POOL_MINIMO", 1, 0, 20),
            pool_maximo=_inteiro("API_POOL_MAXIMO", 4, 1, 50),
            tempo_limite_consulta_ms=_inteiro("API_TEMPO_LIMITE_CONSULTA_MS", 5000, 100, 60000),
            cache_cobertura_segundos=_inteiro("API_CACHE_COBERTURA_SEGUNDOS", 60, 0, 3600),
            host=os.getenv("API_HOST", "127.0.0.1").strip(),
            porta=_inteiro("API_PORT", 8000, 1, 65535),
            dsn_solicitacoes=os.getenv('SOLICITACOES_DATABASE_URL', '').strip(),
            modelo_vetores=os.getenv('EMBEDDING_MODEL_ID', 'intfloat/multilingual-e5-small').strip(),
            inclusao_habilitada=inclusao in ('true', '1'),
            trabalhador_automatico=automatico in ('true', '1'),
            python_trabalhador=os.getenv('API_WORKER_PYTHON', '').strip(),
            dsn_trabalhador=os.getenv('API_WORKER_DATABASE_URL', '').strip(),
        )
