import unittest

from identificacao_interacoes.erros import ErroSegmentacaoError
from identificacao_interacoes.normalizacao import (
    contem_termo,
    mapear_texto,
    normalizar_alvo,
    normalizar_nome,
)
from identificacao_interacoes.segmentacao import (
    conferir_cobertura,
    criar_janelas,
    criar_unidades,
    dividir_janela,
    segmentar,
)


def contar(texto: str) -> int:
    return len(texto.split())


class TestNormalizacao(unittest.TestCase):
    def test_reutiliza_regra_de_nome_normalizado_do_scraper(self) -> None:
        # planilha.py reexporta esta mesma função; importá-la exigiria openpyxl.
        from scraping_anvisa.anvisa_scraper.normalizacao import normalizar_nome as do_scraper

        self.assertIs(normalizar_nome, do_scraper)
        self.assertEqual(normalizar_alvo("  Ácido   Fólico® "), "acido folico")

    def test_mapa_recupera_posicoes_originais(self) -> None:
        original = "Evite  o uso\ncom   ÁLCOOL."
        mapa = mapear_texto(original)
        self.assertEqual(mapa.texto, "evite o uso com alcool.")
        inicio = mapa.texto.index("alcool")
        ini, fim = mapa.intervalo_original(inicio, inicio + len("alcool"))
        self.assertEqual(original[ini:fim], "ÁLCOOL")

    def test_hifenizacao_de_quebra_de_linha_e_opcional(self) -> None:
        original = "é metaboli-\nzado pelo fígado"
        self.assertIn("metaboli- zado", mapear_texto(original).texto)
        self.assertIn("metabolizado", mapear_texto(original, unir_hifenizacao=True).texto)

    def test_termo_inteiro_sem_substring(self) -> None:
        self.assertTrue(contem_termo("uso com omeprazol e sal", "omeprazol"))
        self.assertFalse(contem_termo("uso com esomeprazol", "omeprazol"))
        self.assertFalse(contem_termo("uso com omeprazolico", "omeprazol"))


class TestSegmentacao(unittest.TestCase):
    def test_frases_respeitam_abreviacoes_e_referencias(self) -> None:
        texto = (
            "Inibidores potentes (p. ex. cetoconazol) aumentam a exposição. "
            "Para mais dados, vide 3. CARACTERÍSTICAS FARMACOLÓGICAS. Doses de 2,5 mg são seguras."
        )
        frases = [texto[i:f] for i, f, _ in segmentar(texto)]
        self.assertEqual(frases[0], "Inibidores potentes (p. ex. cetoconazol) aumentam a exposição.")
        self.assertIn("vide 3. CARACTERÍSTICAS FARMACOLÓGICAS.", frases[1])
        self.assertEqual(frases[2], "Doses de 2,5 mg são seguras.")

    def test_quebra_de_linha_no_meio_da_frase_nao_divide(self) -> None:
        texto = "O uso com varfarina\npode aumentar o sangramento.\n6.1 Outros efeitos\nNada consta."
        frases = [(texto[i:f], titulo) for i, f, titulo in segmentar(texto)]
        self.assertEqual(frases[0], ("O uso com varfarina\npode aumentar o sangramento.", False))
        self.assertEqual(frases[1], ("6.1 Outros efeitos", True))

    def test_bloco_sem_pontuacao_e_dividido_sem_perder_texto(self) -> None:
        linhas = "\n".join(f"medicamento{i} risco" for i in range(400))
        unidades = segmentar(linhas, max_caracteres=300)
        self.assertGreater(len(unidades), 5)
        self.assertTrue(all(f - i <= 300 for i, f, _ in unidades))
        reconstruido = " ".join(linhas[i:f] for i, f, _ in unidades).split()
        self.assertEqual(reconstruido, linhas.split())

    def test_janelas_cobrem_todo_o_texto_com_sobreposicao(self) -> None:
        texto = " ".join(f"Frase número {i} sobre o medicamento." for i in range(60))
        unidades = criar_unidades(texto, contar)
        janelas = criar_janelas(texto, unidades, limite_tokens=40, sobreposicao_tokens=10)
        self.assertGreater(len(janelas), 3)
        conferir_cobertura(unidades, janelas)
        for anterior, seguinte in zip(janelas, janelas[1:]):
            self.assertLess(seguinte.unidades[0].indice, anterior.unidades[-1].indice + 1)
            self.assertLessEqual(seguinte.inicio, anterior.fim)
        self.assertEqual(janelas[-1].fim, len(texto.rstrip()))
        self.assertTrue(all(texto[j.inicio:j.fim] == j.texto for j in janelas))

    def test_janela_leva_titulo_da_subsecao_como_contexto(self) -> None:
        texto = "6.1 Efeito de outros medicamentos\n" + " ".join(
            f"Frase {i} com cetoconazol." for i in range(30)
        )
        unidades = criar_unidades(texto, contar)
        janelas = criar_janelas(texto, unidades, limite_tokens=30, sobreposicao_tokens=5)
        self.assertEqual(janelas[0].contexto_titulo, "")
        self.assertEqual(janelas[1].contexto_titulo, "6.1 Efeito de outros medicamentos")

    def test_cobertura_incompleta_e_detectada(self) -> None:
        texto = "Uma frase. Outra frase. Mais uma."
        unidades = criar_unidades(texto, contar)
        janelas = criar_janelas(texto, unidades[:2], limite_tokens=50, sobreposicao_tokens=0)
        with self.assertRaises(ErroSegmentacaoError):
            conferir_cobertura(unidades, janelas)

    def test_divisao_preserva_unidade_comum(self) -> None:
        texto = "A primeira. A segunda. A terceira. A quarta."
        unidades = criar_unidades(texto, contar)
        janela = criar_janelas(texto, unidades, 100, 0)[0]
        esquerda, direita = dividir_janela(texto, janela, unidades)
        self.assertEqual(esquerda.unidades[-1].indice, direita.unidades[0].indice)
        unica = criar_janelas("Só uma.", criar_unidades("Só uma.", contar), 100, 0)[0]
        with self.assertRaises(ErroSegmentacaoError):
            dividir_janela("Só uma.", unica, unica.unidades)


if __name__ == "__main__":
    unittest.main()
