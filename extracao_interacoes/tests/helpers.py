import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.modelos import (
    ConfiguracaoLLM,
    DocumentoPdf,
    LinhaDocumento,
    RespostaLLM,
)
from extracao_interacoes.validacao import normalizar_linha


def criar_documento(textos: list[str], paginas: list[int] | None = None) -> DocumentoPdf:
    paginas = paginas or [1] * len(textos)
    linhas = tuple(
        LinhaDocumento(
            numero=indice,
            pagina=paginas[indice - 1],
            posicao_pagina=indice,
            texto_original=texto,
            texto_normalizado=normalizar_linha(texto),
        )
        for indice, texto in enumerate(textos, start=1)
    )
    return DocumentoPdf(
        linhas=linhas,
        quantidade_paginas=max(paginas, default=0),
        quantidade_caracteres=sum(len(texto) for texto in textos),
    )


def configuracao(**alteracoes) -> ConfiguracaoLLM:
    valores = {
        "backend": "llama_cpp",
        "modo_extracao": "completo",
        "repositorio_modelo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "arquivo_modelo": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "tamanho_contexto": 4096,
        "tokens_janela": 1800,
        "sobreposicao_tokens": 250,
        "max_tokens_saida": 256,
        "threads": 0,
        "tamanho_lote": 128,
        "camadas_gpu": 0,
        "temperatura": 0,
        "limite_memoria_mb": 5500,
        "concorrencia": 1,
    }
    valores.update(alteracoes)
    return ConfiguracaoLLM(**valores)


class ProvedorFalso:
    def __init__(self, respostas=None, tokens_por_palavra: bool = True) -> None:
        self.respostas = list(respostas or [])
        self.chamadas = []
        self.esquemas = []
        self.tokens_por_palavra = tokens_por_palavra
        self.smoke_executado = False

    def contar_tokens(self, texto: str) -> int:
        return len(texto.split()) if self.tokens_por_palavra else len(texto)

    def analisar(self, mensagens, *, esquema=None):
        self.chamadas.append(mensagens)
        self.esquemas.append(esquema)
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, RespostaLLM):
            return resposta
        if isinstance(resposta, dict):
            resposta = json.dumps(resposta, ensure_ascii=False)
        return RespostaLLM(str(resposta), 10, 5, False)

    def testar(self) -> None:
        self.smoke_executado = True


class MedidorFalso:
    def __init__(self, _limite: int) -> None:
        self.memoria_antes_mb = 100.0
        self.pico_mb = 120.0

    def executar(self, funcao):
        return funcao(), 0.01

    def ler_atual_mb(self):
        return self.pico_mb


def resposta_secao(linha="L000001", titulo="5. INTERAÇÕES MEDICAMENTOSAS"):
    return {
        "tipo_ocorrencia": "SECAO_CORPO",
        "linha_titulo": linha,
        "titulo_encontrado": titulo,
        "confianca": "ALTA",
    }


def resposta_nao_encontrado():
    return {
        "tipo_ocorrencia": "NAO_ENCONTRADO",
        "linha_titulo": None,
        "titulo_encontrado": None,
        "confianca": "ALTA",
    }


def resposta_fim(
    linha="L000004",
    titulo="6. ADVERTÊNCIAS",
    inicio="L000001",
    titulo_secao="5. INTERAÇÕES MEDICAMENTOSAS",
):
    return {
        "encontrou_fim": True,
        "linha_inicio": inicio,
        "linha_fim_exclusiva": linha,
        "titulo_encontrado": titulo_secao,
        "proximo_titulo": titulo,
        "fim_documento": False,
    }
