"""Regressões dos cards pendentes: formatação, escopo e evidência insuficiente."""

import json
from types import SimpleNamespace
import unittest

from pipelines.resumos_interacoes.contexto import RevisaoDaEvidencia, recuperar_secao, validar_entrada
from pipelines.resumos_interacoes.validacao import validar_fundamentacao, validar_resposta


def resposta(texto):
    return SimpleNamespace(conteudo=json.dumps({"texto": texto}), truncada=False)


class TestContextoResumos(unittest.TestCase):
    def test_rash_e_clearance_aceitam_variantes_de_aspas(self):
        for palavra, frase in [
            ("rash", "Ampicilina com alopurinol pode aumentar a frequência de {} cutâneo."),
            ("clearance", "Cetoconazol com zolpidem pode diminuir o {} do zolpidem."),
        ]:
            fonte = frase.format(f"“{palavra}”")
            for aspas in ["'", '"', "‘"]:
                fechamento = "’" if aspas == "‘" else aspas
                texto = frase.format(aspas + palavra + fechamento)
                with self.subTest(palavra=palavra, aspas=aspas):
                    self.assertEqual(validar_resposta(resposta(texto), fonte), texto)

    def test_citacao_inventada_bloqueada_mesmo_quando_fonte_tem_aspas(self):
        with self.assertRaisesRegex(ValueError, "aspas"):
            validar_resposta(resposta("Ampicilina com alopurinol provoca 'efeito irreversível'."),
                             "Ampicilina com alopurinol aumenta a frequência de “rash” cutâneo.")

    def test_negacao_especifica_preserva_escopo_sem_liberar_seguranca(self):
        fontes = [
            "As concentrações plasmáticas de lacosamida não foram afetadas pela carbamazepina.",
            "A farmacocinética de pemetrexede não é influenciada pelo ácido fólico.",
            "Clonazepam: sem influência na concentração de perampanel.",
        ]
        textos = [
            "Carbamazepina não afetou as concentrações plasmáticas de lacosamida.",
            "Ácido fólico não alterou a farmacocinética de pemetrexede.",
            "Clonazepam não afetou a concentração de perampanel.",
        ]
        for fonte, texto in zip(fontes, textos):
            with self.subTest(fonte=fonte):
                validar_fundamentacao(texto, fonte)
                for extrapolacao in [" Não houve interação entre eles.", " A associação é segura.",
                                     " A associação é sem riscos.", " Não há estudos dessa interação."]:
                    with self.assertRaises(ValueError):
                        validar_fundamentacao(texto + extrapolacao, fonte)

    def test_nao_libera_negacao_quando_fonte_descreve_aumento(self):
        with self.assertRaises(ValueError):
            validar_fundamentacao("Claritromicina não afetou a concentração de sirolimo.",
                                   "Claritromicina aumenta a concentração de sirolimo.")

    def test_recupera_sirolimo_sem_sulfonilureias(self):
        secao = ("sirolimo: fluconazol aumenta as concentrações plasmáticas de sirolimo por inibição do metabolismo. "
                 "Esta combinação pode ser usada com ajuste da dose de sirolimo\n"
                 "dependendo das determinações de efeito/concentração.")
        vizinha = "sulfonilureias: fluconazol prolonga a meia-vida sérica de sulfonilureias."
        original = "Esta combinação pode ser usada com ajuste da dose de sirolimo\ndependendo das determinações de efeito/concentração.\n" + vizinha
        completo = secao + "\n" + vizinha + "\ntacrolimo: outro efeito."
        recuperada = recuperar_secao(completo, original, "sirolimo")
        self.assertEqual(recuperada, secao)
        self.assertIn(recuperada, completo)
        self.assertNotIn("sulfonilureias", recuperada)

    def test_nao_recupera_ocorrencia_ambigua_ou_sem_rotulo(self):
        evidencia = "O uso concomitante aumenta a concentração de sirolimo."
        for fonte in ["sirolimo: " + evidencia + "\nOutra ocorrência: " + evidencia,
                      evidencia, "Uma bula diferente que não contém o recorte."]:
            self.assertEqual(recuperar_secao(fonte, evidencia, "sirolimo"), evidencia)

    def test_nome_corrompido_ou_incompleto_pede_revisao(self):
        for alvo in ["tenofovir ↔ ↔ alafenamida", "vacina sarampo, caxumba, rubéola (atenuada"]:
            with self.subTest(alvo=alvo), self.assertRaisesRegex(RevisaoDaEvidencia, "nome do alvo"):
                validar_entrada({"alvo": alvo, "evidencia": "O uso concomitante pode aumentar o efeito."}, "bula")

    def test_componente_da_propria_bula_nao_vira_par_de_medicamentos(self):
        with self.assertRaisesRegex(RevisaoDaEvidencia, "componente"):
            validar_entrada({"alvo": "ferripolimaltose", "evidencia":
                             "Dexfer® (ferripolimaltose) não sofre diminuição de sua absorção."}, "dexfer")

    def test_lista_isolada_nao_aciona_llm(self):
        with self.assertRaisesRegex(RevisaoDaEvidencia, "não descreve o efeito"):
            validar_entrada({"alvo": "budesonida", "evidencia":
                             "budesonida, ciclesonida,\n       ciclosporina,\n dexametasona,"}, "cetomicoss")

    def test_tabela_com_efeitos_em_duas_direcoes_precisa_revisao(self):
        tabela = ("DAE          Influência na concentração        Influência na concentração do DAE\n"
                  "coadministrada\ncarbamazepina    Diminuição em 3 vezes      Diminuição <10%\n"
                  "clobazam          Sem influência              Diminuição <10%")
        with self.assertRaisesRegex(RevisaoDaEvidencia, "tabela"):
            validar_entrada({"alvo": "clobazam", "evidencia": tabela}, "fycompa")

    def test_lista_com_efeito_e_condicao_e_aceita(self):
        validar_entrada({"alvo": "fluoxetina", "evidencia":
                         "Risco aumentado de sangramento: citalopram, fluoxetina, paroxetina e sertralina."}, "somalgin cardio")


if __name__ == "__main__":
    unittest.main()
