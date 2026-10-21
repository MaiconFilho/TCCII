import unittest

from pipelines.identificacao_interacoes.composicao import equivalencias_declaradas
from pipelines.identificacao_interacoes.modelos import (
    CriterioVinculo,
    MotivoRejeicao,
    RelacaoIdentificada,
    Rejeicao,
    TipoAlvo,
)
from pipelines.identificacao_interacoes.resolucao import CatalogoMedicamentos, MedicamentoCatalogo, forma_sem_sal
from pipelines.identificacao_interacoes.validacao import (
    ContextoValidacao,
    chaves_da_origem,
    consolidar,
    validar_alvo,
)


TEXTO = """6. INTERAÇÕES MEDICAMENTOSAS
Não foram realizados estudos de interação com digoxina.
Não foram observadas interações clinicamente relevantes com propranolol.
Anticonvulsivantes, como a carbamazepina e a fenitoína, reduzem os níveis séricos de biotina.
Doses de paracetamol acima de 2 g por dia não devem ser usadas com PRODUTO TESTE.
A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina.
A solução por via intravenosa não deve ser misturada com heparina.
Não se sabe se o uso com álcool aumenta o risco de hepatotoxicidade.
Macrolídeos: azitromicina não estudado.
Saladas verdes e outros alimentos ricos em vitamina K reduzem o efeito da varfarina.
O uso com cetoconazol aumenta a exposição, sem necessidade de ajuste de dose.
A finasterida não parece afetar significativamente o sistema.
O produto não parece afetar a teofilina.
A solução pode potencializar os efeitos de sedativos, não devendo ser administrada com eles.
É provável que os inibidores do CYP3A4 afetem a concentração plasmática do produto."""


def contexto() -> ContextoValidacao:
    return ContextoValidacao(
        chaves_origem=chaves_da_origem("produto teste", "PRODUTO TESTE", "biotina; substancia teste"),
        texto=TEXTO,
    )


def validar(alvo, tipo, resumo, frase):
    """A evidência é a frase indicada, localizada no texto pelo teste."""
    inicio = TEXTO.index(frase) if frase in TEXTO else len(TEXTO)
    return validar_alvo(
        contexto(), alvo=alvo, tipo_alvo=TipoAlvo(tipo), resumo=resumo,
        inicio_evidencia=inicio, fim_evidencia=inicio + len(frase), indice_janela=0,
    )


