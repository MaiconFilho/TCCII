from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator


class StatusExtracao(StrEnum):
    CONCLUIDO = "CONCLUIDO"
    SEM_SECAO_INTERACOES = "SEM_SECAO_INTERACOES"
    PDF_SEM_TEXTO = "PDF_SEM_TEXTO"
    PDF_INVALIDO = "PDF_INVALIDO"
    CONTEXTO_EXCEDIDO = "CONTEXTO_EXCEDIDO"
    RESPOSTA_INVALIDA = "RESPOSTA_INVALIDA"
    RESPOSTA_TRUNCADA = "RESPOSTA_TRUNCADA"
    ERRO_MODELO = "ERRO_MODELO"
    ERRO_BANCO = "ERRO_BANCO"


@dataclass(frozen=True, slots=True)
class BulaParaExtracao:
    nome_normalizado: str
    numero_registro: str
    expediente: str | None
    caminho_pdf: Path


@dataclass(frozen=True, slots=True)
class DocumentoPdf:
    texto_com_marcadores: str
    texto_sem_marcadores: str
    quantidade_paginas: int
    quantidade_caracteres: int


@dataclass(frozen=True, slots=True)
class ConfiguracaoModelo:
    modelo_id: str
    max_input_tokens: int
    max_new_tokens: int
    dispositivo: str = "auto"


@dataclass(frozen=True, slots=True)
class GeracaoModelo:
    conteudo: str
    quantidade_tokens_entrada: int
    quantidade_tokens_saida: int
    truncada: bool


class RespostaExtracao(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    encontrado: StrictBool
    titulo_encontrado: str | None
    trecho_interacoes: str | None

    @model_validator(mode="after")
    def validar_coerencia(self) -> "RespostaExtracao":
        titulo = (self.titulo_encontrado or "").strip()
        trecho = (self.trecho_interacoes or "").strip()
        if self.encontrado and (not titulo or not trecho):
            raise ValueError(
                "titulo_encontrado e trecho_interacoes são obrigatórios "
                "quando encontrado=true"
            )
        if not self.encontrado and (
            self.titulo_encontrado is not None
            or self.trecho_interacoes is not None
        ):
            raise ValueError(
                "titulo_encontrado e trecho_interacoes devem ser null "
                "quando encontrado=false"
            )
        return self
