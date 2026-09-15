import tempfile
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import RegistroJaExisteError
from extracao_interacoes.modelos import BulaParaExtracao
from extracao_interacoes.repositorio import RepositorioInteracoes, SQL_CRIAR_TABELA


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
    def test_metricas_sao_gravadas_e_atualizadas_com_o_trecho(self):
        for reprocessar in (False, True):
            with self.subTest(reprocessar=reprocessar):
                cursor = CursorFalso()
                criar_repositorio(cursor).gravar_resultado(
                    criar_bula(), "trecho", reprocessar=reprocessar,
                    tempo_leitura_segundos=0.125,
                    tempo_inferencia_segundos=3.25,
                    tempo_total_segundos=4.5,
                )
                consulta, parametros = cursor.consultas[0]
                self.assertEqual(parametros[-3:], (0.125, 3.25, 4.5))
                for nome in ("tempo_leitura_segundos", "tempo_inferencia_segundos", "tempo_total_segundos"):
                    self.assertIn(nome, consulta)
                    if reprocessar:
                        self.assertIn(f"{nome} = EXCLUDED.{nome}", consulta)

    def test_criacao_sql_e_python_tem_o_mesmo_esquema_com_metricas(self):
        sql = (Path(__file__).resolve().parents[1] / "criar_tabela.sql").read_text(encoding="utf-8")
        self.assertEqual(" ".join(sql.strip().rstrip(";").split()), " ".join(SQL_CRIAR_TABELA.split()))
        for nome in ("tempo_leitura_segundos", "tempo_inferencia_segundos", "tempo_total_segundos"):
            self.assertIn(f"{nome} NUMERIC(12,3)", sql)

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
        self.assertEqual(parametros, (None, None, False))
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
        self.assertIn("VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", consulta)
        self.assertEqual(
            parametros,
            ("medicamento", "001234", "000567", "trecho integral", "CONCLUIDO", None, None, None, None),
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

    def test_revisao_nao_sobrescreve_conclusao_anterior(self):
        cursor = CursorFalso(rowcount=0)
        criar_repositorio(cursor).gravar_resultado(
            criar_bula(), None, reprocessar=True,
            status_extracao="REVISAO_MANUAL", detalhe_revisao="Limite incerto",
        )
        sql, parametros = cursor.consultas[0]
        self.assertIn("WHERE EXCLUDED.status_extracao <> 'REVISAO_MANUAL'", sql)
        self.assertIn("OR bulas_interacoes.status_extracao = 'REVISAO_MANUAL'", sql)
        self.assertEqual(parametros[4:6], ("REVISAO_MANUAL", "Limite incerto"))

    def test_ausencia_e_pendencia_possuem_status_diferentes(self):
        cursor = CursorFalso()
        criar_repositorio(cursor).gravar_resultado(criar_bula(), None)
        self.assertEqual(cursor.consultas[0][1][4], "SEM_SECAO_INTERACOES")

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

        self.assertEqual(cursor.consultas[0][1], (None, None, True))

    def test_selecao_por_nome_normalizado_e_exata_e_parametrizada(self) -> None:
        cursor = CursorFalso()

        criar_repositorio(cursor).selecionar_bulas(
            nome_normalizado="a saude da mulher",
            reprocessar=True,
        )

        consulta, parametros = cursor.consultas[0]
        self.assertIn("b.nome_normalizado = %s", consulta)
        self.assertEqual(
            parametros,
            ("a saude da mulher", "a saude da mulher", True),
        )


if __name__ == "__main__":
    unittest.main()
