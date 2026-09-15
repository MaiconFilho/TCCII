import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from extracao_interacoes.validacao import localizar_fallback_estrutural, localizar_titulos_interacoes, copiar_e_validar_trecho
from helpers import criar_documento


class TestTitulosReteste(unittest.TestCase):
    def test_variantes_de_titulo_e_limite_preservam_original(self):
        for titulo in (
            '6 – INTERAÇÕES MEDICAMENTOSAS',
            '6 — INTERAÇÕES MEDICAMENTOSAS',
            '6. INTERAÇÔES MEDICAMENTOSAS',
            '6. INTERACOES MEDICAMENTOSAS',
            '6. INTERAÇÕES MEDICAMENTOSAS E OUTRAS INTERAÇÕES',
            '6. INTERAÇÕES MEDICAMENTOSAS E OUTRAS FORMAS DE INTERAÇÃO',
            '6. INTERAÇÕES COM OUTROS MEDICAMENTOS E OUTRAS FORMAS DE INTERAÇÃO',
        ):
            with self.subTest(titulo=titulo):
                doc = criar_documento([titulo, 'A associacao altera a concentracao do medicamento.',
                    'Interferencia em exames laboratoriais', 'Pode alterar os resultados dos exames.',
                    '7 – CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO', 'Conservar em lugar seco.'])
                candidatos = localizar_fallback_estrutural(doc)
                self.assertEqual(len(candidatos), 1)
                inicio, fim, nome = candidatos[0]
                self.assertEqual(fim, 'L000005')
                trecho = copiar_e_validar_trecho(doc, inicio, fim, nome)
                self.assertTrue(trecho.startswith(titulo))
                self.assertIn('Pode alterar', trecho)
                self.assertNotIn('Conservar', trecho)

    def test_frase_sumario_e_titulo_de_outra_secao_nao_sao_aceitos(self):
        for texto in ('Vide 6 – INTERAÇÕES MEDICAMENTOSAS',
                      '6 – INTERAÇÕES MEDICAMENTOSAS ..... 12',
                      'INTERAÇÕES MEDICAMENTOSAS REAÇÕES ADVERSAS',
                      'Interações medicamentosas e outras formas de interação devem ser avaliadas.'):
            with self.subTest(texto=texto):
                self.assertFalse(localizar_titulos_interacoes(criar_documento([texto])))

    def test_titulo_estendido_quebrado_em_linhas(self):
        doc = criar_documento(['6 – INTERAÇÕES MEDICAMENTOSAS', 'E OUTRAS FORMAS DE INTERAÇÃO',
            'Pode alterar o efeito de outros medicamentos.', '7 – CUIDADOS DE ARMAZENAMENTO'])
        titulos = localizar_titulos_interacoes(doc)
        self.assertEqual(titulos[0][1], 2)
        self.assertEqual(len(localizar_fallback_estrutural(doc)), 1)
