import unittest

from pipelines.identificacao_interacoes.contexto_evidencia import (
    blocos_estruturados, completar_evidencia, efeito_do_bloco,
)
from pipelines.identificacao_interacoes.dicionario import recortar_evidencia, trechos

LISTA = ("Medicamentos: salbutamol; formoterol; linezolida; azul metileno; ozanimod; midodrina.")
EFEITO = ("Efeitos da interação: o uso concomitante com nafazolina nasal pode aumentar o risco "
          "de efeitos adversos cardiovasculares, como elevações na frequência cardíaca e "
          "pressão arterial ou ritmo cardíaco irregular.")
BLOCO = LISTA + "\n" + EFEITO


class TestContextoEvidencia(unittest.TestCase):
    def test_lista_e_efeito_permanecem_um_recorte_literal(self):
        texto = "6. INTERAÇÕES MEDICAMENTOSAS\n" + BLOCO + "\nMedicamento: fentanil\nEfeito da interação: efeito reduzido."
        pos = texto.index("linezolida")
        faixa = next((a, b) for a, b in trechos(texto) if a <= pos < b)
        a, b = recortar_evidencia(texto, faixa, (pos, pos + len("linezolida")))
        self.assertEqual(texto[a:b], BLOCO)
        self.assertNotIn("fentanil", texto[a:b])

    def test_lista_longa_nao_perde_o_efeito_pelo_limite_de_caracteres(self):
        texto = "Medicamentos: " + "substancia, " * 140 + "linezolida.\n" + EFEITO
        pos = texto.index("linezolida")
        self.assertEqual(recortar_evidencia(texto, (0, len(texto)), (pos, pos + 10)), (0, len(texto)))

    def test_completa_legado_sem_adivinhar_um_bloco_vizinho(self):
        texto = LISTA + "\nMedicamento: outro\nEfeito da interação: redução de efeito."
        self.assertEqual(completar_evidencia(texto, LISTA), LISTA)
        self.assertEqual(completar_evidencia(BLOCO, LISTA), BLOCO)
        self.assertEqual(completar_evidencia(BLOCO, BLOCO), BLOCO)
        self.assertEqual(efeito_do_bloco(BLOCO), EFEITO.split(": ", 1)[1])

    def test_mesma_lista_em_blocos_diferentes_e_ambigua(self):
        texto = BLOCO + "\n" + LISTA + "\nEfeitos da interação: outro efeito."
        self.assertEqual(completar_evidencia(texto, LISTA), LISTA)

    def test_aceita_rotulo_no_meio_da_linha_e_espacos_do_pdf(self):
        texto = LISTA + " Efeitos da  interação: pode aumentar a pressão arterial."
        self.assertEqual(blocos_estruturados(texto), [(0, len(texto))])
        self.assertEqual(completar_evidencia(texto, LISTA), texto)

    def test_nao_ultrapassa_proximo_topico_numerado(self):
        texto = BLOCO + "\n7. CUIDADOS DE ARMAZENAMENTO\nOutro texto."
        self.assertEqual(completar_evidencia(texto, LISTA), BLOCO)

    def test_prefixo_de_titulo_legado_e_preservado(self):
        titulo = "INTERAÇÕES MEDICAMENTOSAS\n"
        self.assertEqual(completar_evidencia(titulo + BLOCO, titulo + LISTA), titulo + BLOCO)

    def test_identificacao_grava_lista_e_efeito_para_o_alvo(self):
        from pipelines.identificacao_interacoes.tests.test_dicionario import servico
        from pipelines.identificacao_interacoes.tests.auxiliares import fonte
        resultado = servico(("linezolida", "Linezolida", "linezolida", None)).identificar(fonte(BLOCO))
        self.assertEqual(len(resultado.relacoes), 1)
        self.assertEqual(resultado.relacoes[0].alvo, "linezolida")
        self.assertEqual(resultado.relacoes[0].evidencia, BLOCO)

    def test_negacao_no_efeito_compartilhado_nao_cria_interacao(self):
        from pipelines.identificacao_interacoes.tests.test_dicionario import servico
        from pipelines.identificacao_interacoes.tests.auxiliares import fonte
        texto = "Medicamentos: linezolida; " + "outro, " * 80 + ".\nEfeitos da interação: não foram observadas interações."
        resultado = servico(("linezolida", "Linezolida", "linezolida", None)).identificar(fonte(texto))
        self.assertEqual(resultado.relacoes, [])
