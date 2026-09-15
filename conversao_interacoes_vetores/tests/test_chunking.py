import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.chunking import (
    contar_tokens_do_texto,
    dividir_em_chunks,
)
from conversao_interacoes_vetores.erros import ErroChunkingError
from tests.helpers import ProvedorFalso, texto_com_palavras


class TestChunkingTextoCurto(unittest.TestCase):
    def test_texto_curto_gera_um_unico_chunk_indice_zero(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(5)

        chunks = dividir_em_chunks(texto, provedor, tamanho_chunk=10, sobreposicao=2)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].indice, 0)
        self.assertEqual(chunks[0].quantidade_tokens, 5)

    def test_texto_curto_preserva_o_trecho_original_sem_alteracao(self):
        provedor = ProvedorFalso()
        texto = "Não usar com varfarina — risco de sangramento (INR > 3)."

        chunks = dividir_em_chunks(texto, provedor, tamanho_chunk=50, sobreposicao=5)

        self.assertEqual(chunks[0].texto, texto)

    def test_texto_exatamente_no_limite_continua_em_um_chunk(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(10)

        chunks = dividir_em_chunks(texto, provedor, tamanho_chunk=10, sobreposicao=2)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].texto, texto)


class TestChunkingTextoLongo(unittest.TestCase):
    def test_texto_longo_e_dividido_em_varios_chunks(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(25)

        chunks = dividir_em_chunks(texto, provedor, tamanho_chunk=10, sobreposicao=2)

        self.assertGreater(len(chunks), 1)

    def test_indices_sao_sequenciais_e_preservam_a_ordem(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(40)

        chunks = dividir_em_chunks(texto, provedor, tamanho_chunk=10, sobreposicao=3)

        self.assertEqual(
            [chunk.indice for chunk in chunks], list(range(len(chunks)))
        )

    def test_sobreposicao_configuravel_altera_o_passo(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(30)

        sem_sobreposicao = dividir_em_chunks(
            texto, provedor, tamanho_chunk=10, sobreposicao=0
        )
        com_sobreposicao = dividir_em_chunks(
            texto, provedor, tamanho_chunk=10, sobreposicao=5
        )

        self.assertEqual(len(sem_sobreposicao), 3)
        self.assertGreater(len(com_sobreposicao), len(sem_sobreposicao))

    def test_nenhum_chunk_excede_o_tamanho_configurado(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(57)

        chunks = dividir_em_chunks(texto, provedor, tamanho_chunk=10, sobreposicao=4)

        for chunk in chunks:
            self.assertLessEqual(chunk.quantidade_tokens, 10)

    def test_nao_ha_truncamento_todos_os_tokens_sao_cobertos(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(57)
        tamanho, sobreposicao = 10, 4
        passo = tamanho - sobreposicao
        total = provedor.contar_tokens(texto)

        chunks = dividir_em_chunks(texto, provedor, tamanho, sobreposicao)

        cobertos = set()
        for posicao, chunk in enumerate(chunks):
            inicio = min(posicao * passo, total - chunk.quantidade_tokens)
            cobertos.update(range(inicio, inicio + chunk.quantidade_tokens))
        self.assertEqual(cobertos, set(range(total)))

    def test_ultimo_chunk_alcanca_o_final_do_texto(self):
        provedor = ProvedorFalso()
        texto = texto_com_palavras(23)

        chunks = dividir_em_chunks(texto, provedor, tamanho_chunk=10, sobreposicao=3)
        passo = 10 - 3
        inicio_do_ultimo = (len(chunks) - 1) * passo

        self.assertGreaterEqual(
            inicio_do_ultimo + chunks[-1].quantidade_tokens, 23
        )


class TestChunkingValidacoes(unittest.TestCase):
    def test_texto_vazio_e_recusado(self):
        with self.assertRaises(ErroChunkingError):
            dividir_em_chunks("   ", ProvedorFalso(), 10, 2)

    def test_sobreposicao_maior_ou_igual_ao_chunk_e_recusada(self):
        with self.assertRaises(ErroChunkingError):
            dividir_em_chunks(texto_com_palavras(20), ProvedorFalso(), 10, 10)

    def test_sobreposicao_negativa_e_recusada(self):
        with self.assertRaises(ErroChunkingError):
            dividir_em_chunks(texto_com_palavras(20), ProvedorFalso(), 10, -1)

    def test_falha_do_tokenizer_vira_erro_de_chunking(self):
        class ProvedorQuebrado(ProvedorFalso):
            def tokenizar(self, texto):
                raise RuntimeError("tokenizer indisponível")

        with self.assertRaises(ErroChunkingError):
            dividir_em_chunks("texto válido", ProvedorQuebrado(), 10, 2)

    def test_contagem_de_tokens_usa_o_tokenizer_do_modelo(self):
        provedor = ProvedorFalso()

        self.assertEqual(contar_tokens_do_texto(texto_com_palavras(7), provedor), 7)


if __name__ == "__main__":
    unittest.main()
