import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from extracao_interacoes.validacao import eh_limite_principal, localizar_fallback_estrutural, copiar_e_validar_trecho
from helpers import criar_documento

class TestLimitesParenteses(unittest.TestCase):
    def test_vibral_corpo_curto_e_numeracao_com_parentese(self):
        doc=criar_documento(['6) INTERAÇÕES MEDICAMENTOSAS',
            'Álcool e depressores do SNC podem potencializar efeitos colaterais como hipotensão ortostática e sonolência.',
            '7) CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO', 'Conservar em temperatura ambiente.'])
        candidatos=localizar_fallback_estrutural(doc)
        self.assertEqual(len(candidatos),1)
        inicio,fim,titulo=candidatos[0]
        self.assertEqual(fim,'L000003')
        self.assertEqual(copiar_e_validar_trecho(doc,inicio,fim,titulo),'\n'.join(l.texto_original for l in doc.linhas[:2]))

    def test_referencia_fechada_ainda_nao_encerra_secao(self):
        for texto in ['ADVERTÊNCIAS E PRECAUÇÕES).','7) CUIDADOS DE ARMAZENAMENTO).','(vide item 7) CUIDADOS DE ARMAZENAMENTO']:
            self.assertFalse(eh_limite_principal('6) INTERAÇÕES MEDICAMENTOSAS',texto))
        self.assertTrue(eh_limite_principal('6) INTERAÇÕES MEDICAMENTOSAS','7) CUIDADOS DE ARMAZENAMENTO'))