class TestValidacaoSemantica(unittest.TestCase):
    def test_relacao_fiel_e_aceita_com_passagem_original(self) -> None:
        relacao = validar(
            "varfarina", "PRINCIPIO_ATIVO",
            "Pode ter o efeito aumentado; monitorar o tempo de protrombina.",
            "A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina.",
        )
        self.assertIsInstance(relacao, RelacaoIdentificada)
        self.assertEqual(
            relacao.evidencia,
            "A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina.",
        )
        self.assertEqual(TEXTO[relacao.inicio_evidencia:relacao.fim_evidencia], relacao.evidencia)

    def test_ausencia_de_estudos_e_de_interacao_nao_sao_relacoes(self) -> None:
        for alvo, citacao in (
            ("digoxina", "Não foram realizados estudos de interação com digoxina."),
            ("propranolol", "Não foram observadas interações clinicamente relevantes com propranolol."),
            ("álcool", "Não se sabe se o uso com álcool aumenta o risco de hepatotoxicidade."),
            ("azitromicina", "Macrolídeos: azitromicina não estudado."),
        ):
            with self.subTest(alvo=alvo):
                rejeicao = validar(alvo, "PRINCIPIO_ATIVO", "Interage com o produto.", citacao)
                self.assertIsInstance(rejeicao, Rejeicao)
                self.assertEqual(rejeicao.motivo, MotivoRejeicao.RELACAO_NAO_POSITIVA)

    def test_negacao_omitida_no_resumo(self) -> None:
        rejeicao = validar(
            "paracetamol", "PRINCIPIO_ATIVO",
            "Doses de paracetamol acima de 2 g por dia devem ser usadas com cautela.",
            "Doses de paracetamol acima de 2 g por dia não devem ser usadas com PRODUTO TESTE.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_OMITE_NEGACAO)

    def test_dose_omitida_no_resumo(self) -> None:
        rejeicao = validar(
            "paracetamol", "PRINCIPIO_ATIVO",
            "O paracetamol não deve ser usado em conjunto.",
            "Doses de paracetamol acima de 2 g por dia não devem ser usadas com PRODUTO TESTE.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_OMITE_QUANTIDADE)
        aceita = validar(
            "paracetamol", "PRINCIPIO_ATIVO",
            "Doses de paracetamol acima de 2 g por dia não devem ser usadas em conjunto.",
            "Doses de paracetamol acima de 2 g por dia não devem ser usadas com PRODUTO TESTE.",
        )
        self.assertIsInstance(aceita, RelacaoIdentificada)

    def test_via_omitida_no_resumo(self) -> None:
        rejeicao = validar(
            "heparina", "PRINCIPIO_ATIVO", "Não deve ser misturada com heparina.",
            "A solução por via intravenosa não deve ser misturada com heparina.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_OMITE_VIA)

    def test_gravidade_inventada(self) -> None:
        rejeicao = validar(
            "varfarina", "PRINCIPIO_ATIVO",
            "Interação grave: efeito aumentado; monitorar o tempo de protrombina.",
            "A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_INVENTA_GRAVIDADE)

    def test_resumo_sem_apoio_na_propria_evidencia(self) -> None:
        rejeicao = validar(
            "varfarina", "PRINCIPIO_ATIVO",
            "Anticonvulsivantes alteram os níveis séricos de biotina.",
            "A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_SEM_APOIO_NA_EVIDENCIA)

    def test_condicao_negativa_secundaria_deve_aparecer(self) -> None:
        citacao = "O uso com cetoconazol aumenta a exposição, sem necessidade de ajuste de dose."
        rejeicao = validar("cetoconazol", "PRINCIPIO_ATIVO", "Aumenta a exposição.", citacao)
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_OMITE_NEGACAO)
        aceita = validar(
            "cetoconazol", "PRINCIPIO_ATIVO",
            "Aumenta a exposição, sem necessidade de ajuste de dose.", citacao,
        )
        self.assertIsInstance(aceita, RelacaoIdentificada)

    def test_nao_parece_afetar_e_ausencia(self) -> None:
        rejeicao = validar(
            "teofilina", "PRINCIPIO_ATIVO", "Não afeta a teofilina.",
            "O produto não parece afetar a teofilina.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RELACAO_NAO_POSITIVA)

    def test_origem_com_sal_no_cadastro_tambem_e_origem(self) -> None:
        texto = "A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina."
        contexto_marevan = ContextoValidacao(
            chaves_origem=chaves_da_origem("marevan", "MAREVAN", "varfarina sódica"), texto=texto
        )
        rejeicao = validar_alvo(
            contexto_marevan, alvo="varfarina", tipo_alvo=TipoAlvo.PRINCIPIO_ATIVO,
            resumo="Pode ter o efeito aumentado.", inicio_evidencia=0, fim_evidencia=len(texto),
            indice_janela=0,
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.ALVO_E_ORIGEM)

    def test_alvo_generico_ou_fisiologico(self) -> None:
        for alvo, tipo in (("sistema", "OUTRA_SUBSTANCIA"), ("drogas", "PRINCIPIO_ATIVO"),
                           ("mucosa gastrintestinal", "GRUPO_ALIMENTAR"), ("doses anti-inflamatórias", "PRINCIPIO_ATIVO")):
            rejeicao = validar(alvo, tipo, "Não afeta significativamente o sistema.",
                               "A finasterida não parece afetar significativamente o sistema.")
            self.assertEqual(rejeicao.motivo, MotivoRejeicao.ALVO_GENERICO, alvo)
        self.assertFalse(Rejeicao(MotivoRejeicao.ALVO_GENERICO, "", "", "", "", "", 0).exige_revisao)

    def test_negacao_omitida_indica_o_trecho_ao_modelo(self) -> None:
        rejeicao = validar(
            "sedativos", "CLASSE_FARMACOLOGICA", "Pode potencializar os efeitos de sedativos.",
            "A solução pode potencializar os efeitos de sedativos, não devendo ser administrada com eles.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_OMITE_NEGACAO)
        self.assertIn('"não devendo ser administrada com eles"', rejeicao.detalhe)

    def test_direcao_de_efeito_inventada(self) -> None:
        frase = "É provável que os inibidores do CYP3A4 afetem a concentração plasmática do produto."
        rejeicao = validar(
            "inibidores do CYP3A4", "CLASSE_FARMACOLOGICA", "Aumentam a concentração plasmática.", frase
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.RESUMO_INVENTA_EFEITO)
        aceita = validar(
            "inibidores do CYP3A4", "CLASSE_FARMACOLOGICA",
            "Provavelmente afetam a concentração plasmática.", frase,
        )
        self.assertIsInstance(aceita, RelacaoIdentificada)

    def test_evidencia_fora_do_trecho(self) -> None:
        rejeicao = validar(
            "varfarina", "PRINCIPIO_ATIVO", "Aumenta o sangramento.",
            "A varfarina aumenta muito o risco de sangramento grave.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA)

    def test_alvo_que_nao_aparece_na_evidencia(self) -> None:
        rejeicao = validar(
            "fenobarbital", "PRINCIPIO_ATIVO", "Reduzem os níveis séricos de biotina.",
            "Anticonvulsivantes, como a carbamazepina e a fenitoína, reduzem os níveis séricos de biotina.",
        )
        self.assertEqual(rejeicao.motivo, MotivoRejeicao.ALVO_AUSENTE_NA_EVIDENCIA)

    def test_origem_nunca_e_alvo(self) -> None:
        for alvo in ("Produto Teste", "biotina"):
            rejeicao = validar(
                alvo, "MEDICAMENTO", "Reduzem os níveis séricos de biotina.",
                "Anticonvulsivantes, como a carbamazepina e a fenitoína, reduzem os níveis séricos de biotina.",
            )
            self.assertEqual(rejeicao.motivo, MotivoRejeicao.ALVO_E_ORIGEM)

    def test_frase_com_classe_exemplos_e_alimentos(self) -> None:
        citacao = "Anticonvulsivantes, como a carbamazepina e a fenitoína, reduzem os níveis séricos de biotina."
        resumo = "Reduzem os níveis séricos de biotina."
        for alvo, tipo in (
            ("anticonvulsivantes", "CLASSE_FARMACOLOGICA"),
            ("carbamazepina", "PRINCIPIO_ATIVO"),
            ("fenitoína", "PRINCIPIO_ATIVO"),
        ):
            self.assertIsInstance(validar(alvo, tipo, resumo, citacao), RelacaoIdentificada)
        alimento = validar(
            "saladas verdes", "GRUPO_ALIMENTAR", "Saladas verdes reduzem o efeito da varfarina.",
            "Saladas verdes e outros alimentos ricos em vitamina K reduzem o efeito da varfarina.",
        )
        self.assertIsInstance(alimento, RelacaoIdentificada)


class TestConsolidacao(unittest.TestCase):
    def _relacao(self, alvo, inicio, fim, evidencia="A varfarina aumenta.", tipo=TipoAlvo.PRINCIPIO_ATIVO):
        return RelacaoIdentificada(alvo, tipo, "resumo", evidencia, inicio, fim, 0)

    def test_repeticoes_da_sobreposicao_sao_consolidadas(self) -> None:
        relacoes, descartadas = consolidar([
            self._relacao("Varfarina", 10, 30),
            self._relacao("varfarina", 10, 30),
            self._relacao("varfarina", 5, 40, "Frase maior. A varfarina aumenta."),
        ])
        self.assertEqual(len(relacoes), 1)
        self.assertEqual(descartadas, 2)
        self.assertEqual(relacoes[0].inicio_evidencia, 5)

    def test_evidencias_e_tipos_distintos_sao_preservados(self) -> None:
        relacoes, descartadas = consolidar([
            self._relacao("varfarina", 10, 30),
            self._relacao("varfarina", 50, 70, "A varfarina diminui."),
            self._relacao("varfarina", 10, 30, tipo=TipoAlvo.MEDICAMENTO),
        ])
        self.assertEqual((len(relacoes), descartadas), (3, 0))


def catalogo(*medicamentos) -> CatalogoMedicamentos:
    return CatalogoMedicamentos(MedicamentoCatalogo(*m) for m in medicamentos)


class TestResolucao(unittest.TestCase):
    def setUp(self) -> None:
        self.catalogo = catalogo(
            ("varfarina generico", "Varfarina Genérico", "varfarina", None),
            ("marevan", "MAREVAN", "varfarina sódica", None),
            ("omeprazol a", "Omeprazol A", "omeprazol", None),
            ("omeprazol b", "Omeprazol B", "Omeprazol (pellets)", None),
            ("nexium", "NEXIUM", "esomeprazol magnésico tri-hidratado", None),
            ("amoxil", "AMOXIL", "amoxicilina (sob a forma tri-hidratada)", None),
            ("anestesico", "ANESTÉSICO", "Cloridrato de Articaína; Epinefrina Base", None),
            ("produto teste", "PRODUTO TESTE", "varfarina", None),
            (
                "roxetin", "Roxetin", "cloridrato de paroxetina",
                "COMPOSIÇÃO\nCada comprimido contém:\ncloridrato de paroxetina ........ 22,214 mg\n"
                "(equivalente a 20 mg de paroxetina base)\nexcipientes q.s.p. ........ 1 comprimido\n"
                "Excipientes: lactose monoidratada, estearato de magnésio.\nEste medicamento não contém glúten.",
            ),
            (
                "outro paroxetina", "Outro", "cloridrato de paroxetina",
                "COMPOSIÇÃO\ncloridrato de paroxetina ........ 20 mg\nexcipientes: paroxetina? não",
            ),
        )

    def nomes(self, alvo, tipo=TipoAlvo.PRINCIPIO_ATIVO, origem="produto teste"):
        return [v.medicamento_relacionado for v in self.catalogo.resolver(alvo, tipo, origem)]

    def criterios(self, alvo, origem="produto teste"):
        return {
            v.medicamento_relacionado: v.criterio
            for v in self.catalogo.resolver(alvo, TipoAlvo.PRINCIPIO_ATIVO, origem)
        }

    def test_correspondencia_exata_e_varios_medicamentos(self) -> None:
        self.assertEqual(self.nomes("Omeprazol"), ["omeprazol a", "omeprazol b"])
        self.assertEqual(
            self.nomes("varfarina", origem="outra origem"), ["marevan", "produto teste", "varfarina generico"]
        )

    def test_origem_nao_e_vinculada_a_si_mesma(self) -> None:
        self.assertEqual(self.nomes("varfarina"), ["marevan", "varfarina generico"])

    def test_sal_e_hidrato_vinculam_com_criterio_proprio(self) -> None:
        self.assertEqual(self.criterios("varfarina"), {
            "marevan": CriterioVinculo.PRINCIPIO_ATIVO_SEM_SAL,
            "varfarina generico": CriterioVinculo.PRINCIPIO_ATIVO_EXATO,
        })
        # Nos dois sentidos: o texto com sal também encontra o cadastro sem sal.
        self.assertEqual(self.criterios("varfarina sódica"), {
            "marevan": CriterioVinculo.PRINCIPIO_ATIVO_EXATO,
            "varfarina generico": CriterioVinculo.PRINCIPIO_ATIVO_SEM_SAL,
        })
        self.assertEqual(self.nomes("esomeprazol"), ["nexium"])
        self.assertEqual(self.nomes("amoxicilina tri-hidratada"), ["amoxil"])
        self.assertEqual(self.nomes("articaína"), ["anestesico"])

    def test_nomes_semelhantes_e_anions_nao_correspondem(self) -> None:
        self.assertNotIn("nexium", self.nomes("omeprazol"))
        self.assertNotIn("omeprazol a", self.nomes("esomeprazol"))
        self.assertEqual(self.nomes("prazol"), [])
        self.assertEqual(forma_sem_sal("cloreto de sodio"), "cloreto de sodio")
        self.assertEqual(forma_sem_sal("bicarbonato de sodio"), "bicarbonato de sodio")
        self.assertEqual(forma_sem_sal("cloridrato de tri-hexifenidil"), "tri hexifenidil")
        self.assertEqual(forma_sem_sal("esomeprazol magnesico tri-hidratado"), "esomeprazol")
        self.assertEqual(forma_sem_sal("bissulfato de clopidogrel"), "clopidogrel")
        self.assertEqual(forma_sem_sal("carbonato de litio"), "litio")

    def test_anotacao_e_sufixo_base_da_propria_entrada(self) -> None:
        self.assertEqual(self.nomes("amoxicilina"), ["amoxil"])
        self.assertEqual(self.nomes("epinefrina"), ["anestesico"])

    def test_nome_comercial_exato(self) -> None:
        self.assertEqual(self.nomes("Marevan®", TipoAlvo.MEDICAMENTO), ["marevan"])
        self.assertEqual(self.nomes("Mare", TipoAlvo.MEDICAMENTO), [])
        # Nome comercial não é procurado entre princípios ativos, e vice-versa.
        self.assertEqual(self.nomes("marevan", TipoAlvo.PRINCIPIO_ATIVO), [])

    def test_classes_alimentos_e_outros_nao_sao_expandidos(self) -> None:
        for tipo in (
            TipoAlvo.CLASSE_FARMACOLOGICA, TipoAlvo.ALIMENTO, TipoAlvo.GRUPO_ALIMENTAR,
            TipoAlvo.OUTRA_SUBSTANCIA, TipoAlvo.EXAME_LABORATORIAL, TipoAlvo.OUTRO,
        ):
            self.assertEqual(self.nomes("varfarina", tipo), [], tipo)
        self.assertEqual(self.nomes("anticoagulantes", TipoAlvo.CLASSE_FARMACOLOGICA), [])

    def test_equivalencia_declarada_na_composicao(self) -> None:
        vinculos = self.catalogo.resolver("paroxetina", TipoAlvo.PRINCIPIO_ATIVO, "produto teste")
        self.assertEqual([v.medicamento_relacionado for v in vinculos], ["outro paroxetina", "roxetin"])
        # A equivalência declarada prevalece sobre a forma sem sal.
        self.assertEqual(vinculos[1].criterio, CriterioVinculo.COMPOSICAO_EQUIVALENCIA_DECLARADA)
        self.assertIn("equivalente a 20 mg de paroxetina base", vinculos[1].fundamento)
        self.assertEqual(vinculos[0].criterio, CriterioVinculo.PRINCIPIO_ATIVO_SEM_SAL)

    def test_excipiente_e_ausencia_declarada_nao_criam_vinculo(self) -> None:
        self.assertEqual(self.nomes("lactose monoidratada"), [])
        self.assertEqual(self.nomes("lactose"), [])
        self.assertEqual(self.nomes("glúten"), [])

    def test_composicao_ignora_ocorrencia_sem_papel_ativo(self) -> None:
        composicao = (
            "Cada mL contém: dipropionato de betametasona (equivalente a 0,5 mg (0,05%) de betametasona) 0,64 mg\n"
            "Veículo: propilenoglicol (equivalente a 10 mg de glicerol)\n"
            "Não contém açúcar. Isento de paracetamol."
        )
        encontradas = equivalencias_declaradas(composicao, "dipropionato de betametasona")
        self.assertEqual([e.substancia for e in encontradas], ["betametasona"])


if __name__ == "__main__":
    unittest.main()
