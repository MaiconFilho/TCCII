import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.modelos import StatusExtracao
from extracao_interacoes.modo_rapido import preparar_confirmacao
from extracao_interacoes.servico import ServicoExtracaoInteracoes
from extracao_interacoes.validacao import eh_limite_principal, localizar_fallback_estrutural
from helpers import criar_documento, configuracao, MedidorFalso, ProvedorFalso
from test_pipeline import RepositorioFalso, RelatorioFalso, bula
from extracao_interacoes.pipeline import processar_lote


class TestSecoesCurtasELimites(unittest.TestCase):
    def servico(self, documento, respostas):
        provedor = ProvedorFalso(respostas)
        return ServicoExtracaoInteracoes(
            provedor, configuracao(modo_extracao="rapido"),
            leitor=lambda _: documento, fabrica_medidor=MedidorFalso,
        ), provedor

    def test_ad_furp_resposta_curta_e_literal_e_gravada(self):
        for corpo in ("Não existem.", "Nenhuma.", "Não se aplica.", "Desconhecidas."):
            with self.subTest(corpo=corpo):
                textos = ["6. INTERAÇÕES MEDICAMENTOSAS", corpo,
                          "7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO"]
                servico, provedor = self.servico(criar_documento(textos), [{"confirmado": True}])
                banco = RepositorioFalso()
                resumo = processar_lote([bula()], servico, banco, RelatorioFalso())
                self.assertEqual(resumo, {"CONCLUIDO": 1})
                self.assertEqual(banco.gravacoes[0][1], "\n".join(textos[:2]))
                self.assertEqual(len(provedor.chamadas), 1)

    def test_titulo_vazio_rotulos_e_fragmentos_nao_sao_corpo_curto(self):
        for corpo in ("", "Não", "123456", "01/01/2026", "POSOLOGIA", "INTERAÇÕES MEDICAMENTOSAS"):
            with self.subTest(corpo=corpo):
                doc = criar_documento(["6. INTERAÇÕES MEDICAMENTOSAS", corpo,
                                       "7. CUIDADOS DE ARMAZENAMENTO"])
                self.assertEqual(localizar_fallback_estrutural(doc), [])

    def test_resposta_curta_sem_titulo_nao_cria_secao(self):
        self.assertEqual(localizar_fallback_estrutural(
            criar_documento(["Não existem.", "7. CUIDADOS DE ARMAZENAMENTO"])
        ), [])

    def test_resposta_curta_ainda_depende_de_confirmacao(self):
        doc = criar_documento(["6. INTERAÇÕES MEDICAMENTOSAS", "Não existem.",
                               "7. CUIDADOS DE ARMAZENAMENTO"])
        servico, provedor = self.servico(doc, [{"confirmado": False}])
        resultado = servico.extrair("teste", "1", None, Path("bula.pdf"))
        self.assertEqual(resultado.status, StatusExtracao.REVISAO_MANUAL)
        self.assertIsNone(resultado.trecho_interacoes)
        self.assertEqual(len(provedor.chamadas), 1)

    def test_adenon_titulos_sem_numero_preservam_todo_o_corpo(self):
        textos = [
            "INTERAÇÕES MEDICAMENTOSAS",
            "Até o momento não houve relatos de interações medicamentosas.",
            "CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO",
            "Texto que nao pertence as interacoes.",
        ]
        doc = criar_documento(textos)
        self.assertEqual(preparar_confirmacao(doc)[1], "L000003")
        servico, provedor = self.servico(doc, [{"confirmado": True}])
        resultado = servico.extrair("teste", "1", None, Path("bula.pdf"))
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.trecho_interacoes, "\n".join(textos[:2]))
        self.assertEqual(len(provedor.chamadas), 1)

    def test_afluv_posologia_no_paragrafo_nao_corta_a_continuacao(self):
        textos = [
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "Recomenda-se vigilancia e reducao na",
            "posologia de ropinirol durante tratamento com fluvoxamina e após sua interrupção.",
            "Interações farmacodinâmicas: texto da continuacao.",
            "Testes laboratoriais: texto do ultimo item.",
            "7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO",
        ]
        servico, provedor = self.servico(
            criar_documento(textos, paginas=[1, 1, 2, 2, 2, 2]), [{"confirmado": True}]
        )
        resultado = servico.extrair("teste", "1", None, Path("bula.pdf"))
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.linha_fim_exclusiva, "L000006")
        self.assertEqual(resultado.trecho_interacoes, "\n".join(textos[:5]))
        self.assertEqual(len(provedor.chamadas), 1)

    def test_prefixos_de_titulos_em_paragrafos_nao_sao_limites(self):
        for texto in (
            "posologia de ropinirol durante tratamento com fluvoxamina.",
            "POSOLOGIA DE ROPINIROL DURANTE TRATAMENTO COM FLUVOXAMINA.",
            "Reações adversas podem ocorrer durante a associacao.",
            "Armazenamento inadequado pode alterar o medicamento.",
            "Advertências e precauções devem ser observadas.",
            "Posologia e modo de usar devem ser avaliados.",
        ):
            with self.subTest(texto=texto):
                self.assertFalse(eh_limite_principal("6. INTERAÇÕES MEDICAMENTOSAS", texto))

    def test_titulos_completos_e_hierarquia_numerica_continuam_validos(self):
        for titulo in ("CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO",
                       "Posologia e modo de usar:", "REAÇÕES ADVERSAS",
                       "7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO"):
            with self.subTest(titulo=titulo):
                self.assertTrue(eh_limite_principal("6. INTERAÇÕES MEDICAMENTOSAS", titulo))
        self.assertFalse(eh_limite_principal("6. INTERAÇÕES MEDICAMENTOSAS", "5. ADVERTÊNCIAS"))
        self.assertFalse(eh_limite_principal("6. INTERAÇÕES MEDICAMENTOSAS", "6.1 Interações com alimentos"))
