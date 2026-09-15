from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StatusExtracao(StrEnum):
    CONCLUIDO = "CONCLUIDO"
    SEM_SECAO_INTERACOES = "SEM_SECAO_INTERACOES"
    SECAO_SOMENTE_TITULO = "SECAO_SOMENTE_TITULO"
    PDF_SEM_TEXTO = "PDF_SEM_TEXTO"
    OCR_INDISPONIVEL = "OCR_INDISPONIVEL"
    ERRO_OCR = "ERRO_OCR"
    PDF_INVALIDO = "PDF_INVALIDO"
    RESPOSTA_INVALIDA = "RESPOSTA_INVALIDA"
    LIMITE_MEMORIA = "LIMITE_MEMORIA"
    ERRO_CARREGAMENTO_MODELO = "ERRO_CARREGAMENTO_MODELO"
    ERRO_INFERENCIA = "ERRO_INFERENCIA"
    ERRO_BANCO = "ERRO_BANCO"
    REVISAO_MANUAL = "REVISAO_MANUAL"


class MetodoExtracao(StrEnum):
    HIBRIDO_LLM = "HIBRIDO_LLM"
    LLM = "LLM"
    LLM_SEGUNDA_TENTATIVA = "LLM_SEGUNDA_TENTATIVA"
    FALLBACK_ESTRUTURAL = "FALLBACK_ESTRUTURAL"
    REVISAO_MANUAL = "REVISAO_MANUAL"


class TipoOcorrencia(StrEnum):
    SECAO_CORPO = "SECAO_CORPO"
    SUMARIO = "SUMARIO"
    MENCAO_ISOLADA = "MENCAO_ISOLADA"
    CONTEUDO_CONTINUACAO = "CONTEUDO_CONTINUACAO"
    NAO_ENCONTRADO = "NAO_ENCONTRADO"


