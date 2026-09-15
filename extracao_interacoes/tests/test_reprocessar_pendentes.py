import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from extracao_interacoes.erros import ErroBancoError
from extracao_interacoes.modelos import BulaParaExtracao
from extracao_interacoes.reprocessar_pendentes import elegivel, nomes_sem_texto, RepositorioRetentativa


class TestRetentativa(unittest.TestCase):
    def test_csv_opcional_nao_exige_relatorio_local(self):
        self.assertEqual(nomes_sem_texto(None), set())

    def test_banco_prevalece_sobre_csv_e_so_pendentes_sao_selecionados(self):
        self.assertFalse(elegivel('CONCLUIDO', 'aas', {'aas'}))
        self.assertFalse(elegivel(None, 'novo', {'aas'}))
        self.assertTrue(elegivel(None, 'aas', {'aas'}))
        for status in ('SEM_SECAO_INTERACOES', 'REVISAO_MANUAL', 'PDF_SEM_TEXTO'):
            self.assertTrue(elegivel(status, 'aas', set()))

    def test_csv_deduplica_e_usa_apenas_pdf_sem_texto(self):
        with tempfile.TemporaryDirectory() as pasta:
            p = Path(pasta)/'lote.csv'
            p.write_text('nome_normalizado;status\na;PDF_SEM_TEXTO\na;PDF_SEM_TEXTO\nb;CONCLUIDO\n', encoding='utf-8-sig')
            self.assertEqual(nomes_sem_texto(p), {'a'})
            p.write_text('outra;coluna\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                nomes_sem_texto(p)

    def test_nova_falha_nao_apaga_resultado_anterior(self):
        conexao = MagicMock()
        repo = RepositorioRetentativa(conexao)
        for status in ('SEM_SECAO_INTERACOES', 'REVISAO_MANUAL', 'PDF_SEM_TEXTO'):
            repo.gravar_resultado(None, None, status_extracao=status)
        conexao.execute.assert_not_called()

    def test_conclusao_vazia_e_conflito_nao_sao_reportados_como_gravados(self):
        conexao = MagicMock()
        repo = RepositorioRetentativa(conexao)
        with self.assertRaises(ErroBancoError):
            repo.gravar_resultado(None, ' ', status_extracao='CONCLUIDO')
        conexao.execute.return_value.rowcount = 0
        bula = BulaParaExtracao('aas', '123', None, Path('bula.pdf'))
        with self.assertRaises(ErroBancoError):
            repo.gravar_resultado(bula, 'Texto valido.', status_extracao='CONCLUIDO')

    def test_gravacao_condicional_nao_substitui_concluido_concorrente(self):
        conexao = MagicMock()
        conexao.execute.return_value.rowcount = 1
        repo = RepositorioRetentativa(conexao)
        bula = BulaParaExtracao('aas', '123', None, Path('bula.pdf'))
        repo.gravar_resultado(bula, 'Texto valido.', status_extracao='CONCLUIDO')
        sql, valores = conexao.execute.call_args.args
        self.assertIn('WHERE destino.status_extracao = ANY', sql)
        self.assertNotIn('CONCLUIDO', valores[-1])
        self.assertEqual(valores[:4], ('aas', '123', None, 'Texto valido.'))
