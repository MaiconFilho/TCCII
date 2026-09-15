"""Esquemas de geracao; validadores semanticos continuam obrigatorios."""

from copy import deepcopy

from pydantic import BaseModel

from .modelos import RespostaClassificacao, RespostaLimite, TipoOcorrencia


def esquema_para_geracao(modelo: type[BaseModel]) -> dict:
    base = modelo.model_json_schema()
    base["required"] = list(base["properties"])
    linha = {"type": "string", "pattern": "^L[0-9]{6}$"}
    texto = {"type": "string", "minLength": 1}
    nulo = {"type": "null"}

    def variante(**campos: dict) -> dict:
        ramo = deepcopy(base)
        ramo.pop("$defs", None)
        ramo["properties"].update(campos)
        return ramo

    # model_validator nao vira JSON Schema automaticamente. Estes ramos
    # representam a mesma coerencia durante a geracao dos tokens.
    if modelo is RespostaClassificacao:
        ramos = [
            variante(
                tipo_ocorrencia={"const": "SECAO_CORPO"},
                linha_titulo=linha,
                titulo_encontrado=texto,
            ),
            variante(
                tipo_ocorrencia={
                    "enum": [t.value for t in TipoOcorrencia if t != TipoOcorrencia.SECAO_CORPO]
                },
                linha_titulo=nulo,
                titulo_encontrado=nulo,
            ),
        ]
    elif modelo is RespostaLimite:
        ramos = [
            variante(
                encontrou_fim={"const": True},
                linha_inicio=linha,
                linha_fim_exclusiva=linha,
                proximo_titulo=texto,
                fim_documento={"const": False},
            ),
            variante(
                encontrou_fim={"const": False},
                linha_inicio=linha,
                linha_fim_exclusiva=nulo,
                proximo_titulo=nulo,
                fim_documento={"const": False},
            ),
            variante(
                encontrou_fim={"const": True},
                linha_inicio=linha,
                linha_fim_exclusiva=nulo,
                proximo_titulo=nulo,
                fim_documento={"const": True},
            ),
        ]
    else:
        return base
    return {"$defs": base.get("$defs", {}), "anyOf": ramos}
