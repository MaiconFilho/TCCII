import unittest

from pipelines.identificacao_interacoes.dicionario import (
    JANELA_EVIDENCIA,
    MAX_EVIDENCIA,
    MAX_EVIDENCIAS_POR_ALVO,
    ServicoDicionario,
    carregar_ignorados,
    resumir,
    trechos,
)
from pipelines.identificacao_interacoes.modelos import CriterioVinculo, MotivoRejeicao, StatusProcessamento, TipoAlvo
from pipelines.identificacao_interacoes.resolucao import CatalogoMedicamentos, MedicamentoCatalogo
from pipelines.identificacao_interacoes.tests.auxiliares import fonte


CADASTRO = (
    ("marevan", "MAREVAN", "varfarina sódica", None),
    ("varfarina generico", "Varfarina", "varfarina", None),
    ("aas", "AAS", "ácido acetilsalicílico", None),
    ("cordarex", "CORDAREX", "cloridrato de amiodarona", None),
    ("digoxina", "DIGOXINA", "digoxina", None),
    ("hidrocortisona", "HIDROCORTISONA", "hidrocortisona", None),
    ("litocit", "LITOCIT", "citrato de potássio", None),
    ("calcio d", "CÁLCIO D", "carbonato de cálcio", None),
    ("limiar", "LIMIAR", "pregabalina", None),
    ("lurasidona", "LURASIDONA", "cloridrato de lurasidona", None),
    ("lur", "LUR", "cloridrato de lurasidona", None),
    ("pantoprazol", "PANTOPRAZOL", "pantoprazol sódico sesqui-hidratado", None),
    ("produto teste", "PRODUTO TESTE", "substancia teste", None),
)


def servico(*extras, ignorados=frozenset({"limiar"})):
    catalogo = CatalogoMedicamentos(MedicamentoCatalogo(*m) for m in (*CADASTRO, *extras))
    return ServicoDicionario(catalogo, ignorados=ignorados)


def alvos(resultado):
    return [(r.alvo, r.tipo_alvo) for r in resultado.relacoes]


class TestBusca(unittest.TestCase):
    def test_principio_sem_sal_vincula_com_criterio_proprio_e_evidencia_literal(self) -> None:
        texto = "6. INTERAÇÕES MEDICAMENTOSAS\n- Varfarina: aumento do risco de sangramento."
        s = servico()
        resultado = s.identificar(fonte(texto))
        self.assertEqual(resultado.status, StatusProcessamento.CONCLUIDO_COM_RELACOES)
        self.assertEqual(alvos(resultado), [("Varfarina", TipoAlvo.PRINCIPIO_ATIVO)])
        relacao = resultado.relacoes[0]
        self.assertEqual(texto[relacao.inicio_evidencia:relacao.fim_evidencia], relacao.evidencia)
        self.assertEqual(relacao.evidencia, "- Varfarina: aumento do risco de sangramento.")
        self.assertEqual(relacao.resumo, "aumento do risco de sangramento.")
        criterios = {
            v.medicamento_relacionado: v.criterio
            for v in s.catalogo.resolver(relacao.alvo, relacao.tipo_alvo, "produto teste")
        }
        self.assertEqual(criterios, {
            "marevan": CriterioVinculo.PRINCIPIO_ATIVO_SEM_SAL,
            "varfarina generico": CriterioVinculo.PRINCIPIO_ATIVO_EXATO,
        })

    def test_nome_mais_longo_e_quebra_de_linha_com_hifen(self) -> None:
        texto = "O uso com ácido acetil-\nsalicílico e com cloridrato de amiodarona exige cautela."
        self.assertEqual(alvos(servico().identificar(fonte(texto))), [
            ("ácido acetilsalicílico", TipoAlvo.PRINCIPIO_ATIVO),
            ("cloridrato de amiodarona", TipoAlvo.PRINCIPIO_ATIVO),
        ])

    def test_nome_comercial_e_sigla_curta_so_em_maiusculas(self) -> None:
        texto = "O uso com Marevan® aumenta o efeito. Evite AAS. Use um aas qualquer."
        self.assertEqual(alvos(servico().identificar(fonte(texto))), [
            ("Marevan", TipoAlvo.MEDICAMENTO),
            ("AAS", TipoAlvo.MEDICAMENTO),
        ])

    def test_sem_nome_do_banco_conclui_sem_relacoes(self) -> None:
        resultado = servico().identificar(fonte("Anticoagulantes e alimentos ricos em vitamina K."))
        self.assertEqual(resultado.status, StatusProcessamento.CONCLUIDO_SEM_RELACOES)
        self.assertEqual(resultado.relacoes, [])
        self.assertEqual(resultado.quantidade_janelas, resultado.janelas_concluidas)

    def test_texto_vazio_e_falha(self) -> None:
        self.assertEqual(servico().identificar(fonte("  ")).status, StatusProcessamento.FALHA)


