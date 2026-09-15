import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.hashing import (
    calcular_hash_chunk,
    calcular_hash_documento,
)


class TestHashChunk(unittest.TestCase):
    def test_hash_e_estavel_entre_chamadas(self):
        primeiro = calcular_hash_chunk("dipirona", 0, "texto", "modelo/a")
        segundo = calcular_hash_chunk("dipirona", 0, "texto", "modelo/a")

        self.assertEqual(primeiro, segundo)
        self.assertEqual(len(primeiro), 64)

    def test_alteracao_do_texto_muda_o_hash(self):
        original = calcular_hash_chunk("dipirona", 0, "texto", "modelo/a")
        alterado = calcular_hash_chunk("dipirona", 0, "texto novo", "modelo/a")

        self.assertNotEqual(original, alterado)

    def test_mudanca_do_modelo_muda_o_hash(self):
        com_a = calcular_hash_chunk("dipirona", 0, "texto", "modelo/a")
        com_b = calcular_hash_chunk("dipirona", 0, "texto", "modelo/b")

        self.assertNotEqual(com_a, com_b)

    def test_chunk_index_participa_do_hash(self):
        primeiro = calcular_hash_chunk("dipirona", 0, "texto", "modelo/a")
        segundo = calcular_hash_chunk("dipirona", 1, "texto", "modelo/a")

        self.assertNotEqual(primeiro, segundo)

    def test_nome_normalizado_participa_do_hash(self):
        primeiro = calcular_hash_chunk("dipirona", 0, "texto", "modelo/a")
        segundo = calcular_hash_chunk("ibuprofeno", 0, "texto", "modelo/a")

        self.assertNotEqual(primeiro, segundo)

    def test_separador_evita_colisao_por_concatenacao(self):
        primeiro = calcular_hash_chunk("ab", 0, "c", "modelo/a")
        segundo = calcular_hash_chunk("a", 0, "bc", "modelo/a")

        self.assertNotEqual(primeiro, segundo)


class TestHashDocumento(unittest.TestCase):
    def test_resume_os_hashes_dos_chunks_na_ordem(self):
        resumo = calcular_hash_documento(["a", "b"])

        self.assertEqual(len(resumo), 64)
        self.assertNotEqual(resumo, calcular_hash_documento(["b", "a"]))


if __name__ == "__main__":
    unittest.main()
