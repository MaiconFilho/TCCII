"""Regressoes dos limites, rodapes e persistencia de secoes completas."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.modelos import StatusExtracao
from extracao_interacoes.servico import ServicoExtracaoInteracoes
from extracao_interacoes.validacao import eh_limite_principal, corpo_apenas_referencias
from helpers import criar_documento, configuracao, MedidorFalso, ProvedorFalso
from test_pipeline import RepositorioFalso, RelatorioFalso, bula
from extracao_interacoes.pipeline import processar_lote


class TestRegressoesAcog(unittest.TestCase):
    def test_referencia_quebrada_nao_corta_e_duas_secoes_chegam_ao_banco(self):
        textos = []
        partes = []
        for apresentacao in ("adultos", "pediatrica"):
            secao = [
                "6. INTERAÇÕES MEDICAMENTOSAS",
                f"Informacoes de interacoes da apresentacao {apresentacao}.",
                "Pode haver resposta mais pronunciada (vide item 5.",
                "ADVERTÊNCIAS E PRECAUÇÕES).",
                "Ao converter pacientes de varfarina: texto que precisa continuar.",
                "Ultima interacao com medicamentos desta apresentacao.",
            ]
            partes.append("\n".join(secao))
            textos.extend(secao + ["7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO"])
        documento = criar_documento(textos)
        provedor = ProvedorFalso([{"confirmado": True}, {"confirmado": True}])
        servico = ServicoExtracaoInteracoes(
            provedor, configuracao(modo_extracao="rapido"),
            leitor=lambda _: documento, fabrica_medidor=MedidorFalso,
        )
        banco = RepositorioFalso()
        resumo = processar_lote([bula()], servico, banco, RelatorioFalso())
        self.assertEqual(resumo, {"CONCLUIDO": 1})
        self.assertEqual(len(banco.gravacoes), 1)
        self.assertEqual(banco.gravacoes[0][1], "\n\n".join(partes))
        self.assertEqual(len(provedor.chamadas), 2)

    def test_parenteses_balanceados_no_titulo_nao_sao_referencia(self):
        self.assertTrue(eh_limite_principal(
            "6. INTERAÇÕES MEDICAMENTOSAS", "7. CUIDADOS DE ARMAZENAMENTO (CONSERVAÇÃO)"
        ))

    def test_rodape_vps_e_palavra_similar_nao_descartam_texto_clinico(self):
        self.assertFalse(corpo_apenas_referencias([
            "Nao foram relatadas interacoes com drogas topicas.",
            "O efeito foi similar com outros medicamentos.",
            "ACULAR LS - VPS - BU02 Pag. 4 de 6",
        ]))

    def test_corpo_sem_fim_confirmavel_vai_para_revisao_sem_varredura(self):
        doc = criar_documento([
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "Texto de interacoes sem proximo titulo principal.",
        ])
        provedor = ProvedorFalso([])
        servico = ServicoExtracaoInteracoes(
            provedor, configuracao(modo_extracao="rapido"),
            leitor=lambda _: doc, fabrica_medidor=MedidorFalso,
        )
        resultado = servico.extrair("teste", "1", None, Path("bula.pdf"))
        self.assertEqual(resultado.status, StatusExtracao.REVISAO_MANUAL)
        self.assertEqual(provedor.chamadas, [])
