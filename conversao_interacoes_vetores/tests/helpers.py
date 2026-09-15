"""Duplos de teste: nenhum modelo real, nenhuma GPU, nenhum PostgreSQL."""

import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.modelos import ConfiguracaoEmbeddings


def configuracao(**alteracoes) -> ConfiguracaoEmbeddings:
    valores = {
        "modelo_id": "modelo/fake-small",
        "dispositivo": "cpu",
        "tamanho_lote": 2,
        "max_tokens_entrada": 512,
        "normalizar": True,
        "tamanho_chunk": 10,
        "sobreposicao_chunk": 2,
        "threads": 0,
        "limite_memoria_mb": 5500,
        "concorrencia": 1,
        "prefixo_passagem": "passage: ",
    }
    valores.update(alteracoes)
    return ConfiguracaoEmbeddings(**valores)


class ProvedorFalso:
    """Tokeniza por palavra e devolve vetores determinísticos e unitários."""

    def __init__(
        self,
        dimensao: int = 4,
        limite_tokens: int = 512,
        modelo: str = "modelo/fake-small",
        tamanho_lote: int = 2,
        vetores=None,
        erro=None,
    ) -> None:
        self._dimensao = dimensao
        self._limite_tokens = limite_tokens
        self._modelo = modelo
        self.tamanho_lote = tamanho_lote
        self.vetores = list(vetores) if vetores is not None else None
        self.erro = erro
        self.chamadas: list[list[str]] = []
        self.lotes: list[int] = []
        self.carregamentos = 0
        self.smoke_executado = False

    @property
    def identificador_modelo(self) -> str:
        return self._modelo

    @property
    def dimensao(self) -> int:
        return self._dimensao

    @property
    def limite_tokens(self) -> int:
        return self._limite_tokens

    def tokenizar(self, texto: str) -> list[int]:
        return [len(palavra) for palavra in texto.split()]

    def contar_tokens(self, texto: str) -> int:
        return len(self.tokenizar(texto))

    def destokenizar(self, tokens) -> str:
        # Reconstrói palavras artificiais com o comprimento de cada token.
        return " ".join("p" * int(token) for token in tokens)

    def codificar(self, textos: list[str]) -> list[list[float]]:
        self.chamadas.append(list(textos))
        if self.erro is not None:
            raise self.erro
        for inicio in range(0, len(textos), self.tamanho_lote):
            self.lotes.append(len(textos[inicio : inicio + self.tamanho_lote]))
        if self.vetores is not None:
            return [list(vetor) for vetor in self.vetores[: len(textos)]]
        vetores = []
        for posicao in range(len(textos)):
            vetor = [0.0] * self._dimensao
            vetor[posicao % self._dimensao] = 1.0
            vetores.append(vetor)
        return vetores

    def testar(self) -> None:
        self.smoke_executado = True


class MedidorFalso:
    def __init__(self, _limite: int = 5500) -> None:
        self.memoria_antes_mb = 100.0
        self.pico_mb = 120.0

    def verificar_antes_da_inferencia(self) -> float:
        return self.memoria_antes_mb

    def executar(self, funcao):
        return funcao(), 0.01

    def ler_atual_mb(self) -> float:
        return 105.0


class MedidorEstourado(MedidorFalso):
    """Simula o processo já acima do limite configurado."""

    def executar(self, funcao):
        from conversao_interacoes_vetores.erros import LimiteMemoriaError

        raise LimiteMemoriaError("Processo com 6000.0 MB; limite: 5500 MB.")


class CursorFalso:
    def __init__(self, linhas=None, rowcount=1) -> None:
        self.linhas = list(linhas or [])
        self.rowcount = rowcount
        self.consultas: list[tuple] = []
        self.erro_em = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, consulta, parametros=None):
        self.consultas.append((consulta, parametros))
        if self.erro_em and self.erro_em in consulta:
            raise RuntimeError("falha simulada de banco")

    def fetchone(self):
        return self.linhas[0] if self.linhas else None

    def fetchall(self):
        return self.linhas


class ConexaoFalsa:
    def __init__(self, cursor: CursorFalso) -> None:
        self.cursor_falso = cursor
        self.transacoes = 0
        self.fechada = False

    def cursor(self):
        return self.cursor_falso

    def transaction(self):
        self.transacoes += 1
        return nullcontext()

    def close(self):
        self.fechada = True


def criar_repositorio(cursor: CursorFalso):
    from conversao_interacoes_vetores.repositorio import RepositorioEmbeddings

    repositorio = RepositorioEmbeddings.__new__(RepositorioEmbeddings)
    repositorio.conexao = ConexaoFalsa(cursor)
    return repositorio


class RepositorioFalso:
    def __init__(self, hashes=None, erro=None) -> None:
        self.hashes = dict(hashes or {})
        self.erro = erro
        self.gravacoes: list[tuple] = []

    def hashes_existentes(self, nome_normalizado: str) -> dict[int, str]:
        return dict(self.hashes)

    def gravar_embeddings(self, nome_normalizado, dimensao, chunks) -> int:
        if self.erro is not None:
            raise self.erro
        self.gravacoes.append((nome_normalizado, dimensao, list(chunks)))
        return len(chunks)


class RelatorioFalso:
    def __init__(self) -> None:
        self.linhas: list[dict] = []

    def registrar(self, dados: dict) -> None:
        self.linhas.append(dados)


def texto_com_palavras(quantidade: int, comprimento: int = 3) -> str:
    """Texto previsível: cada palavra vira exatamente um token no ProvedorFalso."""
    return " ".join(f"{'a' * (comprimento - 1)}{indice % 10}" for indice in range(quantidade))
