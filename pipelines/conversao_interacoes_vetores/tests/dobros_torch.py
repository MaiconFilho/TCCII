"""Torch e Transformers falsos: os testes não carregam pesos nem usam GPU.

Implementa somente as operações usadas por
``ProvedorEmbeddingsTransformers._codificar_lote``: mean pooling mascarado,
normalização L2 e conversão para lista.
"""

from __future__ import annotations

import math
from contextlib import contextmanager


class TensorFalso:
    def __init__(self, dados) -> None:
        self.dados = dados

    # ------------------------------------------------------------- utilidades

    @property
    def dtype(self) -> str:
        return "float32"

    def to(self, _destino):
        return self

    def detach(self):
        return self

    def tolist(self):
        return self.dados

    # -------------------------------------------------------------- operações

    def unsqueeze(self, dimensao):
        if dimensao != -1:
            raise NotImplementedError("Somente unsqueeze(-1) é usado.")
        return TensorFalso([[[valor] for valor in linha] for linha in self.dados])

    def __mul__(self, outro):
        # (B, T, H) * (B, T, 1) -> (B, T, H)
        resultado = []
        for lote_a, lote_b in zip(self.dados, outro.dados):
            linhas = []
            for posicao_a, posicao_b in zip(lote_a, lote_b):
                fator = posicao_b[0]
                linhas.append([valor * fator for valor in posicao_a])
            resultado.append(linhas)
        return TensorFalso(resultado)

    def sum(self, dim):
        if dim != 1:
            raise NotImplementedError("Somente sum(dim=1) é usado.")
        resultado = []
        for lote in self.dados:
            largura = len(lote[0])
            acumulado = [0.0] * largura
            for posicao in lote:
                for indice, valor in enumerate(posicao):
                    acumulado[indice] += valor
            resultado.append(acumulado)
        return TensorFalso(resultado)

    def clamp(self, min):  # noqa: A002 - assinatura espelha a do torch
        return TensorFalso(
            [[max(valor, min) for valor in linha] for linha in self.dados]
        )

    def __truediv__(self, outro):
        # (B, H) / (B, 1) -> (B, H)
        resultado = []
        for linha, divisor in zip(self.dados, outro.dados):
            fator = divisor[0]
            resultado.append([valor / fator for valor in linha])
        return TensorFalso(resultado)


def _normalize(tensor: TensorFalso, p: int = 2, dim: int = 1) -> TensorFalso:
    if p != 2 or dim != 1:
        raise NotImplementedError("Somente normalize(p=2, dim=1) é usado.")
    resultado = []
    for linha in tensor.dados:
        norma = math.sqrt(sum(valor * valor for valor in linha)) or 1e-12
        resultado.append([valor / norma for valor in linha])
    return TensorFalso(resultado)


class _Functional:
    normalize = staticmethod(_normalize)


class _Nn:
    functional = _Functional()


class TorchFalso:
    nn = _Nn()

    def __init__(self) -> None:
        self.threads = None

    def set_num_threads(self, quantidade: int) -> None:
        self.threads = quantidade

    @contextmanager
    def inference_mode(self):
        yield


class ConfigFalsa:
    def __init__(self, hidden_size: int, max_position_embeddings: int = 512) -> None:
        self.hidden_size = hidden_size
        self.max_position_embeddings = max_position_embeddings


class TokenizerFalso:
    """Um token por palavra; ids são o comprimento de cada palavra."""

    model_max_length = 512

    def __init__(self) -> None:
        self.chamadas: list[list[str]] = []

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        return 2

    def encode(self, texto: str, add_special_tokens: bool = True, truncation=False):
        return [len(palavra) for palavra in texto.split()]

    def decode(self, tokens, skip_special_tokens: bool = True) -> str:
        return " ".join("p" * int(token) for token in tokens)

    def __call__(self, textos, padding=True, truncation=True, max_length=512,
                 return_tensors="pt"):
        self.chamadas.append(list(textos))
        sequencias = [self.encode(texto, add_special_tokens=False)[:max_length]
                      for texto in textos]
        maior = max((len(seq) for seq in sequencias), default=1) or 1
        ids = [seq + [0] * (maior - len(seq)) for seq in sequencias]
        mascara = [[1.0] * len(seq) + [0.0] * (maior - len(seq)) for seq in sequencias]
        return {
            "input_ids": TensorFalso(ids),
            "attention_mask": TensorFalso(mascara),
        }


class SaidaFalsa:
    def __init__(self, last_hidden_state: TensorFalso) -> None:
        self.last_hidden_state = last_hidden_state


class ModeloFalso:
    """Devolve, para cada token, um vetor constante derivado do seu id."""

    def __init__(self, hidden_size: int = 4, valores_fixos=None) -> None:
        self.config = ConfigFalsa(hidden_size)
        self.hidden_size = hidden_size
        self.valores_fixos = valores_fixos
        self.dispositivo = None
        self.em_avaliacao = False
        self.chamadas = 0
        self.carregamentos = 0

    def eval(self):
        self.em_avaliacao = True
        return self

    def to(self, dispositivo):
        self.dispositivo = dispositivo
        return self

    def __call__(self, **entradas):
        self.chamadas += 1
        ids = entradas["input_ids"].dados
        ocultos = []
        for posicao, sequencia in enumerate(ids):
            if self.valores_fixos is not None:
                base = list(self.valores_fixos[posicao % len(self.valores_fixos)])
            else:
                base = None
            linhas = []
            for token in sequencia:
                if base is not None:
                    linhas.append(list(base))
                else:
                    linhas.append(
                        [float(token + indice) for indice in range(self.hidden_size)]
                    )
            ocultos.append(linhas)
        return SaidaFalsa(TensorFalso(ocultos))
