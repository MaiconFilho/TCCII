import sys
from pathlib import Path
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pipelines.extracao_interacoes.cadastro import ServicoExtracaoCadastro, localizar_composicoes, _janelas, _permite_nomes_adicionais
from pipelines.extracao_interacoes.erros import RespostaInvalidaError, LimiteMemoriaError
from pipelines.extracao_interacoes.modelos import RespostaLLM
from helpers import criar_documento, configuracao, ProvedorFalso, MedidorFalso


def resposta(*nomes, excipientes=()):
    return {"principios_ativos": list(nomes), "excipientes": list(excipientes)}


def documento_padrao():
    return criar_documento([
        "COMPOSIÇÃO", "Cada mL contém:", "dipirona monoidratada ... 500 mg",
        "Excipientes: ácido ascórbico, água e sorbitol.",
        "INFORMAÇÕES TÉCNICAS AOS PROFISSIONAIS DE SAÚDE", "1. INDICAÇÕES", "Outro texto.",
    ])


def extrair(doc, respostas, **cfg):
    p = ProvedorFalso(respostas)
    return ServicoExtracaoCadastro(p, configuracao(**cfg), MedidorFalso).extrair(doc), p


class TestCadastro(unittest.TestCase):
    def test_ids_classificam_ativo_e_excipiente_sem_reescrever_nomes(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada mL contém:",
            "cloridrato de lidocaína ... 50 mg", "glicose ... 75 mg", "1. INDICAÇÕES"])
        r, p = extrair(doc, [{"principios_ativos": [], "classificacao": {"1": "ativo", "2": "excipiente"}}])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "cloridrato de lidocaína")
        self.assertIn("glicose ... 75 mg", r.composicao)
        self.assertEqual(p.esquemas[0]["properties"]["classificacao"]["required"], ["1", "2"])
        self.assertNotIn("confirmado", p.esquemas[0]["properties"])

    def test_tabela_com_funcao_explicita_nao_inclui_estabilizantes(self):
        doc = criar_documento(["COMPOSIÇÃO", "Componente    Quantidade    Função",
            "Fator IX    50 UI    Princípio ativo", "heparina sódica    5 UI    Estabilizante",
            "cloreto de sódio    26 mg    Estabilizante", "1. INDICAÇÕES"])
        r, p = extrair(doc, [{"principios_ativos": [], "classificacao": {"1": "ativo"}}])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "Fator IX")
        self.assertIn("heparina sódica", r.composicao)
        self.assertNotIn("heparina sódica", p.chamadas[0][-1]["content"])

    def test_nome_generico_da_identificacao_com_evidencia_propria(self):
        doc = criar_documento(["YESCARTA®", "axicabtageno ciloleucel", "APRESENTAÇÕES",
            "Suspensão para infusão.", "COMPOSIÇÃO", "Cada bolsa contém células T autólogas.",
            "1. INDICAÇÕES"])
        r, _ = extrair(doc, [{"principios_ativos": [], "classificacao": {"1": "ativo"}}])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "axicabtageno ciloleucel")
        self.assertNotIn("axicabtageno", r.composicao)
        self.assertTrue(r.evidencias)

    def test_diluente_da_apresentacao_nao_vira_ativo(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada frasco contém:", "ampicilina sódica ... 1 g",
            "Cada ampola de diluente contém:", "água para injetáveis ... 3 mL", "1. INDICAÇÕES"])
        r, p = extrair(doc, [{"principios_ativos": [], "classificacao": {"1": "ativo"}}])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "ampicilina sódica")
        self.assertIn("água para injetáveis", r.composicao)
        self.assertNotIn("água para injetáveis", p.chamadas[0][-1]["content"])

    def test_ids_invalidos_ou_classificacao_contraditoria_nao_sao_aceitos(self):
        for ids_ativos, ids_excipientes in (([2], []), ([1], [1])):
            with self.subTest(ids_ativos=ids_ativos):
                resposta_ids = {"principios_ativos": [], "ids_ativos": ids_ativos,
                                "ids_excipientes": ids_excipientes}
                r, _ = extrair(documento_padrao(), [resposta_ids] * 2)
                self.assertEqual(r.status, "REVISAO_MANUAL")
                self.assertIsNone(r.principios_ativos)

    def test_copia_redundante_de_excipiente_nao_contradiz_classificacao(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada mL contém:",
            "cloridrato de lidocaína ... 50 mg", "glicose ... 75 mg", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [{"principios_ativos": ["cloridrato de lidocaína", "glicose"],
                             "classificacao": {"1": "ativo", "2": "excipiente"}}])
        self.assertEqual(r.principios_ativos, "cloridrato de lidocaína")

    def test_gramatica_evitar_reescrita_nao_silencia_ingrediente_nao_listado(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada comprimido contém:", "cafeína ... 30 mg"])
        self.assertFalse(_permite_nomes_adicionais(doc.linhas, ["cafeína"]))
        associacao = criar_documento(["cafeína ... 30 mg", "Cada comprimido contém também paracetamol 500 mg."])
        self.assertTrue(_permite_nomes_adicionais(associacao.linhas, ["cafeína"]))

    def test_associacao_omitida_recebe_segunda_tentativa(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada comprimido contém:",
            "paracetamol ... 500 mg", "cafeína ... 30 mg", "1. INDICAÇÕES"])
        r, p = extrair(doc, [
            {"principios_ativos": [], "ids_ativos": [1], "ids_excipientes": []},
            {"principios_ativos": [], "ids_ativos": [1, 2], "ids_excipientes": []},
        ])
        self.assertEqual(r.principios_ativos, "paracetamol; cafeína")
        self.assertIn("cafeína", p.chamadas[1][-1]["content"])

    def test_tabela_transposta_nao_junta_nomes_com_marca(self):
        doc = criar_documento(["COMPOSIÇÃO",
            "Princípios ativos              trimetoprima                  sulfametoxazol",
            "Bacfar® comprimido         80 mg                     400 mg",
            "Bacfar® F comprimido       160 mg                    800 mg",
            "Excipientes: amido.", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [{"principios_ativos": [], "ids_ativos": [1, 2], "ids_excipientes": []}])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "trimetoprima; sulfametoxazol")

    def test_bloco_sem_titulo_na_identificacao_nao_confunde_posologia(self):
        doc = criar_documento(["APRESENTAÇÕES", "Comprimidos.", "USO ADULTO",
            "Cada comprimido contém:", "ácido fólico ... 5 mg", "Excipientes: água.",
            "1. INDICAÇÕES", "Indicado para tratamento.", "8. POSOLOGIA E MODO DE USAR",
            "Cada paciente contém informações específicas no cadastro."])
        secoes = localizar_composicoes(doc)
        self.assertEqual(len(secoes), 1)
        self.assertTrue(secoes[0].texto.startswith("Cada comprimido"))
        self.assertNotIn("paciente", secoes[0].texto)

    def test_dose_com_unidade_na_linha_seguinte_nao_vira_sumario(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada dose contém:", "budesonida ... 32", "mcg",
            "budesonida ... 64", "mcg", "1. INDICAÇÕES"])
        self.assertEqual(len(localizar_composicoes(doc)), 1)
        r, _ = extrair(doc, [{"principios_ativos": [], "classificacao": {"1": "ativo"}}])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "budesonida")

    def test_potencia_homeopatica_e_parte_do_nome(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada mL contém:",
            "Passiflora alata D1 ... 526 mg", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [{"principios_ativos": [], "ids_ativos": [1], "ids_excipientes": []}])
        self.assertEqual(r.principios_ativos, "Passiflora alata D1")

    def test_corrige_acento_e_dose_da_resposta_usando_nome_da_bula(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada comprimido contém:",
            "cafeína ... 30 mg", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("caféína 30 mg")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "cafeína")

    def test_somente_composicao_copia_todo_topico_sem_modelo(self):
        linhas = [
            "COMPOSIÇÃO", "Cada 1 mL de solução contém:",
            "Extrato seco de folhas de Hedera helix Linné ... 7 mg*;",
            "excipientes q.s.p. ... 1 mL.", "* equivalente a 0,95 mg/mL.",
            "Cada 1 mL de solução sabor menta contém:",
            "extrato seco de folhas de Hedera helix Linné ... 7 mg*;",
            "excipientes q.s.p. ... 1 mL.", "1. INDICAÇÕES",
        ]
        doc = criar_documento(linhas)
        resultado = ServicoExtracaoCadastro(None, None).extrair(
            doc, somente_composicao=True,
        )
        self.assertEqual(resultado.status, "COMPOSICAO_EXTRAIDA")
        self.assertIsNone(resultado.principios_ativos)
        self.assertEqual(resultado.composicao, "\n".join(linhas[:-1]))
        self.assertEqual(resultado.chamadas_llm, 0)

    def test_tabela_em_colunas_usa_ativo_da_identificacao_sem_incluir_excipientes(self):
        primeira = [
            "ZYPREXA®", "olanzapina", "APRESENTAÇÕES",
            "ZYPREXA 2,5 mg, 5 mg e 10 mg, comprimidos revestidos.",
            "VIA ORAL", "USO ADULTO", "COMPOSIÇÃO", "Cada comprimido contém:",
            "               ZYPREXA 2,5 mg  ZYPREXA 5 mg  ZYPREXA 10 mg",
            "     olanzapina              2,5 mg              5,0 mg            10,0 mg",
            "lactose monoidratada      102,15 mg          156,0 mg          312,0 mg",
            "Outros excipientes q.s.p. um comprimido: cera de carnaúba.",
            "INFORMAÇÕES TÉCNICAS AOS PROFISSIONAIS DE SAÚDE",
        ]
        segunda = [
            "ZYPREXA® ZYDIS®", "olanzapina", "APRESENTAÇÕES",
            "ZYPREXA ZYDIS 5 e 10 mg, comprimidos orodispersíveis.",
            "USO ADULTO", "COMPOSIÇÃO", "Cada comprimido orodispersível contém:",
            "         ZYPREXA ZYDIS 5 mg  ZYPREXA ZYDIS 10 mg",
            " olanzapina           5 mg                  10 mg",
            " aspartamo           0,60 mg                  0,80 mg",
            "Outros excipientes q.s.p. um comprimido: gelatina e manitol.",
            "INFORMAÇÕES TÉCNICAS AOS PROFISSIONAIS DE SAÚDE",
        ]
        doc = criar_documento(primeira + segunda, [1] * len(primeira) + [14] * len(segunda))
        resultado, provedor = extrair(doc, [
            resposta("olanzapina", excipientes=["lactose monoidratada"]),
            resposta("olanzapina", excipientes=["aspartamo"]),
        ])

        self.assertEqual(resultado.status, "CONCLUIDO")
        self.assertEqual(resultado.principios_ativos, "olanzapina")
        self.assertEqual(len(resultado.evidencias), 2)
        self.assertEqual(resultado.composicao.count("COMPOSIÇÃO"), 2)
        self.assertIn("ZYPREXA 2,5 mg  ZYPREXA 5 mg  ZYPREXA 10 mg", resultado.composicao)
        self.assertIn("ZYPREXA ZYDIS 5 mg  ZYPREXA ZYDIS 10 mg", resultado.composicao)
        self.assertIn("lactose monoidratada", resultado.composicao)
        self.assertIn("aspartamo", resultado.composicao)
        self.assertTrue(all("<CANDIDATOS_DOSADOS>\n1: olanzapina" in chamada[-1]["content"]
                            for chamada in provedor.chamadas))

    def test_marca_sem_principio_identificado_nao_vira_candidato_dosado(self):
        doc = criar_documento([
            "ZYPREXA®", "APRESENTAÇÕES", "ZYPREXA 5 mg, comprimidos.",
            "COMPOSIÇÃO", "Cada comprimido contém:",
            "ZYPREXA          5 mg", "lactose monoidratada          100 mg",
            "Outros excipientes: celulose.", "1. INDICAÇÕES",
        ])
        resultado, provedor = extrair(doc, [
            {"confirmado": False, "principios_ativos": []},
            {"confirmado": False, "principios_ativos": []},
        ])
        self.assertEqual(resultado.status, "REVISAO_MANUAL")
        self.assertNotIn("\nZYPREXA\n", provedor.chamadas[0][-1]["content"])

    def test_ativo_e_excipientes_na_mesma_linha_preserva_so_prefixo_no_prompt(self):
        doc = criar_documento([
            "CASODEX®", "bicalutamida", "APRESENTAÇÃO",
            "Comprimidos revestidos de 50 mg em embalagens com 28 comprimidos.",
            "VIA ORAL", "COMPOSIÇÃO",
            "Cada comprimido revestido de CASODEX contém 50 mg de bicalutamida. Excipientes: dióxido de titânio, lactose.",
            "1. INDICAÇÕES",
        ])
        resultado, provedor = extrair(doc, [resposta("bicalutamida")])
        self.assertEqual(resultado.status, "CONCLUIDO")
        self.assertEqual(resultado.principios_ativos, "bicalutamida")
        self.assertIn("Excipientes: dióxido de titânio", resultado.composicao)
        prompt = provedor.chamadas[0][-1]["content"]
        self.assertIn("<CANDIDATOS_DOSADOS>\n1: bicalutamida", prompt)
        self.assertNotIn("dióxido de titânio", prompt)
        self.assertEqual(resultado.evidencias[0]["linha"], "L000007")

    def test_associacao_com_doses_antes_dos_tres_nomes(self):
        doc = criar_documento([
            "DORFLEX MAX®", "dipirona monoidratada", "citrato de orfenadrina",
            "cafeína anidra", "APRESENTAÇÕES", "Comprimidos 600 mg + 70 mg + 100 mg.",
            "COMPOSIÇÃO",
            "Cada comprimido contém 600 mg de dipirona monoidratada, 70 mg de citrato de orfenadrina",
            "e 100", "mg de cafeína anidra.",
            "Excipientes: amido e talco.", "1. INDICAÇÕES",
        ])
        nomes = ("dipirona monoidratada", "citrato de orfenadrina", "cafeína anidra")
        resultado, provedor = extrair(doc, [resposta(*nomes)])
        self.assertEqual(resultado.status, "CONCLUIDO")
        self.assertEqual(resultado.principios_ativos, "; ".join(nomes))
        self.assertEqual(len(resultado.evidencias), 3)
        prompt = provedor.chamadas[0][-1]["content"]
        for nome in nomes:
            self.assertIn(nome, prompt.split("<CANDIDATOS_DOSADOS>")[1])

    def test_ablok_titulo_com_marcador_preserva_tres_apresentacoes(self):
        textos = ["    •  COMPOSIÇÃO"]
        for dose in (25, 50, 100):
            textos.extend([f"Comprimido {dose} mg", f"Cada comprimido de {dose} mg contém:",
                f"atenolol........................  {dose} mg",
                "Excipientes: celulose microcristalina e estearato de magnésio.",
                "crospovidona, dióxido de silício, laurilsulfato de sódio."])
        textos.extend(["INFORMAÇÕES TÉCNICAS AOS PROFISSIONAIS DE SAÚDE", "1. INDICAÇÕES", "Outro assunto."])
        r, _ = extrair(criar_documento(textos), [resposta("atenolol")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "atenolol")
        self.assertEqual(r.composicao, "\n".join(textos[:-3]).strip())
        for dose in (25, 50, 100):
            self.assertIn(f"Comprimido {dose} mg", r.composicao)
        self.assertEqual(r.composicao.count("Excipientes:"), 3)

    def test_marcadores_no_titulo_e_no_limite_nao_exigem_regra_por_medicamento(self):
        for marcador in ("•", "▪", "◦", "●", "\uf0b7", "*", "-"):
            with self.subTest(marcador=marcador):
                textos = [f"{marcador} 2. COMPOSIÇÃO", "Cada mL contém:", "dipirona ... 500 mg",
                    f"{marcador} INFORMAÇÕES TÉCNICAS AOS PROFISSIONAIS DE SAÚDE", "Texto fora da composição."]
                r, _ = extrair(criar_documento(textos), [resposta("dipirona")])
                self.assertEqual(r.status, "CONCLUIDO")
                self.assertNotIn("Texto fora", r.composicao)
                self.assertTrue(r.composicao.startswith(marcador))

    def test_mencao_em_lista_nao_vira_cabecalho_de_composicao(self):
        doc = criar_documento(["• Consulte a composição do medicamento.", "dipirona ... 500 mg", "1. INDICAÇÕES"])
        self.assertEqual(localizar_composicoes(doc), [])

    def test_composicao_literal_com_excipientes_e_ativo_com_evidencia(self):
        doc = documento_padrao()
        r, p = extrair(doc, [resposta("dipirona monoidratada")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.composicao, "\n".join(l.texto_original for l in doc.linhas[:4]))
        self.assertEqual(r.principios_ativos, "dipirona monoidratada")
        self.assertEqual(r.evidencias[0]["linha"], "L000003")
        self.assertNotIn("ácido ascórbico", p.chamadas[0][-1]["content"])
        self.assertGreaterEqual(r.tempo_segundos, 0)

    def test_multiplas_apresentacoes_deduplicam_ativo_sem_perder_texto(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada mL contém:", "dropropizina ... 3 mg",
            "Excipientes: água.", "Cada mL da outra apresentação contém:", "dropropizina ... 30 mg",
            "Excipientes: sacarose.", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("dropropizina", "dropropizina")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "dropropizina")
        self.assertIn("30 mg", r.composicao)
        self.assertIn("sacarose", r.composicao)

    def test_associacao_com_dois_ativos_sem_espaco_antes_dos_pontos(self):
        doc = criar_documento(["COMPOSIÇÃO:", "Cada mL contém:", "tintura de Plumeria lancifolia (1)....0,625 mL",
            "salicilato de sódio....400 mg", "veículo*....1 mL", "*água.", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("tintura de Plumeria lancifolia", "salicilato de sódio")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "tintura de Plumeria lancifolia; salicilato de sódio")
        r, _ = extrair(doc, [resposta("tintura de Plumeria lancifolia")] * 2)
        self.assertEqual(r.status, "REVISAO_MANUAL")
        self.assertIn("salicilato de sódio....400 mg", r.composicao)
        self.assertIn("Classifique", r.detalhe)

    def test_fragmento_univoco_e_resolvido_para_nome_literal_completo(self):
        r, _ = extrair(documento_padrao(), [resposta("dipirona"), resposta("dipirona")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "dipirona monoidratada")

    def test_candidatos_dosados_preservam_sal_qualificador_e_ignoram_equivalencia(self):
        casos = [
            ("acetato de atosibana ... 7,953 mg", "acetato de atosibana"),
            ("ácido zoledrônico (anidro) ... 5 mg", "ácido zoledrônico (anidro)"),
            ("aciclovir sódico (equivalente a 250 mg de aciclovir base) ... 274 mg", "aciclovir sódico"),
            ("Albumina humana (D.C.B.: 00460) ... 200 mg", "Albumina humana"),
            ("ibuprofeno …………………………………………600mg", "ibuprofeno"),
            ("água para injetáveis q.s.p ... 100 mL", "água para injetáveis"),
            ("Princípio ativo: bevacizumabe (anticorpo monoclonal anti-VEGF humanizado) ... 100 mg", "bevacizumabe"),
        ]
        for linha, nome in casos:
            with self.subTest(linha=linha):
                doc = criar_documento(["COMPOSIÇÃO", "Cada unidade contém:", linha,
                                      "Excipientes: água.", "1. INDICAÇÕES"])
                r, p = extrair(doc, [resposta(nome)])
                self.assertEqual(r.status, "CONCLUIDO")
                self.assertEqual(r.principios_ativos, nome)
                self.assertIn(nome, p.chamadas[0][-1]["content"])

    def test_resposta_abreviada_tenta_corrigir_com_nome_literal(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada mL contém:",
                              "acetato de atosibana ... 7,953 mg", "1. INDICAÇÕES"])
        r, p = extrair(doc, [resposta("atosibana"), resposta("acetato de atosibana")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "acetato de atosibana")
        self.assertEqual(r.chamadas_llm, 1)

    def test_llm_pode_negar_secao_mas_nome_dosado_literal_confirma_cadastro(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada grama contém:",
                              "desonida ... 1 mg", "Excipientes: água.", "1. INDICAÇÕES"])
        p = ProvedorFalso([{"confirmado": False, "principios_ativos": ["desonida"]}])
        r = ServicoExtracaoCadastro(p, configuracao(), MedidorFalso).extrair(doc)
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "desonida")

    def test_agua_qsp_e_veiculo_quando_ha_outro_ativo_dosado(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada seringa contém:",
                              "enoxaparina sódica ... 60 mg", "água para injetáveis q.s.p. ... 1 mL",
                              "1. INDICAÇÕES"])
        r, p = extrair(doc, [resposta("enoxaparina sódica")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "enoxaparina sódica")
        self.assertNotIn("água para injetáveis", p.chamadas[0][-1]["content"].split("<DOCUMENTO>")[1].split("</DOCUMENTO>")[0])
        self.assertIn("água para injetáveis", r.composicao)

    def test_rotulo_principio_ativo_nao_integra_nome(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada frasco contém:",
                              "Princípio ativo: bevacizumabe ... 100 mg", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("bevacizumabe")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "bevacizumabe")

    def test_descricao_de_anticorpo_nao_integra_nome_ativo(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada frasco contém:",
                              "Princípio ativo: bevacizumabe (anticorpo monoclonal anti-VEGF humanizado) ... 100 mg",
                              "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("bevacizumabe (anticorpo monoclonal anti-VEGF humanizado)"),
                              resposta("bevacizumabe")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "bevacizumabe")

    def test_adjuvante_dosado_e_classificado_sem_bloquear_ativo(self):
        doc = criar_documento(["COMPOSIÇÃO", "Substância ativa por dose:",
                              "toxoide tetânico ... 20 UI", "fosfato de alumínio como adjuvante ... 1,5 mg",
                              "1. INDICAÇÕES"])
        r, p = extrair(doc, [resposta("toxoide tetânico", "fosfato de alumínio"),
                            resposta("toxoide tetânico", excipientes=["fosfato de alumínio como adjuvante"])])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "toxoide tetânico")
        self.assertEqual(len(p.chamadas), 2)

    def test_marcador_com_nota_declarada_nao_integra_nome_do_ativo(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada frasco contém:",
                              "Glicoproteína F estabilizada na conformação",
                              "pré-fusão1 ... 60 mcg", "1 Produzida por tecnologia recombinante.",
                              "1. INDICAÇÕES"])
        nome = "Glicoproteína F estabilizada na conformação pré-fusão"
        r, _ = extrair(doc, [resposta(nome)])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, nome)
        self.assertIn("pré-fusão1 ... 60 mcg", r.composicao)
        self.assertEqual(r.evidencias[0]["linhas"], ["L000003", "L000004"])

    def test_underscore_da_llm_e_convertido_em_espaco_com_evidencia(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada mL contém:",
                              "Extrato seco de folhas de Hedera helix Linné ... 7 mg",
                              "Excipientes: água.", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("extrato_seco_de_folhas_de_Hedera_helix_Linné")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "Extrato seco de folhas de Hedera helix Linné")
        self.assertEqual(r.evidencias[0]["linha"], "L000003")

    def test_prefixo_curto_do_sal_quebrado_na_linha_anterior(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada frasco contém:", "acetato de",
                              "caspofungina ... 55,5 mg", "Excipientes: água.",
                              "Cada frasco da outra apresentação contém:",
                              "acetato de caspofungina ... 77,7 mg",
                              "Excipientes: água.", "1. INDICAÇÕES"])
        r, p = extrair(doc, [resposta("acetato de caspofungina")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "acetato de caspofungina")
        self.assertIn("55,5 mg", r.composicao)
        self.assertIn("77,7 mg", r.composicao)
        candidatos = p.chamadas[0][-1]["content"].split("<CANDIDATOS_DOSADOS>")[1]
        self.assertNotIn("\ncaspofungina\n", candidatos)

    def test_tabela_paralela_de_sais_e_ions_nao_vira_lista_de_ativos(self):
        doc = criar_documento(["COMPOSIÇÃO:", "Composição Quantitativa dos Sais (p/v): Composição Quantitativa dos Íons:",
                              "Cloreto de Sódio 200 g/L sódio 140 mEq/L",
                              "Cloreto de Potássio 4 g/L potássio 2 mEq/L",
                              "Água Purificada q.s.p. 1000 mL cálcio 3 mEq/L",
                              "1. INDICAÇÕES"])
        r, p = extrair(doc, [resposta("sódio", "potássio", "Água Purificada")])
        self.assertEqual(r.status, "REVISAO_MANUAL")
        self.assertIn("Tabela paralela", r.detalhe)
        self.assertFalse(p.chamadas)

    def test_composicao_cruza_pagina_e_nome_pode_cruzar_linha(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada comprimido contém cloridrato de",
            "lidocaína em quantidade de 10 mg.", "Excipientes: água.", "1. INDICAÇÕES"], [1, 1, 2, 2, 2])
        r, _ = extrair(doc, [resposta("cloridrato de lidocaína")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.evidencias[0]["linhas"], ["L000002", "L000003"])
        self.assertIn("Excipientes: água.", r.composicao)

    def test_duas_secoes_no_mesmo_pdf_sao_unidas(self):
        doc = criar_documento(["COMPOSIÇÃO", "Cada mL contém:", "dipirona ... 500 mg", "1. INDICAÇÕES",
            "Texto", "COMPOSIÇÃO", "Cada mL contém:", "paracetamol ... 50 mg", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("dipirona"), resposta("paracetamol")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.composicao.count("COMPOSIÇÃO"), 2)
        self.assertEqual(r.principios_ativos, "dipirona; paracetamol")

    def test_titulo_plural_composicoes_preserva_apresentacoes(self):
        doc = criar_documento(["COMPOSIÇÕES", "Comprimido 10 mg", "Cada comprimido contém:",
                              "aripiprazol ... 10 mg", "Excipientes: manitol.",
                              "COMPOSIÇÕES", "Comprimido 20 mg", "Cada comprimido contém:",
                              "aripiprazol ... 20 mg", "Excipientes: amido.", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("aripiprazol")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertEqual(r.principios_ativos, "aripiprazol")
        self.assertIn("Comprimido 10 mg", r.composicao)
        self.assertIn("Comprimido 20 mg", r.composicao)

    def test_sem_titulo_limite_ou_corpo_fica_para_revisao_sem_llm(self):
        for linhas in (["Texto qualquer com a palavra composição no corpo."],
            ["COMPOSIÇÃO", "1. INDICAÇÕES"], ["COMPOSIÇÃO", "dipirona ... 500 mg"]):
            with self.subTest(linhas=linhas):
                r, p = extrair(criar_documento(linhas), [])
                self.assertEqual(r.status, "REVISAO_MANUAL")
                self.assertFalse(p.chamadas)
                self.assertIsNone(r.composicao)

    def test_sumario_e_historico_nao_viram_composicao(self):
        doc = criar_documento(["Histórico de alterações da bula", "Dados da submissão eletrônica",
            "COMPOSIÇÃO", "Texto alterado na revisão do documento.", "1. INDICAÇÕES"])
        self.assertEqual(localizar_composicoes(doc), [])

    def test_continuacao_do_historico_sem_cabecalhos_nao_vira_composicao(self):
        clinica = ["COMPOSIÇÃO", "Cada comprimido contém 80 mg de valsartana.", "1. INDICAÇÕES"]
        historico = ["Histórico de alterações da bula", "Dados da submissão eletrônica",
            "Petição/Notificação", "Itens de bula", "Apresentações relacionadas",
            "MEDICAMENTO NOVO - Notificação", "Texto de Bula - RDC 60/12"]
        continuacao = ["MEDICAMENTO NOVO - Notificação", "Texto de Bula - RDC 60/12",
            "- Composição", "- Onde, como e por quanto tempo posso guardar este medicamento?",
            "MEDICAMENTO NOVO - Notificação", "Texto de Bula - RDC 60/12",
            "- Composição", "- Cuidados de armazenamento do medicamento"]
        doc = criar_documento(
            clinica + historico + continuacao,
            [1] * len(clinica) + [2] * len(historico) + [3] * len(continuacao),
        )
        secoes = localizar_composicoes(doc)
        self.assertEqual(len(secoes), 1)
        self.assertEqual(doc.linhas[secoes[0].inicio].pagina, 1)

    def test_rejeita_nome_inventado_excipiente_e_dose(self):
        for nome in ("paracetamol", "ácido ascórbico", "COMPOSIÇÃO"):
            with self.subTest(nome=nome):
                r, _ = extrair(documento_padrao(), [resposta(nome), resposta(nome)])
                self.assertEqual(r.status, "REVISAO_MANUAL")
                self.assertIsNone(r.principios_ativos)

    def test_resposta_truncada_preserva_composicao_literal(self):
        truncada = RespostaLLM('{"confirmado":true}', 10, 256, True)
        r, _ = extrair(documento_padrao(), [truncada, truncada])
        self.assertEqual(r.status, "REVISAO_MANUAL")
        self.assertEqual(r.chamadas_llm, 2)
        self.assertIn("dipirona monoidratada ... 500 mg", r.composicao)

    def test_memoria_e_erro_de_modelo_nao_geram_sucesso(self):
        for erro, status in ((LimiteMemoriaError("memória"), "LIMITE_MEMORIA"), (RuntimeError("modelo"), "ERRO_EXTRACAO_CADASTRO")):
            r, _ = extrair(documento_padrao(), [erro])
            self.assertEqual(r.status, status)
            self.assertIn("dipirona monoidratada ... 500 mg", r.composicao)

    def test_lista_vazia_nao_e_cadastro_completo(self):
        r, _ = extrair(documento_padrao(), [resposta()] * 2)
        self.assertEqual(r.status, "REVISAO_MANUAL")
        self.assertEqual(r.composicao, "\n".join(documento_padrao().linhas[i].texto_original for i in range(4)))

    def test_corpo_curto_e_copiado_sem_validacao_de_conteudo(self):
        doc = criar_documento(["COMPOSIÇÃO", "texto curto", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta()])
        self.assertEqual(r.status, "REVISAO_MANUAL")
        self.assertEqual(r.composicao, "COMPOSIÇÃO\ntexto curto")

    def test_janelas_preservam_todas_linhas_sem_corte(self):
        doc = criar_documento([f"texto {i}" for i in range(10)])
        janelas = list(_janelas(doc.linhas, lambda texto: len(texto.split()), 10))
        self.assertEqual({l.numero for janela in janelas for l in janela}, set(range(1, 11)))
        with self.assertRaises(RespostaInvalidaError):
            list(_janelas(doc.linhas, lambda texto: 100, 10))

    def test_contexto_insuficiente_nao_chama_modelo(self):
        r, p = extrair(documento_padrao(), [], tamanho_contexto=800)
        self.assertEqual(r.status, "REVISAO_MANUAL")
        self.assertFalse(p.chamadas)

    def test_ocr_preserva_evidencia_e_avisa_para_conferir_a_imagem(self):
        doc = replace(documento_padrao(), paginas_ocr=(1,))
        r, _ = extrair(doc, [resposta("dipirona monoidratada")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertIn("OCR", r.detalhe)
        self.assertIn("doses", r.detalhe)

    def test_limite_tecnicasaos_unido_pelo_ocr_nao_entra_na_composicao(self):
        doc = criar_documento(["COMPOSIÇÃO", "dipirona ... 500 mg",
            "INFORMAÇÕES TÉCNICASAOS PROFISSIONAIS DE SAÚDE", "1. INDICAÇÕES"])
        r, _ = extrair(doc, [resposta("dipirona")])
        self.assertEqual(r.status, "CONCLUIDO")
        self.assertNotIn("TÉCNICAS", r.composicao)
