import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import RespostaInvalidaError
from extracao_interacoes.modelos import MetodoExtracao, StatusExtracao
from extracao_interacoes.modo_rapido import preparar_confirmacao
from extracao_interacoes.servico import ServicoExtracaoInteracoes
from extracao_interacoes.validacao import copiar_e_validar_trecho, eh_limite_principal
from helpers import (
    MedidorFalso, ProvedorFalso, configuracao, criar_documento,
    resposta_secao, resposta_fim, resposta_nao_encontrado,
)


def documento_aas():
    return criar_documento([
        "6. INTERAÇÕES MEDICAMENTOSAS",
        "Interações contraindicadas:",
        "Metotrexato em doses elevadas: texto oficial da primeira combinacao.",
        "Combinações que requerem precauções para o uso:",
        "Metotrexato em doses inferiores: texto oficial que precisa ser mantido.",
        "Digoxina: texto oficial da interacao que precisa ser mantido.",
        "Uricosúricos como benzbromarona, probenecida: texto do ultimo item.",
        "7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO",
        "Armazenar em temperatura ambiente.",
    ], paginas=[1, 1, 2, 2, 2, 2, 2, 2, 2])


class TestModoRapido(unittest.TestCase):
    def executar(self, documento, respostas, modo="rapido"):
        provedor = ProvedorFalso(respostas)
        servico = ServicoExtracaoInteracoes(
            provedor, configuracao(modo_extracao=modo),
            leitor=lambda _: documento, fabrica_medidor=MedidorFalso,
        )
        return servico.extrair("aas", "001", "002", Path("bula.pdf")), provedor

    def test_aas_completo_em_uma_chamada_inclui_subtopicos_e_ultima_interacao(self):
        doc = documento_aas()
        resultado, provedor = self.executar(doc, [{"confirmado": True}])
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.metodo, MetodoExtracao.HIBRIDO_LLM)
        self.assertEqual(resultado.quantidade_chamadas_llm, 1)
        self.assertEqual(len(provedor.chamadas), 1)
        self.assertEqual(resultado.trecho_interacoes, "\n".join(l.texto_original for l in doc.linhas[:7]))
        self.assertEqual(resultado.linha_fim_exclusiva, "L000008")

    def test_copia_rejeita_o_limite_incorreto_reportado_para_aas(self):
        with self.assertRaises(RespostaInvalidaError):
            copiar_e_validar_trecho(documento_aas(), "L000001", "L000004", "6. INTERAÇÕES MEDICAMENTOSAS")

    def test_subtitulos_itens_e_subnumeracao_nao_encerram_secao(self):
        for titulo in (
            "Combinações que requerem precauções para o uso:",
            "Interações contraindicadas:",
            "6.1 Interações com alimentos",
            "7. tomar o medicamento com agua",
            "AAS INFANTIL PODE ALTERAR O EFEITO DE OUTROS MEDICAMENTOS",
        ):
            with self.subTest(titulo=titulo):
                self.assertFalse(eh_limite_principal("6. INTERAÇÕES MEDICAMENTOSAS", titulo))
        self.assertTrue(eh_limite_principal("6. INTERAÇÕES MEDICAMENTOSAS", "7. CUIDADOS DE ARMAZENAMENTO"))
        self.assertTrue(eh_limite_principal("6. INTERAÇÕES MEDICAMENTOSAS", "7. CUIDADOS DE ARMAZENAMENTO:"))

    def test_confirmacao_negativa_nao_varre_documento_inteiro(self):
        resultado, provedor = self.executar(documento_aas(), [
            {"confirmado": False}, resposta_secao(titulo="6. INTERAÇÕES MEDICAMENTOSAS"),
            resposta_fim("L000008", "7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO", titulo_secao="6. INTERAÇÕES MEDICAMENTOSAS"),
        ])
        self.assertEqual(resultado.metodo, MetodoExtracao.REVISAO_MANUAL)
        self.assertEqual(len(provedor.chamadas), 1)
        self.assertTrue(resultado.avisos)

    def test_confirmacao_invalida_nao_aprova_o_atalho(self):
        resultado, _ = self.executar(documento_aas(), [
            "invalido", "invalido", resposta_secao(titulo="6. INTERAÇÕES MEDICAMENTOSAS"),
            resposta_fim("L000008", "7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO", titulo_secao="6. INTERAÇÕES MEDICAMENTOSAS"),
        ])
        self.assertEqual(resultado.metodo, MetodoExtracao.REVISAO_MANUAL)
        self.assertEqual(resultado.quantidade_chamadas_llm, 2)

    def test_sem_secao_nao_passa_pelo_atalho(self):
        doc = criar_documento(["Texto de bula sem secao de interacoes medicamentosas."])
        self.assertIsNone(preparar_confirmacao(doc))
        resultado, _ = self.executar(doc, [resposta_nao_encontrado()])
        self.assertEqual(resultado.status, StatusExtracao.SEM_SECAO_INTERACOES)

    def test_sumario_nao_passa_pelo_atalho(self):
        doc = criar_documento(["SUMÁRIO"] + [l.texto_original for l in documento_aas().linhas])
        self.assertIsNone(preparar_confirmacao(doc))

    def test_fim_sem_proximo_titulo_explicito_exige_modo_completo(self):
        doc = criar_documento([l.texto_original for l in documento_aas().linhas[:7]])
        self.assertIsNone(preparar_confirmacao(doc))

    def test_duas_copias_identicas_sao_consolidadas_antes_do_atalho(self):
        textos = [l.texto_original for l in documento_aas().linhas]
        doc = criar_documento(textos + textos)
        confirmacao = preparar_confirmacao(doc)
        self.assertIsNotNone(confirmacao)
        self.assertEqual(confirmacao[:3], (
            "L000001", "L000008", "6. INTERAÇÕES MEDICAMENTOSAS"
        ))

    def test_modo_completo_tambem_rejeita_subtitulo_e_preserva_corpo(self):
        errado = resposta_fim("L000004", "Combinações que requerem precauções para o uso:", titulo_secao="6. INTERAÇÕES MEDICAMENTOSAS")
        resultado, _ = self.executar(documento_aas(), [
            resposta_secao(titulo="6. INTERAÇÕES MEDICAMENTOSAS"), errado, errado,
        ], modo="completo")
        self.assertEqual(resultado.metodo, MetodoExtracao.FALLBACK_ESTRUTURAL)
        self.assertEqual(resultado.linha_fim_exclusiva, "L000008")
        self.assertIn("Uricosúricos", resultado.trecho_interacoes)

    def test_modo_invalido_e_rejeitado(self):
        with self.assertRaises(ValueError):
            configuracao(modo_extracao="desconhecido").validar()
