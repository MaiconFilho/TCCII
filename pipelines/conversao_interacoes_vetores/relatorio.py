import csv
from pathlib import Path
from typing import Any


COLUNAS_RELATORIO = [
    "nome_normalizado",
    "quantidade_chunks",
    "modelo",
    "dimensao",
    "quantidade_tokens",
    "texto_hash",
    "status",
    "tempo_chunking_segundos",
    "tempo_inferencia_segundos",
    "tempo_banco_segundos",
    "tempo_total_segundos",
    "memoria_antes_mb",
    "pico_memoria_mb",
    "detalhe_erro",
]


class RelatorioEmbeddingsCsv:
    """Mesmo padrão do relatório de extração: CSV local, ; e utf-8-sig."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho.resolve()
        self.arquivo = None
        self.escritor = None

    def __enter__(self) -> "RelatorioEmbeddingsCsv":
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.arquivo = self.caminho.open("w", newline="", encoding="utf-8-sig")
        self.escritor = csv.DictWriter(
            self.arquivo,
            fieldnames=COLUNAS_RELATORIO,
            delimiter=";",
        )
        self.escritor.writeheader()
        self.arquivo.flush()
        return self

    def __exit__(self, *_args: object) -> None:
        if self.arquivo is not None:
            self.arquivo.close()

    def registrar(self, dados: dict[str, Any]) -> None:
        if self.escritor is None or self.arquivo is None:
            raise RuntimeError(
                "O relatório deve ser aberto como gerenciador de contexto."
            )
        linha = {coluna: dados.get(coluna, "") for coluna in COLUNAS_RELATORIO}
        self.escritor.writerow(linha)
        self.arquivo.flush()
