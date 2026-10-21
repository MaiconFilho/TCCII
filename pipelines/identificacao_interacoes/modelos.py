"""Modelos de domínio da identificação das interações.

Usa somente a biblioteca padrão: a API de consulta importa estas definições
sem carregar bibliotecas de inferência, PDF ou banco.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TipoAlvo(StrEnum):
    """Categoria do alvo citado no tópico de interações."""

    PRINCIPIO_ATIVO = "PRINCIPIO_ATIVO"
    MEDICAMENTO = "MEDICAMENTO"
    CLASSE_FARMACOLOGICA = "CLASSE_FARMACOLOGICA"
    ALIMENTO = "ALIMENTO"
    GRUPO_ALIMENTAR = "GRUPO_ALIMENTAR"
    OUTRA_SUBSTANCIA = "OUTRA_SUBSTANCIA"
    EXAME_LABORATORIAL = "EXAME_LABORATORIAL"
    OUTRO = "OUTRO"


# Somente estes tipos procuram medicamentos no cadastro. Classes, alimentos e
# demais alvos permanecem textuais, sem expansão.
TIPOS_VINCULAVEIS = frozenset({TipoAlvo.PRINCIPIO_ATIVO, TipoAlvo.MEDICAMENTO})


class StatusProcessamento(StrEnum):
    """Estado persistido em processamentos_interacoes."""

    CONCLUIDO_COM_RELACOES = "CONCLUIDO_COM_RELACOES"
    CONCLUIDO_SEM_RELACOES = "CONCLUIDO_SEM_RELACOES"
    REVISAO_NECESSARIA = "REVISAO_NECESSARIA"
    FALHA = "FALHA"


# Processamentos que percorreram todas as janelas. Conclusão operacional não
# garante que o modelo encontrou todas as relações corretamente.
STATUS_CONCLUIDOS = frozenset(
    {
        StatusProcessamento.CONCLUIDO_COM_RELACOES,
        StatusProcessamento.CONCLUIDO_SEM_RELACOES,
        StatusProcessamento.REVISAO_NECESSARIA,
    }
)


class StatusExecucao(StrEnum):
    """Resultado de cada medicamento no relatório da CLI."""

    CONCLUIDO_COM_RELACOES = "CONCLUIDO_COM_RELACOES"
    CONCLUIDO_SEM_RELACOES = "CONCLUIDO_SEM_RELACOES"
    REVISAO_NECESSARIA = "REVISAO_NECESSARIA"
    FALHA = "FALHA"
    IGNORADO_JA_PROCESSADO = "IGNORADO_JA_PROCESSADO"
    EM_ANDAMENTO_OUTRO_PROCESSO = "EM_ANDAMENTO_OUTRO_PROCESSO"
    FONTE_INCOERENTE = "FONTE_INCOERENTE"
    FONTE_ALTERADA = "FONTE_ALTERADA"
    ERRO_BANCO = "ERRO_BANCO"
    VINCULOS_ATUALIZADOS = "VINCULOS_ATUALIZADOS"


STATUS_EXECUCAO_COM_ERRO = frozenset(
    {
        StatusExecucao.FALHA,
        StatusExecucao.FONTE_INCOERENTE,
        StatusExecucao.FONTE_ALTERADA,
        StatusExecucao.ERRO_BANCO,
    }
)


class CriterioVinculo(StrEnum):
    """Regra determinística que ligou o alvo a um medicamento do cadastro."""

    PRINCIPIO_ATIVO_EXATO = "PRINCIPIO_ATIVO_EXATO"
    NOME_MEDICAMENTO_EXATO = "NOME_MEDICAMENTO_EXATO"
    COMPOSICAO_EQUIVALENCIA_DECLARADA = "COMPOSICAO_EQUIVALENCIA_DECLARADA"
    # "varfarina" citada no texto e "varfarina sódica" no cadastro (migração 003).
    PRINCIPIO_ATIVO_SEM_SAL = "PRINCIPIO_ATIVO_SEM_SAL"


class MotivoRejeicao(StrEnum):
    """Por que uma relação proposta pelo modelo não foi aceita."""

    ITEM_FORA_DO_ESQUEMA = "ITEM_FORA_DO_ESQUEMA"
    ALVO_INVALIDO = "ALVO_INVALIDO"
    ALVO_GENERICO = "ALVO_GENERICO"
    ALVO_E_ORIGEM = "ALVO_E_ORIGEM"
    EVIDENCIA_NAO_ENCONTRADA = "EVIDENCIA_NAO_ENCONTRADA"
    ALVO_AUSENTE_NA_EVIDENCIA = "ALVO_AUSENTE_NA_EVIDENCIA"
    RELACAO_NAO_POSITIVA = "RELACAO_NAO_POSITIVA"
    RESUMO_OMITE_NEGACAO = "RESUMO_OMITE_NEGACAO"
    RESUMO_OMITE_QUANTIDADE = "RESUMO_OMITE_QUANTIDADE"
    RESUMO_OMITE_VIA = "RESUMO_OMITE_VIA"
    RESUMO_INVENTA_GRAVIDADE = "RESUMO_INVENTA_GRAVIDADE"
    RESUMO_INVENTA_EFEITO = "RESUMO_INVENTA_EFEITO"
    RESUMO_SEM_APOIO_NA_EVIDENCIA = "RESUMO_SEM_APOIO_NA_EVIDENCIA"
    LISTA_DE_ALVOS_NO_LIMITE = "LISTA_DE_ALVOS_NO_LIMITE"


# Rejeições que podem esconder uma relação verdadeira mal formulada pelo
# modelo: se não forem resolvidas na correção, o resultado pede revisão.
MOTIVOS_CORRIGIVEIS = frozenset(
    {
        MotivoRejeicao.ITEM_FORA_DO_ESQUEMA,
        MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA,
        MotivoRejeicao.ALVO_AUSENTE_NA_EVIDENCIA,
        MotivoRejeicao.RESUMO_OMITE_NEGACAO,
        MotivoRejeicao.RESUMO_OMITE_QUANTIDADE,
        MotivoRejeicao.RESUMO_OMITE_VIA,
        MotivoRejeicao.RESUMO_INVENTA_GRAVIDADE,
        MotivoRejeicao.RESUMO_INVENTA_EFEITO,
        MotivoRejeicao.RESUMO_SEM_APOIO_NA_EVIDENCIA,
    }
)

# Pendências que impedem marcar o resultado como completo, se não resolvidas.
MOTIVOS_QUE_EXIGEM_REVISAO = MOTIVOS_CORRIGIVEIS | {MotivoRejeicao.LISTA_DE_ALVOS_NO_LIMITE}


ESTRATEGIAS = ("dicionario", "atual", "duas_etapas")


@dataclass(frozen=True, slots=True)
class ConfiguracaoIdentificacao:
    """Parâmetros lidos do .env; independentes dos limites da extração."""

    repositorio_modelo: str = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
    arquivo_modelo: str = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    tamanho_contexto: int = 8192
    tokens_janela: int = 1000
    sobreposicao_tokens: int = 150
    max_tokens_saida: int = 3072
    threads: int = 0
    tamanho_lote: int = 256
    camadas_gpu: int = 0
    temperatura: float = 0.0
    limite_memoria_mb: int = 5500
    concorrencia: int = 1
    tentativas_correcao: int = 1
    max_divisoes_janela: int = 4
    max_caracteres_resumo: int = 300
    exemplos_de_classe: bool = True
    releitura_de_listas: bool = True
    # dicionario: nomes do banco citados no tópico, sem modelo (padrão).
    # atual e duas_etapas: identificação com a LLM local.
    estrategia: str = "dicionario"
    lote_resumos: int = 6

    @property
    def usa_modelo(self) -> bool:
        return self.estrategia != "dicionario"

    def validar(self) -> None:
        if self.estrategia not in ESTRATEGIAS:
            raise ValueError("Estratégia deve ser " + ", ".join(ESTRATEGIAS) + ".")
        if not 1 <= self.lote_resumos <= 12:
            raise ValueError("Lote de resumos deve estar entre 1 e 12.")
        if self.tentativas_correcao > 2:
            raise ValueError("Use no máximo duas correções por caso/lote.")
        if not self.repositorio_modelo.strip() or not self.arquivo_modelo.strip():
            raise ValueError("O repositório e o arquivo GGUF devem ser informados.")
        if self.tamanho_contexto <= 0 or self.tokens_janela <= 0:
            raise ValueError("Contexto e janela devem ser positivos.")
        if self.sobreposicao_tokens < 0:
            raise ValueError("IDENTIFICACAO_JANELA_SOBREPOSICAO não pode ser negativa.")
        if self.sobreposicao_tokens >= self.tokens_janela:
            raise ValueError(
                "IDENTIFICACAO_JANELA_SOBREPOSICAO deve ser menor que "
                "IDENTIFICACAO_JANELA_TOKENS."
            )
        if self.max_tokens_saida <= 0 or self.tamanho_lote <= 0:
            raise ValueError("Saída e lote devem ser positivos.")
        if self.tokens_janela + self.max_tokens_saida >= self.tamanho_contexto:
            raise ValueError(
                "IDENTIFICACAO_JANELA_TOKENS somado a "
                "IDENTIFICACAO_LLM_MAX_OUTPUT_TOKENS precisa caber em "
                "IDENTIFICACAO_LLM_CONTEXT_SIZE, com folga para as instruções."
            )
        if self.threads < 0 or self.camadas_gpu < 0:
            raise ValueError("Threads e camadas de GPU não podem ser negativos.")
        if self.temperatura < 0:
            raise ValueError("A temperatura não pode ser negativa.")
        if self.limite_memoria_mb < 0:
            raise ValueError("O limite de memória não pode ser negativo.")
        if self.concorrencia != 1:
            raise ValueError("A inferência deve ser serial (concorrência 1).")
        if self.tentativas_correcao < 0 or self.max_divisoes_janela < 0:
            raise ValueError("Correções e divisões não podem ser negativas.")
        if self.max_caracteres_resumo < 40:
            raise ValueError("O resumo precisa aceitar ao menos 40 caracteres.")


def calcular_hash_trecho(texto: str) -> str:
    """SHA-256 do texto em UTF-8; igual a sha256(convert_to(texto, 'UTF8'))."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def chave_bloqueio(nome_normalizado: str) -> int:
    """Chave bigint estável do pg_advisory_lock de um medicamento."""
    material = f"identificacao_interacoes\x1f{nome_normalizado}".encode("utf-8")
    valor = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    # Mantém a chave positiva no intervalo de bigint.
    return valor & 0x7FFF_FFFF_FFFF_FFFF


