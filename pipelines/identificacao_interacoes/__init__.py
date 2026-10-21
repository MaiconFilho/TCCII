"""Identificação estruturada das interações a partir de bulas_interacoes.

O pacote expõe aqui somente os modelos de domínio, sem efeitos colaterais: a
API importa estas definições sem carregar o llama.cpp, o PyMuPDF ou pesos.
Serviço, repositório e CLI são importados explicitamente por quem os usa.
"""

from .modelos import (
    CriterioVinculo,
    StatusProcessamento,
    TipoAlvo,
)

__all__ = ["CriterioVinculo", "StatusProcessamento", "TipoAlvo"]
