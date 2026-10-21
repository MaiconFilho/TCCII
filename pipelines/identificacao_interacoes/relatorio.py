import csv
import json
from pathlib import Path
from typing import Any


COLUNAS_RELATORIO = [
    "nome_normalizado",
    "status",
    "processamento_id",
    "versao_processamento",
    "caracteres_trecho",
    "quantidade_janelas",
    "janelas_concluidas",
    "divisoes_por_limite",
    "releituras",
    "chamadas_llm",
    "tokens_entrada_total",
    "tokens_saida_total",
    "relacoes_extraidas",
    "duplicatas_consolidadas",
    "rejeicoes",
    "rejeicoes_pendentes",
    "propostas_brutas",
    "aceitas_antes_consolidacao",
    "resumos_gerados",
    "resumos_aceitos",
    "resumos_rejeitados",
    "chamadas_adicionais",
    "vinculos_criados",
    "vinculos_reativados",
    "vinculos_atualizados",
    "vinculos_invalidados",
    "tempo_inferencia_segundos",
    "tempo_banco_segundos",
    "tempo_total_segundos",
    "memoria_antes_mb",
    "pico_memoria_mb",
    "memoria_depois_mb",
    "detalhe_erro",
]


class RelatorioIdentificacao:
    """CSV por medicamento (mesmo padrão dos outros módulos: ``;`` e utf-8-sig)
    e um JSONL ao lado com as relações e rejeições, para revisão humana."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho.resolve()
        self.caminho_detalhes = self.caminho.with_name(self.caminho.stem + "_detalhes.jsonl")
        self.arquivo = None
        self.arquivo_detalhes = None
        self.escritor = None

    def __enter__(self) -> "RelatorioIdentificacao":
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        if self.caminho.exists() or self.caminho_detalhes.exists():
            raise FileExistsError("Escolha outro relatório; resultados anteriores são preservados.")
        self.arquivo = self.caminho.open("x", newline="", encoding="utf-8-sig")
        self.arquivo_detalhes = self.caminho_detalhes.open("x", encoding="utf-8")
        self.escritor = csv.DictWriter(self.arquivo, fieldnames=COLUNAS_RELATORIO, delimiter=";")
        self.escritor.writeheader()
        self.arquivo.flush()
        return self

    def __exit__(self, *_args: object) -> None:
        for arquivo in (self.arquivo, self.arquivo_detalhes):
            if arquivo is not None:
                arquivo.close()

    def registrar(self, dados: dict[str, Any], detalhes: dict[str, Any] | None = None) -> None:
        if self.escritor is None or self.arquivo is None or self.arquivo_detalhes is None:
            raise RuntimeError("O relatório deve ser aberto como gerenciador de contexto.")
        self.escritor.writerow({coluna: dados.get(coluna, "") for coluna in COLUNAS_RELATORIO})
        self.arquivo.flush()
        if detalhes is not None:
            self.arquivo_detalhes.write(json.dumps(detalhes, ensure_ascii=False, default=str) + "\n")
            self.arquivo_detalhes.flush()