class TestDescartes(unittest.TestCase):
    def motivos(self, resultado):
        return {(r.alvo, r.motivo) for r in resultado.rejeicoes}

    def test_origem_tambem_sem_sal_e_por_outro_nome(self) -> None:
        texto = "A varfarina e o Marevan aumentam o efeito da digoxina."
        resultado = servico().identificar(
            fonte(texto, nome_normalizado="marevan", nome_exibicao="MAREVAN", principios_ativos="varfarina sódica")
        )
        self.assertEqual(alvos(resultado), [("digoxina", TipoAlvo.PRINCIPIO_ATIVO)])
        self.assertIn(("varfarina", MotivoRejeicao.ALVO_E_ORIGEM), self.motivos(resultado))

    def test_sigla_de_outra_marca_da_mesma_substancia(self) -> None:
        texto = "Medicamento LUR Cmáx AUC cetoconazol aumentou a exposição. Digoxina: sem ajuste de dose."
        resultado = servico().identificar(fonte(
            texto, nome_normalizado="lurasidona", nome_exibicao="LURASIDONA",
            principios_ativos="cloridrato de lurasidona",
        ))
        self.assertEqual(alvos(resultado), [("Digoxina", TipoAlvo.PRINCIPIO_ATIVO)])
        self.assertIn(("lur", MotivoRejeicao.ALVO_E_ORIGEM), self.motivos(resultado))

    def test_enantiomero_ou_racemato_da_origem(self) -> None:
        extras = (
            ("zyxem", "ZYXEM", "dicloridrato de levocetirizina", None),
            ("zyrtec", "ZYRTEC", "dicloridrato de cetirizina", None),
        )
        texto = "Estudos com cetirizina mostraram aumento da exposição com digoxina."
        resultado = servico(*extras).identificar(fonte(
            texto, nome_normalizado="zyxem", nome_exibicao="ZYXEM",
            principios_ativos="dicloridrato de levocetirizina",
        ))
        self.assertEqual(alvos(resultado), [("digoxina", TipoAlvo.PRINCIPIO_ATIVO)])
        self.assertIn(("cetirizina", MotivoRejeicao.ALVO_E_ORIGEM), self.motivos(resultado))

    def test_oracao_de_ausencia_nao_afeta_a_outra_oracao(self) -> None:
        texto = (
            "Não foram observadas interações com a digoxina; a amiodarona aumenta os níveis da origem. "
            "A varfarina não alterou a farmacocinética da origem."
        )
        resultado = servico().identificar(fonte(texto))
        self.assertEqual(alvos(resultado), [("amiodarona", TipoAlvo.PRINCIPIO_ATIVO)])
        self.assertIn(("digoxina", MotivoRejeicao.RELACAO_NAO_POSITIVA), self.motivos(resultado))
        self.assertIn(("varfarina", MotivoRejeicao.RELACAO_NAO_POSITIVA), self.motivos(resultado))

    def test_formas_de_ausencia_observadas_na_base(self) -> None:
        for frase in (
            "O produto não teve efeito clinicamente relevante sobre a digoxina.",
            "O produto e seus metabólitos não inibem o metabolismo da digoxina.",
            "Estudos de interação com digoxina não foram conduzidos.",
            "Macrolídeos: digoxina não estudado.",
            "Não há na literatura informações sobre o uso com digoxina.",
            "Estudos demonstraram que não havia interações clinicamente relevantes com antipirina, "
            "pseudoefedrina, eritromicina, azitromicina, glipizida e digoxina.",
        ):
            self.assertEqual(alvos(servico().identificar(fonte(frase))), [], frase)

    def test_excecao_nao_e_alvo(self) -> None:
        texto = (
            "Glicocorticoides sistêmicos, exceto hidrocortisona usada como terapia de reposição: "
            "risco aumentado com varfarina. Esomeprazol (mas não pantoprazol) inibe a digoxina."
        )
        resultado = servico().identificar(fonte(texto))
        self.assertEqual(alvos(resultado), [
            ("varfarina", TipoAlvo.PRINCIPIO_ATIVO), ("digoxina", TipoAlvo.PRINCIPIO_ATIVO),
        ])
        self.assertIn(("hidrocortisona", MotivoRejeicao.RELACAO_NAO_POSITIVA), self.motivos(resultado))
        self.assertIn(("pantoprazol", MotivoRejeicao.RELACAO_NAO_POSITIVA), self.motivos(resultado))

    def test_mineral_como_medida_ou_mecanismo_e_ignorado(self) -> None:
        for frase in (
            "Pode aumentar os níveis séricos de potássio.",
            "Use com cautela com diuréticos poupadores de potássio.",
            "Diminuição do cálcio sérico.",
        ):
            self.assertEqual(alvos(servico().identificar(fonte(frase))), [], frase)
        resultado = servico().identificar(fonte("Suplementos de potássio aumentam o risco de hipercalemia."))
        self.assertEqual(alvos(resultado), [("potássio", TipoAlvo.PRINCIPIO_ATIVO)])

    def test_termo_ignorado_e_aviso_de_excipientes(self) -> None:
        texto = "O limiar convulsivo pode diminuir. Atenção: contém carbonato de cálcio e varfarina."
        resultado = servico().identificar(fonte(texto))
        self.assertEqual(alvos(resultado), [])
        self.assertIn(("limiar", MotivoRejeicao.ALVO_GENERICO), self.motivos(resultado))

    def test_nome_so_da_propria_origem_no_banco_nao_vira_relacao(self) -> None:
        resultado = servico(("exclusivo", "EXCLUSIVO", "substancia rara", None)).identificar(fonte(
            "A substancia rara aumenta a toxicidade.",
            nome_normalizado="exclusivo", nome_exibicao="EXCLUSIVO", principios_ativos="outra coisa",
        ))
        self.assertEqual(alvos(resultado), [])
        self.assertIn(("substancia rara", MotivoRejeicao.ALVO_INVALIDO), self.motivos(resultado))


