import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import RespostaInvalidaError, SecaoSomenteTituloError
from extracao_interacoes.modelos import MetodoExtracao, RespostaClassificacao, StatusExtracao
from extracao_interacoes.modo_rapido import preparar_confirmacao
from extracao_interacoes.servico import ServicoExtracaoInteracoes
from extracao_interacoes.validacao import (
    copiar_e_validar_trecho, corpo_apenas_referencias,
    localizar_fallback_estrutural, validar_candidato,
    localizar_titulos_interacoes,
)
from helpers import MedidorFalso, ProvedorFalso, configuracao, criar_documento, resposta_nao_encontrado, resposta_secao


class TestValidacaoEstrutura(unittest.TestCase):
    def executar(self, documento, respostas):
        provedor = ProvedorFalso(respostas)
        servico = ServicoExtracaoInteracoes(
            provedor, configuracao(modo_extracao="rapido"),
            leitor=lambda _: documento, fabrica_medidor=MedidorFalso,
        )
        return servico.extrair("generico", "1", None, Path("bula.pdf")), provedor

    def test_distingue_corpo_de_codigos_sem_palavra_historico(self):
        real = [
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "A associacao pode alterar o efeito dos medicamentos.",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ]
        referencia = [
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "500 MG/ML SOL ORAL CX 50 FR PLAS OPC GOT",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ]
        for textos, inicio in ((real + referencia, "L000001"), (referencia + real, "L000004")):
            resultado, _ = self.executar(criar_documento(textos), [{"confirmado": True}])
            self.assertEqual(resultado.metodo, MetodoExtracao.HIBRIDO_LLM)
            self.assertEqual(resultado.linha_inicio, inicio)
            self.assertNotIn("500 MG", resultado.trecho_interacoes)

    def test_duas_secoes_reais_sao_reunidas_em_um_unico_resultado(self):
        secao_a = ["6. INTERAÇÕES MEDICAMENTOSAS", "A associacao pode alterar o efeito do medicamento.", "7. CUIDADOS DE ARMAZENAMENTO"]
        secao_b = ["6. INTERAÇÕES MEDICAMENTOSAS", "Outra associacao possui efeito diferente no medicamento.", "7. CUIDADOS DE ARMAZENAMENTO"]
        doc = criar_documento(secao_a + secao_b)
        resultado, provedor = self.executar(
            doc, [{"confirmado": True}, {"confirmado": True}]
        )
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.metodo, MetodoExtracao.HIBRIDO_LLM)
        self.assertEqual(resultado.quantidade_chamadas_llm, 2)
        self.assertEqual(resultado.quantidade_janelas, 2)
        self.assertEqual(resultado.linha_inicio, "L000001;L000004")
        self.assertEqual(resultado.linha_fim_exclusiva, "L000003;L000006")
        self.assertEqual(
            resultado.trecho_interacoes,
            "\n".join(secao_a[:2]) + "\n\n" + "\n".join(secao_b[:2]),
        )
        self.assertEqual(len(provedor.chamadas), 2)

    def test_multiplas_secoes_nao_gravam_resultado_parcial(self):
        doc = criar_documento([
            "6. INTERAÇÕES MEDICAMENTOSAS", "Texto valido da primeira apresentacao.",
            "7. CUIDADOS DE ARMAZENAMENTO",
            "6. INTERAÇÕES MEDICAMENTOSAS", "Texto valido da segunda apresentacao.",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ])
        resultado, _ = self.executar(
            doc, [{"confirmado": True}, {"confirmado": False}]
        )
        self.assertEqual(resultado.status, StatusExtracao.REVISAO_MANUAL)
        self.assertIsNone(resultado.trecho_interacoes)

    def test_secao_nao_numerada_tambem_e_reunida_no_unico_resultado(self):
        doc = criar_documento([
            "6. INTERAÇÕES MEDICAMENTOSAS", "Texto da apresentacao oral.",
            "7. CUIDADOS DE ARMAZENAMENTO",
            "Interações", "Texto da apresentacao em creme.",
            "8. POSOLOGIA E MODO DE USAR",
        ])
        resultado, _ = self.executar(
            doc, [{"confirmado": True}, {"confirmado": True}]
        )
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.quantidade_chamadas_llm, 2)
        self.assertIn("Texto da apresentacao oral.", resultado.trecho_interacoes)
        self.assertIn("Texto da apresentacao em creme.", resultado.trecho_interacoes)
        self.assertNotIn("7. CUIDADOS", resultado.trecho_interacoes)
        self.assertNotIn("8. POSOLOGIA", resultado.trecho_interacoes)

    def test_secoes_repetidas_exatamente_iguais_sao_consolidadas(self):
        secao = [
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "A associacao pode alterar o efeito do medicamento.",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ]
        doc = criar_documento(secao + secao)
        candidatos = localizar_fallback_estrutural(doc)
        self.assertEqual(candidatos, [("L000001", "L000003", secao[0])])
        resultado, _ = self.executar(doc, [{"confirmado": True}])
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.linha_inicio, "L000001")

    def test_secoes_quase_iguais_continuam_ambiguas(self):
        secao_a = ["6. INTERAÇÕES MEDICAMENTOSAS", "Não há interações conhecidas.", "7. CUIDADOS DE ARMAZENAMENTO"]
        secao_b = ["6. INTERAÇÕES MEDICAMENTOSAS", "Não foram observadas interações conhecidas.", "7. CUIDADOS DE ARMAZENAMENTO"]
        self.assertEqual(len(localizar_fallback_estrutural(criar_documento(secao_a + secao_b))), 2)

    def test_corpo_desconhecido_nao_e_descartado_para_forcar_unicidade(self):
        doc = criar_documento([
            "6. INTERAÇÕES MEDICAMENTOSAS", "A associacao pode alterar o efeito do medicamento.",
            "7. CUIDADOS DE ARMAZENAMENTO",
            "6. INTERAÇÕES MEDICAMENTOSAS", "Termos desconhecidos em uma estrutura nova a revisar.",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ])
        self.assertIsNone(preparar_confirmacao(doc))

    def test_negativa_semantica_nao_pode_ser_aprovada_por_fallback(self):
        doc = criar_documento([
            "6. INTERAÇÕES MEDICAMENTOSAS", "Texto aparentemente valido mas rejeitado pela LLM.",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ])
        resultado, _ = self.executar(doc, [{"confirmado": False}, resposta_nao_encontrado()])
        self.assertEqual(resultado.status, StatusExtracao.REVISAO_MANUAL)
        self.assertIsNone(resultado.trecho_interacoes)

    def test_frase_curta_de_ausencia_e_valida(self):
        doc = criar_documento([
            "6. INTERAÇÕES MEDICAMENTOSAS", "Não há interações conhecidas.",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ])
        resultado, _ = self.executar(doc, [{"confirmado": True}])
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertIn("Não há", resultado.trecho_interacoes)

    def test_lista_tabela_e_continuacao_na_pagina_seguinte(self):
        for corpo in (
            ["Interações contraindicadas:", "Medicamento A: pode aumentar a concentracao do medicamento B."],
            ["Medicamento | Efeito", "Medicamento A | Aumento da concentracao do medicamento B"],
        ):
            doc = criar_documento(
                ["6. INTERAÇÕES MEDICAMENTOSAS", *corpo, "7. CUIDADOS DE ARMAZENAMENTO"],
                paginas=[1, 2, 2, 2],
            )
            resultado, provedor = self.executar(doc, [{"confirmado": True}])
            self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
            self.assertIn(corpo[-1], resultado.trecho_interacoes)
            self.assertIn(corpo[-1], provedor.chamadas[0][-1]["content"])

    def test_mencao_no_meio_do_paragrafo_nao_e_titulo(self):
        doc = criar_documento([
            "Consulte INTERAÇÕES MEDICAMENTOSAS para mais informacoes.",
            "Texto qualquer que nao transforma a mencao em titulo.",
        ])
        with self.assertRaises(RespostaInvalidaError):
            validar_candidato(
                RespostaClassificacao.model_validate(resposta_secao(titulo="INTERAÇÕES MEDICAMENTOSAS")),
                doc, {"L000001"},
            )
        resultado, provedor = self.executar(doc, [])
        self.assertEqual(resultado.status, StatusExtracao.SEM_SECAO_INTERACOES)
        self.assertEqual(provedor.chamadas, [])

    def test_frases_de_interacao_dentro_de_outro_topico_nao_formam_secao(self):
        doc = criar_documento([
            "4. O QUE DEVO SABER ANTES DE USAR ESTE MEDICAMENTO?",
            "Medicamentos anti-histaminicos podem aumentar o efeito sedativo.",
            "Interações podem ocorrer entre produtos e plantas medicinais.",
            "5. ONDE, COMO E POR QUANTO TEMPO POSSO GUARDAR?",
        ])
        self.assertEqual(localizar_titulos_interacoes(doc), [])
        resultado, provedor = self.executar(doc, [])
        self.assertEqual(resultado.status, StatusExtracao.SEM_SECAO_INTERACOES)
        self.assertIsNone(resultado.trecho_interacoes)
        self.assertEqual(resultado.quantidade_chamadas_llm, 0)
        self.assertEqual(provedor.chamadas, [])

    def test_apenas_rotulos_e_datas_nao_formam_corpo(self):
        doc = criar_documento([
            "INTERAÇÕES MEDICAMENTOSAS", "INTERAÇÕES MEDICAMENTOSAS", "01/01/2026", "02/02/2026",
        ])
        with self.assertRaises(SecaoSomenteTituloError):
            copiar_e_validar_trecho(doc, "L000001", "L000005", "INTERAÇÕES MEDICAMENTOSAS")

    def test_titulo_quebrado_em_tres_linhas(self):
        doc = criar_documento([
            "6.", "INTERAÇÕES", "MEDICAMENTOSAS",
            "Não há interações conhecidas.", "7. CUIDADOS DE ARMAZENAMENTO",
        ])
        self.assertEqual(localizar_fallback_estrutural(doc), [("L000001", "L000005", "6. INTERAÇÕES MEDICAMENTOSAS")])
        resultado, _ = self.executar(doc, [{"confirmado": True}])
        self.assertEqual(resultado.trecho_interacoes, "\n".join(l.texto_original for l in doc.linhas[:4]))

    def test_titulos_numerados_sem_pontuacao_sao_reconhecidos(self):
        doc = criar_documento([
            "6 INTERAÇÕES MEDICAMENTOSAS",
            "6.1 Interações medicamentosas",
            "A associacao pode alterar o metabolismo do medicamento.",
            "6.2 Interações do medicamento/teste laboratorial",
            "Nenhuma conhecida.",
            "7 CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO",
        ])
        self.assertEqual(
            localizar_fallback_estrutural(doc),
            [("L000001", "L000006", "6 INTERAÇÕES MEDICAMENTOSAS")],
        )
        resultado, _ = self.executar(doc, [{"confirmado": True}])
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertIn("6.2 Interações", resultado.trecho_interacoes)

    def test_indice_terapeutico_no_paragrafo_nao_e_sumario(self):
        doc = criar_documento([
            "O indice terapeutico deve ser considerado.",
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "A associacao pode alterar o efeito dos medicamentos.",
            "7. CUIDADOS DE ARMAZENAMENTO",
        ])
        self.assertEqual(len(localizar_fallback_estrutural(doc)), 1)

    def test_tabela_clinica_com_unidades_e_datas_nao_e_referencia(self):
        self.assertFalse(corpo_apenas_referencias([
            "01/01/2026 | 500 MG/ML CX",
            "Digoxina | Aumento das concentracoes plasmaticas.",
        ]))

    def test_rotulos_administrativos_fragmentados_sao_referencia(self):
        self.assertTrue(corpo_apenas_referencias([
            "Medicamentosas       500 MG/ML SOL ORAL CX",
            "50, 100, 200 FR PLAS        Reações Adversas",
            "20/03/2019 3677058/20- 10450 SIMILAR VP/VPS",
            "Notificação de    Alteração de Texto de Bula RDC 60/12",
        ]))

    def test_tabela_regulatoria_com_palavras_desconhecidas_e_referencia(self):
        self.assertTrue(corpo_apenas_referencias([
            "10450 - SIMILAR VP/VPS",
            "11/06/2019 Notificação de Alteração",
            "Texto de Bula - por quanto tempo posso guardar este medicamento?",
            "25MG X 30 COM RDC 60/12",
        ]))

    def test_dose_clinica_sem_marcadores_regulatorios_nao_e_referencia(self):
        self.assertFalse(corpo_apenas_referencias([
            "Metotrexato em doses de 15 mg por semana:",
            "pode ocorrer aumento da toxicidade hematologica.",
        ]))
