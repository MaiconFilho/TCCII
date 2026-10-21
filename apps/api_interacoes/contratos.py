"""Contratos de resposta (Pydantic), publicados no OpenAPI."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from pipelines.identificacao_interacoes.modelos import CriterioVinculo, TipoAlvo


class EstadoAnalise(StrEnum):
    """Estado da análise da bula de um medicamento. Ausência nunca é negativa."""

    CONCLUIDO_COM_RELACOES = "CONCLUIDO_COM_RELACOES"
    CONCLUIDO_SEM_RELACOES = "CONCLUIDO_SEM_RELACOES"
    REVISAO_NECESSARIA = "REVISAO_NECESSARIA"
    EM_PROCESSAMENTO = "EM_PROCESSAMENTO"
    NAO_PROCESSADO = "NAO_PROCESSADO"
    DESATUALIZADO = "DESATUALIZADO"
    RETIRADO = "RETIRADO"
    FALHA = "FALHA"
    FONTE_INCOERENTE = "FONTE_INCOERENTE"
    SEM_TOPICO_INTERACOES = "SEM_TOPICO_INTERACOES"
    EXTRACAO_INDISPONIVEL = "EXTRACAO_INDISPONIVEL"
    SEM_BULA = "SEM_BULA"


class Sentido(StrEnum):
    DESCRITA_NA_BULA_PESQUISADA = "DESCRITA_NA_BULA_PESQUISADA"
    DESCRITA_EM_OUTRA_BULA = "DESCRITA_EM_OUTRA_BULA"


class SentidoPar(StrEnum):
    BULA_A_CITA_B = "BULA_A_CITA_B"
    BULA_B_CITA_A = "BULA_B_CITA_A"


class Erro(BaseModel):
    erro: str = Field(description="Código estável do erro.")
    mensagem: str = Field(description="Mensagem para o usuário, sem detalhes internos.")
    detalhes: list[dict[str, str]] = Field(default_factory=list)


class Saude(BaseModel):
    status: Literal["ok", "degradado"]
    banco: Literal["ok", "indisponivel"]
    esquema: Literal["ok", "ausente", "desconhecido"]
    versao_api: str


class Sugestao(BaseModel):
    nome_normalizado: str = Field(description="Identificador a usar nas consultas.")
    nome_exibicao: str = Field(description="Nome para exibição (bulas.nome_anvisa).")


class RespostaSugestoes(BaseModel):
    termo: str
    sugestoes: list[Sugestao]


class Medicamento(BaseModel):
    nome_normalizado: str
    nome_exibicao: str
    principios_ativos: list[str] | None = Field(
        default=None,
        description=(
            "Princípios ativos do cadastro (bulas.principios_ativos), na ordem gravada. Preenchido "
            "para os medicamentos consultados; nulo nos demais contextos (origens, relacionados)."
        ),
    )


class MedicamentoRelacionado(Medicamento):
    criterio_vinculo: CriterioVinculo


class FonteBula(BaseModel):
    """Bula efetivamente analisada (cópia guardada no processamento)."""

    medicamento: Medicamento
    numero_registro: str | None
    expediente: str | None
    sha256_pdf: str | None
    data_publicacao_bula: datetime | None = Field(
        description="Publicação da bula no Bulário; não é a data do processamento."
    )
    url_pdf: str | None = Field(
        default=None,
        description=(
            "Sempre nulo nesta versão: o identificador gravado do PDF é um token temporário, "
            "e um link genérico do Bulário não é o PDF específico."
        ),
    )


class Processamento(BaseModel):
    id: int
    estado: EstadoAnalise
    data_processamento: datetime
    versao_processamento: str
    modelo: str


class Relacao(BaseModel):
    id: int
    sentido: Sentido
    alvo: str
    tipo_alvo: TipoAlvo
    resumo: str
    resumo_card: str | None = None
    evidencia: str = Field(description="Trecho literal de bulas_interacoes.trecho_interacoes.")
    gravidade: Literal["desconhecida"] = Field(
        default="desconhecida", description="Não há classificação fundamentada de gravidade."
    )
    fonte: FonteBula
    processamento: Processamento
    medicamentos_relacionados: list[MedicamentoRelacionado]
    total_medicamentos_relacionados: int


class AnaliseBula(BaseModel):
    estado: EstadoAnalise
    mensagem: str
    em_processamento: bool = False
    processamento: Processamento | None = None
    fonte: FonteBula | None = None


class Cobertura(BaseModel):
    bulas_elegiveis: int = Field(description="Extrações CONCLUIDO com texto.")
    bulas_analisadas: int = Field(description="Com processamento concluído e vigente.")
    bulas_em_revisao: int
    corpus_completo: bool
    lista_exaustiva: bool = Field(
        description="Verdadeiro só quando todo o corpus está analisado sem revisões pendentes."
    )
    observacao: str


class Paginacao(BaseModel):
    pagina: int
    tamanho_pagina: int
    total: int
    total_paginas: int


class ResumoGeral(BaseModel):
    status: Literal["CONCLUIDO", "PENDENTE"] = "PENDENTE"
    texto: str | None = None
    gerado_em: datetime | None = None
    processamento_id: int | None = None


class RespostaInteracoes(BaseModel):
    medicamento: Medicamento
    analise_bula: AnaliseBula
    interacao_identificada: bool | None = Field(
        description=(
            "true: interação identificada nas fontes analisadas; false: análise concluída sem "
            "interação identificada; null: análise incompleta, sem conclusão."
        )
    )
    mensagem: str
    cobertura: Cobertura
    relacoes: list[Relacao]
    total_interacoes: int
    resumo_geral: ResumoGeral
    paginacao: Paginacao


class ItemPar(BaseModel):
    interacao_id: int
    alvo: str
    tipo_alvo: TipoAlvo
    resumo: str
    resumo_card: str | None = None
    criterio_vinculo: CriterioVinculo | None = None
    fundamento_vinculo: str | None = Field(
        default=None,
        description=(
            "Texto do cadastro do outro medicamento que fundamentou o vínculo: o princípio ativo "
            "(\"paracetamol\" em \"Paracetamol + Cloridrato de Pseudoefedrina\"), o nome ou a "
            "passagem da composição. Nulo quando não há vínculo."
        ),
    )


class EvidenciaPar(BaseModel):
    sentido: SentidoPar
    evidencia: str
    gravidade: Literal["desconhecida"] = "desconhecida"
    fonte: FonteBula
    processamento: Processamento
    itens: list[ItemPar] = Field(description="Alvos e resumos apoiados por esta mesma evidência.")


class RespostaPar(BaseModel):
    medicamento_a: Medicamento
    medicamento_b: Medicamento
    analise_a: AnaliseBula
    analise_b: AnaliseBula
    interacao_identificada: bool | None
    mensagem: str
    cobertura_completa: bool = Field(description="As duas bulas foram analisadas sem revisão pendente.")
    evidencias: list[EvidenciaPar] = Field(
        description="Relações vinculadas explicitamente ao outro medicamento, nos dois sentidos."
    )
    alertas_nao_confirmados: list[EvidenciaPar] = Field(
        description=(
            "Alertas sobre classes nas duas bulas. Não confirmam interação com o outro "
            "medicamento: a API não classifica medicamentos em classes."
        )
    )
    mencoes_nao_vinculadas: list[EvidenciaPar] = Field(
        description=(
            "Substâncias ou medicamentos citados por uma bula cujo nome está contido nos nomes "
            "do outro medicamento, mas sem correspondência exata (ex.: 'varfarina' e 'varfarina "
            "sódica'). Não são vínculos nem equivalências; impedem a conclusão negativa."
        )
    )
