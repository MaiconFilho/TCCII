import csv
from pathlib import Path
from typing import Any


COLUNAS_RELATORIO = [
    "nome_normalizado",
    "numero_registro",
    "expediente",
    "arquivo_pdf",
    "status",
    "metodo_extracao",
    "titulo_encontrado",
    "linha_inicio",
    "linha_fim_exclusiva",
    "quantidade_paginas",
    "paginas_ocr",
    "tempo_ocr_segundos",
    "acertos_cache_ocr",
    "quantidade_janelas",
    "quantidade_chamadas_llm",
    "tokens_entrada_total",
    "tokens_saida_total",
    "memoria_antes_mb",
    "pico_memoria_mb",
    "memoria_depois_mb",
    "tempo_leitura_segundos",
    "tempo_inferencia_segundos",
    "tempo_total_segundos",
    "detalhe_erro",
]


class RelatorioCsv:
    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho.resolve()
        self.arquivo = None
        self.escritor = None

    def __enter__(self) -> "RelatorioCsv":
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
            raise RuntimeError("O relatório deve ser aberto como gerenciador de contexto.")
        linha = {coluna: dados.get(coluna, "") for coluna in COLUNAS_RELATORIO}
        self.escritor.writerow(linha)
        self.arquivo.flush()
