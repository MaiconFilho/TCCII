import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pipelines.extracao_interacoes.cadastro import ResultadoCadastro
from pipelines.extracao_interacoes.repositorio_cadastro import RepositorioCadastro
from pipelines.extracao_interacoes.erros import ErroBancoError, EsquemaBancoIncompativelError
from test_repositorio import CursorFalso, ConexaoFalsa, criar_bula


class TestRepositorioCadastro(unittest.TestCase):
    def test_esquema_e_verificado_sem_ddl(self):
        cursor = CursorFalso([{"column_name": nome, "data_type": "text"} for nome in ("principios_ativos", "composicao")])
        RepositorioCadastro(ConexaoFalsa(cursor)).validar_esquema()
        self.assertEqual(len(cursor.consultas), 1)
        self.assertTrue(cursor.consultas[0][0].strip().startswith("SELECT"))
        with self.assertRaises(EsquemaBancoIncompativelError):
            RepositorioCadastro(ConexaoFalsa(CursorFalso())).validar_esquema()

    def test_seleciona_apenas_cadastro_pendente_independente_das_interacoes(self):
        cursor = CursorFalso([{"nome_normalizado": "a", "numero_registro": "001", "expediente": None, "caminho_pdf": "inexistente.pdf"}])
        selecionadas = RepositorioCadastro(ConexaoFalsa(cursor)).selecionar(limite=None)
        sql, params = cursor.consultas[0]
        self.assertNotIn("bulas_interacoes", sql)
        self.assertIn("NULLIF(BTRIM(principios_ativos), '')", sql)
        self.assertIn("NULLIF(BTRIM(composicao), '')", sql)
        self.assertEqual(params, (None, None, False, False, False, False, 0, None))
        self.assertEqual(len(selecionadas), 1)

    def test_somente_composicao_filtra_apenas_campo_composicao(self):
        cursor = CursorFalso()
        RepositorioCadastro(ConexaoFalsa(cursor)).selecionar(
            limite=None, somente_composicao=True,
        )
        sql, params = cursor.consultas[0]
        self.assertIn("%s AND (%s OR NULLIF(BTRIM(composicao), '') IS NULL)", sql)
        self.assertEqual(params, (None, None, True, False, True, False, 0, None))

    def test_grava_so_dois_campos_com_parametros_e_preserva_existentes(self):
        cursor = CursorFalso()
        conexao = ConexaoFalsa(cursor)
        r = ResultadoCadastro(status="CONCLUIDO", principios_ativos="dipirona", composicao="texto literal")
        RepositorioCadastro(conexao).gravar(criar_bula(), r)
        sql, params = cursor.consultas[0]
        self.assertIn("UPDATE public.bulas", sql)
        self.assertNotIn("bulas_interacoes", sql)
        self.assertIn("ELSE principios_ativos END", sql)
        self.assertIn("ELSE composicao END", sql)
        self.assertIn("numero_registro IS NOT DISTINCT FROM", sql)
        self.assertEqual(params[:5], ("dipirona", False, "dipirona", False, "texto literal"))
        self.assertEqual(conexao.transacoes, 1)

    def test_revisao_dos_principios_grava_composicao_sem_apagar_ativo_existente(self):
        cursor = CursorFalso()
        resultado = ResultadoCadastro(status="REVISAO_MANUAL", composicao="COMPOSIÇÃO\ntexto literal")
        RepositorioCadastro(ConexaoFalsa(cursor)).gravar(criar_bula(), resultado, reprocessar=True)
        sql, params = cursor.consultas[0]
        self.assertIn("WHEN CAST(%s AS TEXT) IS NOT NULL", sql)
        self.assertEqual(params[:5], (None, True, None, True, resultado.composicao))

    def test_revisao_e_resultado_vazio_nao_disparam_sql(self):
        cursor = CursorFalso()
        repo = RepositorioCadastro(ConexaoFalsa(cursor))
        for r in (ResultadoCadastro(), ResultadoCadastro(status="CONCLUIDO")):
            with self.assertRaises(ErroBancoError):
                repo.gravar(criar_bula(), r)
        self.assertFalse(cursor.consultas)

    def test_bula_modificada_nao_e_atualizada(self):
        repo = RepositorioCadastro(ConexaoFalsa(CursorFalso(rowcount=0)))
        with self.assertRaises(ErroBancoError):
            repo.gravar(criar_bula(), ResultadoCadastro(status="CONCLUIDO", principios_ativos="a", composicao="b"))

    def test_atualizacao_usa_caminho_original_do_banco(self):
        cursor = CursorFalso([{"nome_normalizado": "a", "numero_registro": "001", "expediente": None, "caminho_pdf": "../pdfs/a.pdf"}])
        repo = RepositorioCadastro(ConexaoFalsa(cursor))
        selecionada = repo.selecionar()[0]
        self.assertTrue(selecionada.caminho_pdf.is_absolute())
        repo.gravar(selecionada, ResultadoCadastro(status="CONCLUIDO", principios_ativos="a", composicao="b"))
        self.assertEqual(cursor.consultas[-1][1][-1], "../pdfs/a.pdf")
