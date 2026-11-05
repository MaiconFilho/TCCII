import csv
import hashlib
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from pipelines.caminhos import resolver_caminho

from .modelos import BulaLocalizada, MedicamentoParaColeta, montar_slug_nome


class ControleColeta:
    def __init__(self, dsn: str) -> None:
        self.conexao = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        # Instala??o expl?cita: banco/modelo.sql. O coletor n?o recria schema.
        with self.conexao.cursor() as cursor:
            cursor.execute("SELECT nome_normalizado,nome_anvisa,numero_registro,expediente,status,tentativas,tempo_total_segundos,principios_ativos,composicao FROM bulas LIMIT 0")

    def fechar(self) -> None:
        self.conexao.close()

    def concluido(self, nome_normalizado: str) -> bool:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM bulas
                WHERE nome_normalizado = %s
                """,
                (nome_normalizado,),
            )
            linha = cursor.fetchone()
        if not linha or linha["status"] != "CONCLUIDO":
            return False
        return True  # Base fixa: conclu?do n?o ? baixado novamente, mesmo sem PDF local.

    def recuperar_pdf_existente(
        self,
        medicamento: MedicamentoParaColeta,
        pasta_pdfs: Path,
    ) -> bool:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    numero_registro
                FROM bulas
                WHERE nome_normalizado = %s
                """,
                (medicamento.nome_normalizado,),
            )
            linha = cursor.fetchone()

        if (
            not linha
        ):
            return False

        numero_registro = "".join(
            caractere
            for caractere in str(linha["numero_registro"] or "")
            if caractere.isdigit()
        )
        if not numero_registro:
            return False

        pasta_resolvida = pasta_pdfs.resolve()
        nome_arquivo = (
            f"{montar_slug_nome(medicamento.nome_normalizado)}_"
            f"{numero_registro}_profissional.pdf"
        )
        caminho = (pasta_resolvida / nome_arquivo).resolve()
        if caminho.parent != pasta_resolvida or not caminho.is_file():
            return False

        conteudo = caminho.read_bytes()
        if not conteudo.startswith(b"%PDF"):
            return False

        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                UPDATE bulas
                SET
                    status = 'CONCLUIDO'
                WHERE nome_normalizado = %s
                """,
                (
                    medicamento.nome_normalizado,
                ),
            )
        return True

    def iniciar(self, medicamento: MedicamentoParaColeta) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute("""INSERT INTO bulas(nome_normalizado,status,tentativas)
                VALUES (%s,'PROCESSANDO',1)
                ON CONFLICT(nome_normalizado) DO UPDATE SET
                    status='PROCESSANDO',tentativas=COALESCE(bulas.tentativas,0)+1,
                    tempo_total_segundos=NULL WHERE bulas.status IS DISTINCT FROM 'CONCLUIDO'
            """,(medicamento.nome_normalizado,))

    def finalizar(
        self,
        medicamento: MedicamentoParaColeta,
        status: str,
        mensagem: str = "",
        bula: BulaLocalizada | None = None,
        caminho_pdf: str = "",
        sha256_pdf: str = "",
        tamanho_pdf: int | None = None,
        tempo_consulta_segundos: float | None = None,
        tempo_download_segundos: float | None = None,
        tempo_total_segundos: float | None = None,
    ) -> None:
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                UPDATE bulas SET
                    nome_anvisa = COALESCE(%s, nome_anvisa),
                    numero_registro = COALESCE(%s, numero_registro),
                    expediente = COALESCE(%s, expediente),
                    status = %s,
                    tempo_total_segundos = %s
                WHERE nome_normalizado = %s
                """,
                (
                    bula.nome_produto if bula else None,
                    bula.numero_registro if bula else None,
                    bula.expediente if bula else None,
                    status,
                    tempo_total_segundos,
                    medicamento.nome_normalizado,
                ),
            )

    def exportar_csv(self, caminho: Path) -> Path:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM bulas
                ORDER BY nome_normalizado
                """
            )
            linhas = cursor.fetchall()
            colunas = [descricao.name for descricao in cursor.description or []]

        def gravar(destino: Path) -> None:
            with destino.open("w", newline="", encoding="utf-8-sig") as arquivo:
                escritor = csv.DictWriter(
                    arquivo,
                    fieldnames=colunas,
                    delimiter=";",
                )
                escritor.writeheader()
                escritor.writerows(linhas)

        try:
            gravar(caminho)
            return caminho
        except PermissionError:
            sufixo_data = datetime.now().strftime("%Y%m%d_%H%M%S")
            alternativo = caminho.with_name(
                f"{caminho.stem}_{sufixo_data}{caminho.suffix}"
            )
            gravar(alternativo)
            print(
                f"Aviso: '{caminho.name}' estava aberto. "
                f"Relatório salvo em '{alternativo.name}'."
            )
            return alternativo
