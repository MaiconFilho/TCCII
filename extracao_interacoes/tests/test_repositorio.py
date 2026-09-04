import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

from extracao_interacoes.erros import RegistroJaExisteError
from extracao_interacoes.modelos import BulaParaExtracao
from extracao_interacoes.repositorio import RepositorioInteracoes


class CursorFalso:
    def __init__(self, linhas=None, rowcount=1) -> None:
        self.linhas = linhas or []
        self.rowcount = rowcount
        self.consultas = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, consulta, parametros=None):
        self.consultas.append((consulta, parametros))

    def fetchall(self):
        return self.linhas


class ConexaoFalsa:
    def __init__(self, cursor: CursorFalso) -> None:
        self.cursor_falso = cursor
        self.transacoes = 0

    def cursor(self):
        return self.cursor_falso

    def transaction(self):
        self.transacoes += 1
        return nullcontext()


def criar_repositorio(cursor: CursorFalso) -> RepositorioInteracoes:
    repositorio = RepositorioInteracoes.__new__(RepositorioInteracoes)
    repositorio.conexao = ConexaoFalsa(cursor)
    return repositorio


def criar_bula() -> BulaParaExtracao:
    return BulaParaExtracao(
        nome_normalizado="medicamento",
        numero_registro="001234",
        expediente="000567",
        caminho_pdf=Path("bula.pdf"),
    )


class TestRepositorioInteracoes(unittest.TestCase):
    def test_selecao_filtra_duplicidade_e_preserva_ordem_deterministica(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            pdf_a = Path(pasta) / "a.pdf"
            pdf_b = Path(pasta) / "b.pdf"
            pdf_a.write_bytes(b"%PDF")
            pdf_b.write_bytes(b"%PDF")
            cursor = CursorFalso(
                [
                    {
                        "nome_normalizado": "a",
                        "numero_registro": "001",
                        "expediente": "010",
                        "caminho_pdf": str(pdf_a),
                    },
                    {
                        "nome_normalizado": "b",
                        "numero_registro": "002",
                        "expediente": "020",
                        "caminho_pdf": str(pdf_b),
                    },
                ]
            )
            repositorio = criar_repositorio(cursor)

            selecionadas = repositorio.selecionar_bulas(inicio=1, limite=1)

        consulta, parametros = cursor.consultas[0]
        self.assertIn("b.status = 'CONCLUIDO'", consulta)
        self.assertIn("NOT EXISTS", consulta)
        self.assertIn("ORDER BY", consulta)
        self.assertEqual(parametros, (False,))
        self.assertEqual([item.nome_normalizado for item in selecionadas], ["b"])

    def test_selecao_descarta_caminho_inexistente(self) -> None:
        cursor = CursorFalso(
            [
                {
                    "nome_normalizado": "ausente",
                    "numero_registro": "001",
                    "expediente": None,
                    "caminho_pdf": "arquivo-que-nao-existe.pdf",
                }
            ]
        )

        selecionadas = criar_repositorio(cursor).selecionar_bulas()

        self.assertEqual(selecionadas, [])

    def test_gravacao_parametrizada_associa_identificadores_e_trecho(self) -> None:
        cursor = CursorFalso()
        repositorio = criar_repositorio(cursor)
        bula = criar_bula()

        repositorio.gravar_resultado(bula, "trecho integral")

        consulta, parametros = cursor.consultas[0]
        self.assertIn("VALUES (%s, %s, %s, %s)", consulta)
        self.assertEqual(
            parametros,
            ("medicamento", "001234", "000567", "trecho integral"),
        )
        self.assertEqual(repositorio.conexao.transacoes, 1)

    def test_prevencao_de_duplicidade_sem_reprocessar(self) -> None:
        cursor = CursorFalso(rowcount=0)

        with self.assertRaises(RegistroJaExisteError):
            criar_repositorio(cursor).gravar_resultado(
                criar_bula(),
                "trecho",
                reprocessar=False,
            )

    def test_reprocessamento_atualiza_tambem_os_identificadores(self) -> None:
        cursor = CursorFalso()

        criar_repositorio(cursor).gravar_resultado(
            criar_bula(),
            "trecho novo",
            reprocessar=True,
        )

        consulta, parametros = cursor.consultas[0]
        self.assertIn("DO UPDATE", consulta)
        self.assertIn("numero_registro = EXCLUDED.numero_registro", consulta)
        self.assertIn("expediente = EXCLUDED.expediente", consulta)
        self.assertEqual(parametros[1:3], ("001234", "000567"))

    def test_reprocessar_inclui_registros_existentes_na_selecao(self) -> None:
        cursor = CursorFalso()

        criar_repositorio(cursor).selecionar_bulas(reprocessar=True)

        self.assertEqual(cursor.consultas[0][1], (True,))


if __name__ == "__main__":
    unittest.main()
