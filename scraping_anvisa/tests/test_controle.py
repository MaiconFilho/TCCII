import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from anvisa_scraper.controle import ControleColeta
from anvisa_scraper.modelos import BulaLocalizada, MedicamentoParaColeta


class CursorFalso:
    def __init__(self, linha: dict | None = None) -> None:
        self.linha = linha
        self.consultas: list[tuple[str, tuple | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, consulta: str, parametros: tuple | None = None) -> None:
        self.consultas.append((consulta, parametros))

    def fetchone(self) -> dict | None:
        return self.linha


class ConexaoFalsa:
    def __init__(self, cursor: CursorFalso) -> None:
        self.cursor_falso = cursor

    def cursor(self) -> CursorFalso:
        return self.cursor_falso


def criar_controle(cursor: CursorFalso) -> ControleColeta:
    controle = ControleColeta.__new__(ControleColeta)
    controle.conexao = ConexaoFalsa(cursor)
    return controle


class TestControleColeta(unittest.TestCase):
    def test_finalizar_grava_data_de_publicacao(self) -> None:
        cursor = CursorFalso()
        controle = criar_controle(cursor)
        medicamento = MedicamentoParaColeta("ABLOK PLUS", "ablok plus")
        data_publicacao = datetime(2025, 4, 25, 13, 18, 58, tzinfo=timezone.utc)
        bula = BulaLocalizada(
            nome_normalizado="ablok plus",
            nome_produto="ABLOK PLUS",
            numero_registro="109740092",
            expediente="0556857259",
            id_bula_profissional="id-protegido",
            data_publicacao=data_publicacao,
        )

        controle.finalizar(medicamento, status="CONCLUIDO", bula=bula)

        consulta, parametros = cursor.consultas[-1]
        self.assertIn("data_publicacao_anvisa =", consulta)
        self.assertNotIn("data_publicacao_original =", consulta)
        self.assertNotIn("data_atualizacao_base_anvisa", consulta)
        self.assertNotIn("data_atualizacao_base_original", consulta)
        assert parametros is not None
        self.assertIn(data_publicacao, parametros)

    def test_concluido_com_pdf_existente_nao_deve_ser_reprocessado(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            pdf = Path(pasta) / "ablok_plus.pdf"
            pdf.write_bytes(b"%PDF-1.7")
            cursor = CursorFalso(
                {
                    "status": "CONCLUIDO",
                    "caminho_pdf": str(pdf),
                    "data_publicacao_anvisa": None,
                }
            )

            concluido = criar_controle(cursor).concluido("ablok plus")

        self.assertTrue(concluido)

    def test_pdf_orfao_valido_e_recuperado_sem_download(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            pdf = Path(pasta) / "a_saude_da_mulher_102351059_profissional.pdf"
            conteudo = b"%PDF-1.7\nconteudo existente"
            pdf.write_bytes(conteudo)
            cursor = CursorFalso(
                {
                    "numero_registro": "102351059",
                    "data_publicacao_anvisa": datetime(
                        2025,
                        11,
                        18,
                        tzinfo=timezone.utc,
                    ),
                }
            )
            medicamento = MedicamentoParaColeta(
                "A SAÚDE DA MULHER",
                "a saude da mulher",
            )

            recuperado = criar_controle(cursor).recuperar_pdf_existente(
                medicamento,
                Path(pasta),
            )

        self.assertTrue(recuperado)
        consulta, parametros = cursor.consultas[-1]
        self.assertIn("status = 'CONCLUIDO'", consulta)
        assert parametros is not None
        self.assertIn("PDF existente recuperado; download não repetido.", parametros)


if __name__ == "__main__":
    unittest.main()
