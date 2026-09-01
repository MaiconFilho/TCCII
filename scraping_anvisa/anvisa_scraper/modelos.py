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
    data_atualizacao: datetime
    data_atualizacao_original: str
    id_produto: str = ""