def partes_chave_bloqueio(chave: int) -> tuple[int, int]:
    """(classid, objid) com que o pg_locks expõe uma chave bigint."""
    return chave >> 32, chave & 0xFFFF_FFFF


@dataclass(frozen=True, slots=True)
class FonteInteracoes:
    """Registro selecionado no banco; a origem nunca vem do modelo."""

    nome_normalizado: str
    nome_exibicao: str
    principios_ativos: str | None
    trecho_interacoes: str
    numero_registro: str | None
    expediente: str | None
    sha256_pdf: str | None = None
    id_produto: str | None = None
    data_publicacao_anvisa: datetime | None = None
    hash_trecho_banco: str | None = None
    identificadores_coerentes: bool = True

    @property
    def hash_trecho(self) -> str:
        return calcular_hash_trecho(self.trecho_interacoes)


@dataclass(frozen=True, slots=True)
class RelacaoIdentificada:
    """Relação aceita: evidência é a passagem literal do trecho original."""

    alvo: str
    tipo_alvo: TipoAlvo
    resumo: str
    evidencia: str
    inicio_evidencia: int
    fim_evidencia: int
    indice_janela: int


@dataclass(frozen=True, slots=True)
class Rejeicao:
    motivo: MotivoRejeicao
    alvo: str
    tipo_alvo: str
    resumo: str
    evidencia_informada: str
    detalhe: str
    indice_janela: int

    @property
    def corrigivel(self) -> bool:
        return self.motivo in MOTIVOS_CORRIGIVEIS

    @property
    def exige_revisao(self) -> bool:
        return self.motivo in MOTIVOS_QUE_EXIGEM_REVISAO


