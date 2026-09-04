import hashlib
import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MedicamentoParaColeta:
    nome_produto: str
    nome_normalizado: str
    quantidade_registros_planilha: int = 1


@dataclass(frozen=True, slots=True)
class BulaLocalizada:
    nome_normalizado: str
    nome_produto: str
    numero_registro: str
    expediente: str
    id_bula_profissional: str
    data_publicacao: datetime
    id_produto: str = ""


def montar_slug_nome(nome_normalizado: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", nome_normalizado).strip("_")[:100]
    if slug:
        return slug
    return hashlib.sha256(nome_normalizado.encode("utf-8")).hexdigest()[:12]
