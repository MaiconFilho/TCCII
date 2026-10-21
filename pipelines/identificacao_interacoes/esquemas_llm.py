"""Esquema estruturado da resposta e sua validação item a item.

Formato: o texto da janela é apresentado em frases numeradas (``F1``, ``F2``...)
e o modelo devolve **exatamente um item por frase, na ordem**, com os alvos
que aquela frase relaciona ao medicamento de origem::

    {"frases": [{"id": "F1", "alvos": [{"alvo": ..., "tipo_alvo": ..., "resumo": ...}]},
                {"id": "F2", "alvos": []}]}

Um campo extra de âncora (as três primeiras palavras copiadas de cada frase)
foi testado para corrigir respostas deslocadas e reduziu a revocação do
modelo (ver o README); não faz parte do formato.

Por que assim (medido no piloto com Qwen2.5-1.5B, temperatura 0):

- com uma lista livre de relações, o modelo não encerrava a lista e repetia
  o mesmo item até o limite de tokens, com ou sem penalidade de repetição;
- a gramática gerada daqui fixa o tamanho da lista (``prefixItems`` e
  ``const``), então a resposta sempre termina, e limita os alvos por frase;
- a evidência é a frase indicada, recortada do texto original pelo
  identificador: não há cópia nem paráfrase da evidência pelo modelo.

A resposta é validada de novo pelo Pydantic **item a item**: um alvo com tipo
inexistente vira rejeição registrada, nunca é convertido em ``OUTRO``, e não
invalida os demais. JSON válido não é relação fiel: ``validacao`` continua
obrigatória.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .erros import RespostaInvalidaError
from .modelos import TipoAlvo


# Frases-lista de bulas reais chegam a ~50 fármacos; o teto limita a saída
# (cerca de 40 tokens por alvo) e, se atingido, marca revisão.
MAX_ALVOS_POR_FRASE = 40


class AlvoInformado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alvo: str = Field(min_length=1)
    tipo_alvo: TipoAlvo
    resumo: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ItemInvalido:
    id_frase: str
    alvo: str
    tipo_alvo: str
    resumo: str
    detalhe: str


@dataclass(frozen=True, slots=True)
class FraseInformada:
    id_frase: str
    alvos: tuple[AlvoInformado, ...]
    quantidade_informada: int

    @property
    def no_limite(self) -> bool:
        return self.quantidade_informada >= MAX_ALVOS_POR_FRASE


@dataclass(frozen=True, slots=True)
class RespostaIdentificacao:
    frases: tuple[FraseInformada, ...]
    invalidos: tuple[ItemInvalido, ...] = ()


def _esquema_alvos() -> dict:
    return {
        "type": "array",
        "maxItems": MAX_ALVOS_POR_FRASE,
        "items": {
            "type": "object",
            "properties": {
                "alvo": {"type": "string", "minLength": 1},
                "tipo_alvo": {"enum": [tipo.value for tipo in TipoAlvo]},
                "resumo": {"type": "string", "minLength": 1},
            },
            "required": ["alvo", "tipo_alvo", "resumo"],
            "additionalProperties": False,
        },
    }


def esquema_janela(ids: Sequence[str]) -> dict:
    """Um item por frase, na ordem dada; a gramática fecha a lista sozinha."""
    return {
        "type": "object",
        "properties": {
            "frases": {
                "type": "array",
                "prefixItems": [
                    {
                        "type": "object",
                        "properties": {"id": {"const": identificador}, "alvos": _esquema_alvos()},
                        "required": ["id", "alvos"],
                        "additionalProperties": False,
                    }
                    for identificador in ids
                ],
            }
        },
        "required": ["frases"],
        "additionalProperties": False,
    }


def esquema_correcao(ids: Sequence[str], maximo: int) -> dict:
    """Correções apenas das frases citadas, no máximo ``maximo`` itens."""
    return {
        "type": "object",
        "properties": {
            "frases": {
                "type": "array",
                "maxItems": max(1, maximo),
                "items": {
                    "type": "object",
                    "properties": {"id": {"enum": list(ids)}, "alvos": _esquema_alvos()},
                    "required": ["id", "alvos"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["frases"],
        "additionalProperties": False,
    }


# Uma pergunta por frase precisa caber numa frase-lista inteira (~50 nomes no
# Marevan); com 20, o fim da lista se perdia. Atingir o teto marca revisão.
MAX_EXEMPLOS_DE_CLASSE = 60
TIPOS_DE_EXEMPLO = (TipoAlvo.PRINCIPIO_ATIVO, TipoAlvo.MEDICAMENTO, TipoAlvo.OUTRA_SUBSTANCIA)


def esquema_exemplos() -> dict:
    """Integrantes de uma classe citados na frase (lista limitada)."""
    return {
        "type": "object",
        "properties": {
            "exemplos": {
                "type": "array",
                "maxItems": MAX_EXEMPLOS_DE_CLASSE,
                "items": {
                    "type": "object",
                    "properties": {
                        "nome": {"type": "string", "minLength": 1},
                        "tipo_alvo": {"enum": [tipo.value for tipo in TIPOS_DE_EXEMPLO]},
                    },
                    "required": ["nome", "tipo_alvo"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["exemplos"],
        "additionalProperties": False,
    }


def interpretar_exemplos(conteudo: str) -> list[tuple[str, TipoAlvo]]:
    try:
        dados = json.loads(_remover_cerca(conteudo))
    except json.JSONDecodeError as erro:
        raise RespostaInvalidaError(f"JSON inválido: {erro.msg}.") from erro
    if not isinstance(dados, dict) or not isinstance(dados.get("exemplos"), list):
        raise RespostaInvalidaError('A resposta deve trazer a lista "exemplos".')
    exemplos = []
    for item in dados["exemplos"]:
        if not isinstance(item, dict) or not isinstance(item.get("nome"), str):
            continue
        try:
            tipo = TipoAlvo(item.get("tipo_alvo"))
        except ValueError:
            continue
        if tipo in TIPOS_DE_EXEMPLO and item["nome"].strip():
            exemplos.append((item["nome"].strip(), tipo))
    return exemplos


def _remover_cerca(conteudo: str) -> str:
    texto = conteudo.strip()
    if texto.startswith("```"):
        linhas = texto.splitlines()
        if len(linhas) >= 3 and linhas[-1].strip().startswith("```"):
            return "\n".join(linhas[1:-1]).strip()
    return texto


def interpretar_resposta(
    conteudo: str, ids_validos: Sequence[str], *, exigir_todos: bool = True
) -> RespostaIdentificacao:
    """JSON → frases e alvos válidos; itens fora do esquema viram ``invalidos``.

    A estrutura externa inválida (JSON quebrado, lista ausente, frase
    desconhecida ou faltando) invalida a resposta inteira.
    """
    try:
        dados = json.loads(_remover_cerca(conteudo))
    except json.JSONDecodeError as erro:
        raise RespostaInvalidaError(f"JSON inválido: {erro.msg}.") from erro
    if not isinstance(dados, dict) or not isinstance(dados.get("frases"), list):
        raise RespostaInvalidaError('A resposta deve ser um objeto com a lista "frases".')
    if set(dados) - {"frases"}:
        raise RespostaInvalidaError("A resposta tem campos inesperados.")

    validos = set(ids_validos)
    vistos: list[str] = []
    frases: list[FraseInformada] = []
    invalidos: list[ItemInvalido] = []
    for bruto in dados["frases"]:
        if not isinstance(bruto, dict) or bruto.get("id") not in validos:
            raise RespostaInvalidaError("Item de frase ausente ou com identificador desconhecido.")
        identificador = bruto["id"]
        alvos = bruto.get("alvos")
        if not isinstance(alvos, list):
            raise RespostaInvalidaError(f'A frase {identificador} não tem a lista "alvos".')
        vistos.append(identificador)
        aceitos: list[AlvoInformado] = []
        for alvo in alvos:
            if not isinstance(alvo, dict):
                invalidos.append(ItemInvalido(identificador, "", "", "", "Alvo não é um objeto."))
                continue
            try:
                aceitos.append(AlvoInformado.model_validate(alvo))
            except ValidationError as erro:
                primeiro = erro.errors(include_url=False)[0]
                campo = ".".join(str(p) for p in primeiro.get("loc", ())) or "alvo"
                invalidos.append(
                    ItemInvalido(
                        id_frase=identificador,
                        alvo=str(alvo.get("alvo", ""))[:150],
                        tipo_alvo=str(alvo.get("tipo_alvo", ""))[:60],
                        resumo=str(alvo.get("resumo", ""))[:300],
                        detalhe=f"Campo {campo} inválido: {primeiro['msg']}.",
                    )
                )
        frases.append(FraseInformada(identificador, tuple(aceitos), len(alvos)))
    if exigir_todos and vistos != list(ids_validos):
        raise RespostaInvalidaError("A resposta não traz exatamente uma entrada por frase, na ordem.")
    return RespostaIdentificacao(tuple(frases), tuple(invalidos))