class TestEvidencias(unittest.TestCase):
    def test_trecho_repetido_vira_uma_relacao_e_ha_limite_por_nome(self) -> None:
        item = "- Digoxina: aumento da concentração plasmática."
        repetido = servico().identificar(fonte(f"{item}\n\n{item}"))
        self.assertEqual(len(repetido.relacoes), 1)
        frases = " ".join(f"A digoxina tem o efeito {i} alterado." for i in range(MAX_EVIDENCIAS_POR_ALVO + 2))
        varias = servico().identificar(fonte(frases))
        self.assertEqual(len(varias.relacoes), MAX_EVIDENCIAS_POR_ALVO)
        self.assertTrue(any("não foram gravadas" in aviso for aviso in varias.avisos))

    def test_lista_longa_mantem_o_efeito_do_comeco_da_frase(self) -> None:
        lista = (
            "A atividade da varfarina pode ser potencializada por "
            + ", ".join(f"substancia{i}" for i in range(60)) + ", digoxina e "
            + ", ".join(f"outra{i}" for i in range(80)) + "."
        )
        self.assertGreater(len(lista), MAX_EVIDENCIA)
        relacao = next(r for r in servico().identificar(fonte(lista)).relacoes if r.alvo == "digoxina")
        self.assertTrue(relacao.evidencia.startswith("A atividade da varfarina pode ser potencializada"))
        self.assertLessEqual(len(relacao.evidencia), MAX_EVIDENCIA)
        self.assertEqual(lista[relacao.inicio_evidencia:relacao.fim_evidencia], relacao.evidencia)

    def test_nome_longe_do_comeco_vira_janela_literal(self) -> None:
        tabela = " ".join(["Cmáx 1,2 AUC 3,4 sem ajuste"] * 60) + " digoxina aumenta " + " ".join(["coluna"] * 120)
        relacao = servico().identificar(fonte(tabela)).relacoes[0]
        self.assertLessEqual(len(relacao.evidencia), JANELA_EVIDENCIA)
        self.assertIn("digoxina", relacao.evidencia)
        self.assertEqual(tabela[relacao.inicio_evidencia:relacao.fim_evidencia], relacao.evidencia)

    def test_trechos_e_resumo(self) -> None:
        texto = "Primeira frase. Segunda frase;\n- item um\n• item dois\n\nNova parte"
        self.assertEqual(
            [texto[a:b] for a, b in trechos(texto)],
            ["Primeira frase.", "Segunda frase;", "- item um", "• item dois", "Nova parte"],
        )
        self.assertEqual(resumir("Varfarina: aumento do efeito.", 0), "aumento do efeito.")
        self.assertEqual(resumir("Aumenta o efeito: da varfarina.", 20), "Aumenta o efeito: da varfarina.")
        self.assertTrue(resumir("x " * 400, 0).endswith("…"))

    def test_versao_muda_com_ignorados_e_com_o_banco(self) -> None:
        base = servico().versao_processamento
        self.assertTrue(base.startswith("dicionario/1#"))
        self.assertEqual(base, servico().versao_processamento)
        self.assertNotEqual(base, servico(ignorados=frozenset()).versao_processamento)
        self.assertNotEqual(base, servico(("novo", "NOVO", "femprocumona", None)).versao_processamento)

    def test_lista_de_ignorados_do_projeto(self) -> None:
        ignorados = carregar_ignorados()
        for termo in ("limiar", "glicose", "rdc 60 12", "agua para injecao q s p", "cho"):
            self.assertIn(termo, ignorados)
        self.assertNotIn("", ignorados)


if __name__ == "__main__":
    unittest.main()
