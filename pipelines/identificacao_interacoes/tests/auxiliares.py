"""Duplos de teste: nenhum modelo real, nenhuma GPU e nenhum PostgreSQL."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pipelines.identificacao_interacoes.modelos import ConfiguracaoIdentificacao, FonteInteracoes


@dataclass(frozen=True)
class RespostaFalsa:
    """Mesmos campos de pipelines.extracao_interacoes.modelos.RespostaLLM."""

    conteudo: str
    tokens_entrada: int = 10
    tokens_saida: int = 5
    truncada: bool = False


def configuracao(**alteracoes) -> ConfiguracaoIdentificacao:
    valores = {
        "tamanho_contexto": 8192,
        "tokens_janela": 1000,
        "sobreposicao_tokens": 100,
        "max_tokens_saida": 1000,
        "tentativas_correcao": 1,
        "max_divisoes_janela": 4,
        # Desligado por padrão nos testes; test_servico cobre a etapa à parte.
        "exemplos_de_classe": False,
        "releitura_de_listas": False,
        # Estes auxiliares exercitam o serviço com a LLM; o dicionário tem testes próprios.
        "estrategia": "atual",
    }
    valores.update(alteracoes)
    return ConfiguracaoIdentificacao(**valores)


def fonte(texto: str, **alteracoes) -> FonteInteracoes:
    valores = {
        "nome_normalizado": "produto teste",
        "nome_exibicao": "PRODUTO TESTE",
        "principios_ativos": "substancia teste",
        "trecho_interacoes": texto,
        "numero_registro": "100",
        "expediente": "200",
        "sha256_pdf": "abc",
    }
    valores.update(alteracoes)
    return FonteInteracoes(**valores)


def alvo(nome: str, tipo: str, resumo: str) -> dict:
    return {"alvo": nome, "tipo_alvo": tipo, "resumo": resumo}


def frases_da_janela(mensagens) -> list[tuple[str, str]]:
    """(id, texto) das frases numeradas enviadas ao modelo."""
    corpo = mensagens[3]["content"].split("<TEXTO>\n", 1)[1].split("\n</TEXTO>", 1)[0]
    saida = []
    for linha in corpo.splitlines():
        if linha.startswith("[F"):
            identificador, texto = linha[1:].split("] ", 1)
            saida.append((identificador, texto))
    return saida


def responder(regras: dict[str, list[dict]]):
    """Resposta por conteúdo: frases que contêm a chave recebem aqueles alvos."""

    def gerar(mensagens):
        itens = []
        for identificador, texto in frases_da_janela(mensagens):
            alvos = next((v for chave, v in regras.items() if chave in texto), [])
            itens.append({"id": identificador, "alvos": alvos})
        return json.dumps({"frases": itens}, ensure_ascii=False)

    return gerar


def corrigir(regras: dict[str, list[dict]]):
    """Resposta da rodada de correção: só as frases citadas no pedido."""

    def gerar(mensagens):
        pedido = mensagens[-1]["content"]
        itens = []
        for identificador, texto in frases_da_janela(mensagens):
            if f"frase {identificador}," in pedido:
                alvos = next((v for chave, v in regras.items() if chave in texto), [])
                itens.append({"id": identificador, "alvos": alvos})
        return json.dumps({"frases": itens}, ensure_ascii=False)

    return gerar


class ProvedorFalso:
    """Conta tokens por palavra e devolve respostas roteirizadas em ordem.

    Um item ``callable`` recebe as mensagens e devolve a resposta, o que
    permite responder conforme a janela recebida.
    """

    def __init__(self, respostas=None) -> None:
        self.respostas = list(respostas or [])
        self.chamadas: list[list[dict]] = []
        self.esquemas: list = []

    def contar_tokens(self, texto: str) -> int:
        return len(texto.split())

    def analisar(self, mensagens, *, esquema=None):
        self.chamadas.append(list(mensagens))
        self.esquemas.append(esquema)
        if not self.respostas:
            raise AssertionError("Chamada inesperada ao provedor falso.")
        item = self.respostas.pop(0)
        if callable(item):
            item = item(mensagens)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, RespostaFalsa):
            return item
        return RespostaFalsa(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))

    def testar(self) -> None:
        return None


class MedidorFalso:
    def __init__(self, _limite: int) -> None:
        self.memoria_antes_mb = 100.0
        self.pico_mb = 120.0

    def executar(self, funcao):
        return funcao(), 0.01

    def ler_atual_mb(self) -> float:
        return self.pico_mb


def texto_da_janela(mensagens) -> str:
    return " ".join(texto for _, texto in frases_da_janela(mensagens))