class Confianca(StrEnum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAIXA = "BAIXA"

    @property
    def peso(self) -> int:
        return {self.ALTA: 3, self.MEDIA: 2, self.BAIXA: 1}[self]


@dataclass(frozen=True, slots=True)
class BulaParaExtracao:
    nome_normalizado: str
    numero_registro: str
    expediente: str | None
    caminho_pdf: Path


@dataclass(frozen=True, slots=True)
class LinhaDocumento:
    numero: int
    pagina: int
    posicao_pagina: int
    texto_original: str
    texto_normalizado: str

    @property
    def identificador(self) -> str:
        return f"L{self.numero:06d}"

    @property
    def texto_prompt(self) -> str:
        return f"{self.identificador} | {self.texto_original}"


@dataclass(frozen=True, slots=True)
class DocumentoPdf:
    linhas: tuple[LinhaDocumento, ...]
    quantidade_paginas: int
    quantidade_caracteres: int
    paginas_ocr: tuple[int, ...] = ()
    tempo_ocr_segundos: float = 0.0
    acertos_cache_ocr: int = 0

    @property
    def texto_original(self) -> str:
        return "\n".join(linha.texto_original for linha in self.linhas)

    def obter_linha(self, identificador: str) -> LinhaDocumento | None:
        if not identificador.startswith("L") or not identificador[1:].isdigit():
            return None
        numero = int(identificador[1:])
        if numero < 1 or numero > len(self.linhas):
            return None
        return self.linhas[numero - 1]


@dataclass(frozen=True, slots=True)
class JanelaDocumento:
    indice: int
    linhas: tuple[LinhaDocumento, ...]
    tokens_estimados: int

    @property
    def linha_inicio(self) -> str:
        return self.linhas[0].identificador

    @property
    def linha_fim_inclusiva(self) -> str:
        return self.linhas[-1].identificador

    @property
    def texto_prompt(self) -> str:
        return "\n".join(linha.texto_prompt for linha in self.linhas)


@dataclass(frozen=True, slots=True)
class ConfiguracaoLLM:
    backend: str
    repositorio_modelo: str
    arquivo_modelo: str
    tamanho_contexto: int = 4096
    tokens_janela: int = 700
    sobreposicao_tokens: int = 100
    max_tokens_saida: int = 256
    threads: int = 0
    tamanho_lote: int = 128
    camadas_gpu: int = 0
    temperatura: float = 0.0
    limite_memoria_mb: int = 5500
    concorrencia: int = 1
    modo_extracao: str = "rapido"

    def validar(self) -> None:
        if self.modo_extracao not in {"rapido", "completo"}:
            raise ValueError("EXTRACAO_MODO deve ser rapido ou completo.")
        if self.backend != "llama_cpp":
            raise ValueError("LLM_BACKEND deve ser llama_cpp.")
        if self.tamanho_contexto <= 0 or self.tokens_janela <= 0:
            raise ValueError("Os limites de contexto e janela devem ser positivos.")
        if self.sobreposicao_tokens < 0:
            raise ValueError("LLM_CHUNK_OVERLAP não pode ser negativo.")
        if self.sobreposicao_tokens >= self.tokens_janela:
            raise ValueError("LLM_CHUNK_OVERLAP deve ser menor que LLM_CHUNK_TOKENS.")
        if self.max_tokens_saida <= 0 or self.tamanho_lote <= 0:
            raise ValueError("Saída e lote devem possuir valores positivos.")
        if self.threads < 0 or self.camadas_gpu < 0:
            raise ValueError("Threads e camadas de GPU não podem ser negativos.")
        if self.temperatura < 0:
            raise ValueError("LLM_TEMPERATURE não pode ser negativa.")
        if self.concorrencia != 1:
            raise ValueError("LLM_INFERENCE_CONCURRENCY deve ser 1 neste perfil.")


@dataclass(frozen=True, slots=True)
class RespostaLLM:
    conteudo: str
    tokens_entrada: int
    tokens_saida: int
    truncada: bool = False


class RespostaConfirmacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmado: bool


class RespostaClassificacao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo_ocorrencia: TipoOcorrencia
    linha_titulo: str | None = None
    titulo_encontrado: str | None = None
    confianca: Confianca = Confianca.BAIXA

    @model_validator(mode="after")
    def validar_coerencia(self) -> "RespostaClassificacao":
        if self.tipo_ocorrencia == TipoOcorrencia.SECAO_CORPO:
            if not (self.linha_titulo or "").strip() or not (
                self.titulo_encontrado or ""
            ).strip():
                raise ValueError(
                    "SECAO_CORPO exige linha_titulo e titulo_encontrado."
                )
        elif self.linha_titulo is not None or self.titulo_encontrado is not None:
            raise ValueError("Somente SECAO_CORPO pode informar título e linha.")
        return self


class RespostaDesempate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linha_titulo_escolhida: str
    justificativa_curta: str = Field(min_length=1, max_length=200)


class RespostaLimite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encontrou_fim: bool
    linha_inicio: str
    linha_fim_exclusiva: str | None = None
    titulo_encontrado: str
    proximo_titulo: str | None = None
    fim_documento: bool = False

    @model_validator(mode="after")
    def validar_coerencia(self) -> "RespostaLimite":
        if self.encontrou_fim and not (
            self.linha_fim_exclusiva or self.fim_documento
        ):
            raise ValueError("O fim exige uma linha exclusiva ou fim_documento=true.")
        if not self.encontrou_fim and (
            self.linha_fim_exclusiva is not None or self.fim_documento
        ):
            raise ValueError("Uma janela que continua não pode informar o fim.")
        return self


@dataclass(frozen=True, slots=True)
class CandidatoSecao:
    resposta: RespostaClassificacao
    indice_janela: int
    segunda_tentativa: bool = False


@dataclass(slots=True)
class ResultadoExtracao:
    status: StatusExtracao
    metodo: MetodoExtracao | None = None
    titulo_encontrado: str = ""
    trecho_interacoes: str | None = None
    linha_inicio: str = ""
    linha_fim_exclusiva: str = ""
    quantidade_paginas: int = 0
    paginas_ocr: tuple[int, ...] = ()
    tempo_ocr_segundos: float = 0.0
    acertos_cache_ocr: int = 0
    quantidade_janelas: int = 0
    quantidade_chamadas_llm: int = 0
    tokens_entrada_total: int = 0
    tokens_saida_total: int = 0
    memoria_antes_mb: float = 0.0
    pico_memoria_mb: float = 0.0
    memoria_depois_mb: float = 0.0
    tempo_leitura_segundos: float = 0.0
    tempo_inferencia_segundos: float = 0.0
    detalhe_erro: str = ""
    avisos: list[str] = field(default_factory=list)
    requer_confirmacao_semantica: bool = False
