from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class StatusEmbedding(StrEnum):
    CONCLUIDO = "CONCLUIDO"
    IGNORADO_JA_EXISTENTE = "IGNORADO_JA_EXISTENTE"
    REPROCESSADO = "REPROCESSADO"
    SEM_TEXTO = "SEM_TEXTO"
    MODELO_INVALIDO = "MODELO_INVALIDO"
    DIMENSAO_INVALIDA = "DIMENSAO_INVALIDA"
    ERRO_CHUNKING = "ERRO_CHUNKING"
    ERRO_INFERENCIA = "ERRO_INFERENCIA"
    ERRO_BANCO = "ERRO_BANCO"
    LIMITE_MEMORIA = "LIMITE_MEMORIA"


@dataclass(frozen=True, slots=True)
class ConfiguracaoEmbeddings:
    """Parâmetros lidos do .env e sobrescritos pela CLI."""

    modelo_id: str = "intfloat/multilingual-e5-small"
    dispositivo: str = "cpu"
    tamanho_lote: int = 8
    max_tokens_entrada: int = 512
    normalizar: bool = True
    tamanho_chunk: int = 450
    sobreposicao_chunk: int = 60
    threads: int = 0
    limite_memoria_mb: int = 5500
    concorrencia: int = 1
    prefixo_passagem: str = "passage: "

    def validar(self) -> None:
        if not self.modelo_id.strip():
            raise ValueError("EMBEDDING_MODEL_ID não pode ficar vazio.")
        if self.dispositivo not in {"cpu", "cuda"}:
            raise ValueError("EMBEDDING_DEVICE deve ser cpu ou cuda.")
        if self.tamanho_lote <= 0:
            raise ValueError("EMBEDDING_BATCH_SIZE deve ser maior que zero.")
        if self.max_tokens_entrada <= 0:
            raise ValueError("EMBEDDING_MAX_INPUT_TOKENS deve ser maior que zero.")
        if self.tamanho_chunk <= 0:
            raise ValueError("EMBEDDING_CHUNK_SIZE deve ser maior que zero.")
        if self.sobreposicao_chunk < 0:
            raise ValueError("EMBEDDING_CHUNK_OVERLAP não pode ser negativo.")
        if self.sobreposicao_chunk >= self.tamanho_chunk:
            raise ValueError(
                "EMBEDDING_CHUNK_OVERLAP deve ser menor que EMBEDDING_CHUNK_SIZE."
            )
        if self.tamanho_chunk > self.max_tokens_entrada:
            raise ValueError(
                "EMBEDDING_CHUNK_SIZE não pode exceder EMBEDDING_MAX_INPUT_TOKENS."
            )
        if self.threads < 0:
            raise ValueError("EMBEDDING_THREADS não pode ser negativo.")
        if self.limite_memoria_mb < 0:
            raise ValueError("EMBEDDING_MAX_PROCESS_MEMORY_MB não pode ser negativo.")
        if self.concorrencia != 1:
            raise ValueError(
                "EMBEDDING_INFERENCE_CONCURRENCY deve ser 1 neste perfil de memória."
            )


@dataclass(frozen=True, slots=True)
class InteracaoParaVetorizar:
    """Linha de bulas_interacoes elegível para vetorização."""

    nome_normalizado: str
    trecho_interacoes: str | None


@dataclass(frozen=True, slots=True)
class ChunkTexto:
    indice: int
    texto: str
    quantidade_tokens: int


@dataclass(frozen=True, slots=True)
class EmbeddingChunk:
    chunk_index: int
    texto_chunk: str
    texto_hash: str
    quantidade_tokens: int
    vetor: tuple[float, ...]

    @property
    def dimensao(self) -> int:
        return len(self.vetor)


@dataclass(slots=True)
class ResultadoEmbedding:
    """Retorno de ServicoEmbeddings.gerar_para_interacao."""

    status: StatusEmbedding
    nome_normalizado: str = ""
    modelo: str = ""
    dimensao: int = 0
    chunks: list[EmbeddingChunk] = field(default_factory=list)
    quantidade_tokens: int = 0
    texto_hash: str = ""
    tempo_chunking_segundos: float = 0.0
    tempo_inferencia_segundos: float = 0.0
    memoria_antes_mb: float = 0.0
    pico_memoria_mb: float = 0.0
    memoria_depois_mb: float = 0.0
    detalhe_erro: str = ""

    @property
    def quantidade_chunks(self) -> int:
        return len(self.chunks)
