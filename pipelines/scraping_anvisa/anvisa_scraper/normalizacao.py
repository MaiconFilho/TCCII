"""Normalização dos nomes, sem dependências externas.

É a regra que gera ``bulas.nome_normalizado``. Fica isolada para que outras
etapas (identificação das interações e API) a reutilizem sem importar a leitura
da planilha nem as dependências do scraper.
"""

import re
import unicodedata


def normalizar_nome(valor: object) -> str:
    texto = str(valor or "").strip().casefold()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r"\s+", " ", texto).strip()