@dataclass(frozen=True, slots=True)
class Vinculo:
    medicamento_relacionado: str
    criterio: CriterioVinculo
    fundamento: str


@dataclass(slots=True)
class ResultadoIdentificacao:
    """Retorno do serviço, ainda sem gravação no banco."""

    status: StatusProcessamento
    nome_normalizado: str = ""
    relacoes: list[RelacaoIdentificada] = field(default_factory=list)
    rejeicoes: list[Rejeicao] = field(default_factory=list)
    rejeicoes_pendentes: list[Rejeicao] = field(default_factory=list)
    quantidade_janelas: int = 0
    janelas_concluidas: int = 0
    divisoes_por_limite: int = 0
    releituras: int = 0
    chamadas_llm: int = 0
    tokens_entrada_total: int = 0
    tokens_saida_total: int = 0
    duplicatas_consolidadas: int = 0
    tempo_inferencia_segundos: float = 0.0
    memoria_antes_mb: float = 0.0
    pico_memoria_mb: float = 0.0
    memoria_depois_mb: float = 0.0
    detalhe_erro: str = ""
    avisos: list[str] = field(default_factory=list)
    metricas_chamadas: list[dict] = field(default_factory=list)
    rejeicoes_por_etapa: dict[str, dict[str, int]] = field(default_factory=dict)
    propostas_brutas: int = 0
    aceitas_antes_consolidacao: int = 0
    resumos_gerados: int = 0
    resumos_aceitos: int = 0
    resumos_rejeitados: int = 0
