import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from pipelines.scraping_anvisa.anvisa_scraper.controle import ControleColeta
from pipelines.scraping_anvisa.anvisa_scraper.modelos import BulaLocalizada, MedicamentoParaColeta


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
    def test_criacao_de_bulas_inclui_cadastro_sem_preencher_texto_ficticio(self):
        cursor = CursorFalso()
        with patch("pipelines.scraping_anvisa.anvisa_scraper.controle.psycopg.connect", return_value=ConexaoFalsa(cursor)):
            ControleColeta("conexao-falsa")
        self.assertTrue(all(sql.strip().startswith('SELECT') for sql,_ in cursor.consultas))
        self.assertIn('principios_ativos',cursor.consultas[0][0])
        self.assertIn('composicao',cursor.consultas[0][0])

    def test_finalizar_grava_data_de_publicacao(self):
        cursor=CursorFalso()
        criar_controle(cursor).finalizar(MedicamentoParaColeta('ABLOK PLUS','ablok plus'),status='CONCLUIDO')
        consulta,parametros=cursor.consultas[-1]
        self.assertNotIn('data_publicacao_anvisa',consulta)
        self.assertNotIn('sha256_pdf',consulta)
        self.assertNotIn('caminho_pdf',consulta)
        self.assertEqual(parametros[-1],'ablok plus')

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
        self.assertEqual(parametros, ("a saude da mulher",))


if __name__ == "__main__":
    unittest.main()
