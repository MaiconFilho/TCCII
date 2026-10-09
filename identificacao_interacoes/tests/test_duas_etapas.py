import json
import unittest
from identificacao_interacoes.servico import ServicoIdentificacaoInteracoes
from identificacao_interacoes.duas_etapas import validar_candidata, Candidata
from identificacao_interacoes.segmentacao import criar_unidades
from identificacao_interacoes.validacao import ContextoValidacao
from identificacao_interacoes.modelos import StatusProcessamento, Rejeicao, MotivoRejeicao
from .auxiliares import ProvedorFalso, MedidorFalso, RespostaFalsa, configuracao, fonte


def item(alvo='digoxina', trechos=('F1',), efeito=('F1',)):
    return dict(alvo=alvo, tipo_alvo='PRINCIPIO_ATIVO', trechos=list(trechos), efeito=list(efeito))


def resposta(*frases):
    return {'frases': [{'id': f'F{i+1}', 'relacoes': list(r)} for i, r in enumerate(frases)]}


def resposta_por_id(relacoes):
    return {'frases': [{'id': i, 'relacoes': r} for i, r in relacoes.items()]}


def resumos(mensagens):
    dados = json.loads(mensagens[-1]['content'])
    return {'resumos': [{'id': r['id'], 'resumo': dados['passagens'][r['passagem']]}
                        for r in dados['relacoes']]}


def executar(texto, respostas, **cfg):
    p = ProvedorFalso(respostas)
    s = ServicoIdentificacaoInteracoes(p, configuracao(estrategia='duas_etapas', **cfg), MedidorFalso)
    return s.identificar(fonte(texto)), p


class TestDuasEtapas(unittest.TestCase):
    def test_sem_resumo_na_identificacao_e_lote_com_multiplos_alvos(self):
        texto = 'Digoxina e cafeína aumentam o risco de toxicidade.'
        r, p = executar(texto, [resposta([item(), item('cafeína')]), resumos])
        self.assertEqual(len(r.relacoes), 2)
        self.assertEqual(r.chamadas_llm, 2)
        self.assertNotIn('resumo', json.dumps(p.esquemas[0]))
        self.assertEqual(r.resumos_gerados, 2)
        self.assertIsNone(r.metricas_chamadas[0]['tempo_geracao_segundos'])

    def test_frase_com_efeitos_distintos_nao_compartilha_resumo(self):
        texto = 'Digoxina aumenta a toxicidade; cafeína reduz a absorção.'
        def gerar(m):
            rs = json.loads(m[-1]['content'])['relacoes']
            return {'resumos': [{'id': x['id'], 'resumo': 'Aumenta a toxicidade.' if x['alvo']=='digoxina'
                                else 'Reduz a absorção.'} for x in rs]}
        r, _ = executar(texto, [resposta([item(), item('cafeína')]), gerar])
        self.assertEqual(r.status, StatusProcessamento.CONCLUIDO_COM_RELACOES)
        self.assertNotEqual(r.relacoes[0].resumo, r.relacoes[1].resumo)

    def test_resumo_com_efeito_de_outro_alvo_e_rejeitado(self):
        texto = 'Digoxina aumenta a toxicidade; cafeína reduz a absorção.'
        def trocar(m):
            rs = json.loads(m[-1]['content'])['relacoes']
            return {'resumos': [{'id': x['id'], 'resumo': 'Reduz a absorção.'} for x in rs]}
        r, _ = executar(texto, [resposta([item()]), trocar], tentativas_correcao=0)
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)
        self.assertFalse(r.relacoes)

    def test_antecedente_e_referencia_formam_recorte_continuo(self):
        texto = 'Digoxina e cafeína são citadas. Esses medicamentos aumentam a toxicidade.'
        r, _ = executar(texto, [resposta([], [item(trechos=('F1','F2'), efeito=('F2',))]), resumos])
        self.assertEqual(r.relacoes[0].evidencia, texto)

    def test_cabecalho_de_lista_pode_conter_alvo(self):
        texto = 'DIGOXINA\nAumenta a toxicidade.'
        r, _ = executar(texto, [resposta([], [item(trechos=('F1','F2'), efeito=('F2',))]), resumos])
        self.assertEqual(r.relacoes[0].evidencia, texto)

    def test_alvo_em_frase_vizinha_independente_nao_basta(self):
        texto = 'Digoxina é mencionada. Cafeína aumenta a toxicidade.'
        r, _ = executar(texto, [resposta([], [item(trechos=('F1','F2'), efeito=('F2',))])], tentativas_correcao=0)
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)
        self.assertFalse(r.relacoes)

    def test_nao_une_frases_distantes(self):
        texto = 'Digoxina aumenta a toxicidade. Cafeína é citada. Esses medicamentos reduzem a absorção.'
        r, _ = executar(texto, [resposta([], [], [item(trechos=('F1','F3'), efeito=('F3',))])], tentativas_correcao=0)
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)

    def test_mesmo_alvo_com_condicoes_distintas_e_preservado(self):
        texto = 'Digoxina aumenta a toxicidade com 10 mg diariamente. Digoxina reduz a absorção com 5 mg diariamente.'
        r, _ = executar(texto, [resposta([item()], [item(trechos=('F2',), efeito=('F2',))]), resumos])
        self.assertEqual(len(r.relacoes), 2)
        self.assertEqual(r.resumos_gerados, 2)

    def test_repeticao_exata_e_consolidada_antes_de_resumir(self):
        r, _ = executar('Digoxina aumenta a toxicidade.', [resposta([item(), item()]), resumos])
        self.assertEqual(r.aceitas_antes_consolidacao, 2)
        self.assertEqual(r.duplicatas_consolidadas, 1)
        self.assertEqual(r.resumos_gerados, 1)

    def test_correcao_limitada_nao_declara_sucesso_completo(self):
        r, p = executar('Cafeína aumenta a toxicidade.', [resposta([item()]), resposta([item()])])
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)
        self.assertEqual(len(p.chamadas), 2)
        self.assertEqual(r.metricas_chamadas[1]['finalidade'], 'correcao')

    def test_correcao_especifica_nao_recebe_janela_inteira(self):
        texto = 'Cafeína aumenta a toxicidade. Outra frase independente. Uma terceira frase independente.'
        r, p = executar(texto, [resposta([item()], [], []), resposta([])])
        self.assertNotIn('terceira', p.chamadas[1][-1]['content'])
        self.assertTrue(r.rejeicoes)

    def test_id_de_resumo_errado_nao_e_publicado(self):
        r, _ = executar('Digoxina aumenta a toxicidade.', [resposta([item()]),
            {'resumos': [{'id': 'outra', 'resumo': 'Aumenta a toxicidade.'}]}])
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)

    def test_quantidade_e_negacao_perdidas_no_resumo(self):
        for texto in ('Digoxina aumenta a toxicidade acima de 10 mg.', 'Digoxina não deve ser usada devido à toxicidade.'):
            def perder(m):
                i = json.loads(m[-1]['content'])['relacoes'][0]['id']
                return {'resumos': [{'id': i, 'resumo': 'Toxicidade.'}]}
            r, _ = executar(texto, [resposta([item()]), perder], tentativas_correcao=0)
            self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)

    def test_truncamento_sem_divisao_possivel_e_falha_sem_relacoes(self):
        # Como no fluxo atual: janela sem cobertura completa não vira conclusão.
        r, _ = executar('Digoxina aumenta a toxicidade.', [RespostaFalsa('{}', truncada=True)])
        self.assertEqual(r.status, StatusProcessamento.FALHA)
        self.assertFalse(r.relacoes)
        self.assertIn('RespostaTruncadaError', r.detalhe_erro)

    def test_ausencia_nao_vira_positiva(self):
        r, p = executar('Não foram realizados estudos com digoxina.', [resposta([item()])])
        self.assertFalse(r.relacoes)
        self.assertEqual(len(p.chamadas), 1)

    def test_alvo_sem_cadastro_nao_exige_banco(self):
        r, _ = executar('Compostoalfa aumenta a toxicidade.',
                        [resposta([item('compostoalfa')]), resumos], tentativas_correcao=0)
        self.assertEqual(r.status, StatusProcessamento.CONCLUIDO_COM_RELACOES)
        self.assertEqual(len(r.relacoes), 1)

    def test_pendencia_mesmo_alvo_outra_evidencia_nao_e_resolvida(self):
        from identificacao_interacoes.modelos import RelacaoIdentificada, TipoAlvo
        rejeicao = Rejeicao(MotivoRejeicao.RESUMO_OMITE_QUANTIDADE, 'digoxina', 'PRINCIPIO_ATIVO',
                           '', 'Digoxina com 10 mg aumenta a toxicidade.', '', 0)
        aceita = RelacaoIdentificada('digoxina', TipoAlvo.PRINCIPIO_ATIVO, 'Reduz absorção.',
                                    'Digoxina reduz absorção.', 100, 130, 1)
        self.assertEqual(ServicoIdentificacaoInteracoes._pendentes_sem_relacao([rejeicao], [aceita]), [rejeicao])


class TestDuasEtapasRegressao(unittest.TestCase):
    """Casos da otimização: cada teste fixa um erro observado ou um limite do fluxo."""

    @staticmethod
    def roteiro(texto_alvo='Digoxina', alvo='digoxina', resumo='Aumenta a toxicidade.'):
        """Responde conforme a etapa: relação nas frases que citam o alvo e resumo fixo."""
        from identificacao_interacoes.duas_etapas import SISTEMA_B

        provedor = ProvedorFalso()

        def responder(mensagens):
            if mensagens[0]['content'] == SISTEMA_B:
                pedido = json.loads(mensagens[-1]['content'])
                return {'resumos': [{'id': r['id'], 'resumo': resumo} for r in pedido['relacoes']]}
            esquema = provedor.esquemas[-1]['properties']['frases']['prefixItems']
            nucleos = [f['properties']['id']['const'] for f in esquema]
            linhas = dict(l[1:].split('] ', 1) for l in mensagens[-1]['content'].splitlines()
                          if l.startswith('[F'))
            return resposta_por_id({i: [item(alvo, (i,), (i,))] if texto_alvo in linhas[i] else []
                                    for i in nucleos})

        provedor.respostas = [responder] * 20
        return provedor

    @staticmethod
    def executar(provedor, texto, **cfg):
        s = ServicoIdentificacaoInteracoes(provedor, configuracao(estrategia='duas_etapas', **cfg), MedidorFalso)
        return s.identificar(fonte(texto))

    def test_efeito_fora_da_frase_listada_e_rejeitado(self):
        texto = 'Digoxina é citada aqui. Aumenta a toxicidade.'
        r, _ = executar(texto, [resposta([item(trechos=('F1',), efeito=('F2',))], [])], tentativas_correcao=0)
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)
        self.assertIn('precisa incluir a frase F1', r.rejeicoes_pendentes[0].detalhe)

    def test_relacao_repetida_em_janelas_sobrepostas_resume_uma_vez(self):
        texto = 'Frase um qualquer. Digoxina aumenta a toxicidade. Frase tres sem alvo aqui. Frase quatro.'
        r = self.executar(self.roteiro(), texto, tokens_janela=10, sobreposicao_tokens=5)
        self.assertGreater(r.quantidade_janelas, 1)
        self.assertEqual(r.aceitas_antes_consolidacao, 2)
        self.assertEqual(r.duplicatas_consolidadas, 1)
        self.assertEqual((r.resumos_gerados, len(r.relacoes)), (1, 1))
        self.assertEqual(r.status, StatusProcessamento.CONCLUIDO_COM_RELACOES)

    def test_secao_repetida_com_mesmo_texto_vira_uma_relacao(self):
        texto = 'Digoxina aumenta a toxicidade.\nOutra seção do mesmo PDF.\nDigoxina aumenta a toxicidade.'
        r = self.executar(self.roteiro(), texto)
        self.assertEqual(r.aceitas_antes_consolidacao, 2)
        self.assertEqual(len(r.relacoes), 1)
        self.assertEqual(r.resumos_gerados, 1)

    def test_passagem_compartilhada_vai_uma_vez_no_lote_de_resumos(self):
        texto = 'Digoxina e cafeína aumentam o risco de toxicidade.'
        r, p = executar(texto, [resposta([item(), item('cafeína')]), resumos])
        pedido = json.loads(p.chamadas[1][-1]['content'])
        self.assertEqual(len(pedido['passagens']), 1)
        self.assertEqual({x['passagem'] for x in pedido['relacoes']}, {'P1'})
        self.assertEqual(len(r.relacoes), 2)

    def test_descarte_de_alvo_presente_no_texto_fica_em_revisao(self):
        # Referência contextual possível: o modelo não pode sumir com ela.
        texto = 'Digoxina é citada. Cafeína aumenta a toxicidade.'
        r, p = executar(texto, [resposta([], [item(trechos=('F2',), efeito=('F2',))]),
                                {'correcoes': [{'caso': 'C0', 'relacoes': []}]}])
        self.assertEqual(len(p.chamadas), 2)
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)
        self.assertEqual(r.rejeicoes_pendentes[0].alvo, 'digoxina')

    def test_descarte_de_alvo_inexistente_no_texto_e_registrado(self):
        r, _ = executar('Cafeína aumenta a toxicidade.', [resposta([item()]),
                        {'correcoes': [{'caso': 'C0', 'relacoes': []}]}])
        self.assertEqual(r.status, StatusProcessamento.CONCLUIDO_SEM_RELACOES)
        self.assertEqual(r.rejeicoes_por_etapa['correcao'], {'DESCARTADA_PELO_MODELO_ALVO_FORA_DO_TEXTO': 1})
        self.assertTrue(r.rejeicoes)

    def test_correcao_recupera_referencia_contextual_com_contexto_local(self):
        texto = 'Digoxina e cafeína são citadas. Esses medicamentos aumentam a toxicidade.'
        corrigida = item(trechos=('F1', 'F2'), efeito=('F2',))
        r, p = executar(texto, [resposta([], [item(trechos=('F2',), efeito=('F2',))]),
                                {'correcoes': [{'caso': 'C0', 'relacoes': [corrigida]}]}, resumos])
        pedido = json.loads(p.chamadas[1][-1]['content'])
        self.assertEqual(set(pedido['casos'][0]['frases']), {'F1', 'F2'})
        self.assertEqual(r.status, StatusProcessamento.CONCLUIDO_COM_RELACOES)
        self.assertEqual(r.relacoes[0].evidencia, texto)

    def test_mesma_falha_nao_e_corrigida_duas_vezes(self):
        r, p = executar('Cafeína aumenta a toxicidade.', [resposta([item()]),
                        {'correcoes': [{'caso': 'C0', 'relacoes': [item()]}]}], tentativas_correcao=2)
        self.assertEqual(len(p.chamadas), 2)  # a repetição idêntica não ganha nova rodada
        self.assertEqual(r.status, StatusProcessamento.REVISAO_NECESSARIA)
        self.assertEqual(len(r.rejeicoes_pendentes), 1)

    def test_truncamento_divide_a_janela_e_conclui(self):
        texto = 'Primeira frase sem alvo. Segunda frase sem alvo.'
        p = self.roteiro()
        p.respostas.insert(0, RespostaFalsa('{}', truncada=True))
        r = self.executar(p, texto)
        self.assertEqual(r.divisoes_por_limite, 1)
        self.assertEqual(r.status, StatusProcessamento.CONCLUIDO_SEM_RELACOES)
        self.assertEqual(r.metricas_chamadas[0]['encerramento'], 'length')

    def test_metricas_separam_extracao_correcao_e_resumo(self):
        from identificacao_interacoes.metricas import agregar

        texto = 'Digoxina e cafeína são citadas. Esses medicamentos aumentam a toxicidade.'
        corrigida = item(trechos=('F1', 'F2'), efeito=('F2',))
        r, _ = executar(texto, [resposta([], [item(trechos=('F2',), efeito=('F2',))]),
                                {'correcoes': [{'caso': 'C0', 'relacoes': [corrigida]}]}, resumos])
        grupos = agregar(r.metricas_chamadas)
        self.assertEqual(set(grupos), {'identificacao/extracao',
                                       'identificacao/correcao/ALVO_AUSENTE_NA_EVIDENCIA', 'resumo/resumo'})
        self.assertEqual(r.propostas_brutas, 1)  # só a leitura principal
        self.assertEqual(grupos['identificacao/correcao/ALVO_AUSENTE_NA_EVIDENCIA']['propostas'], 1)
        self.assertIsNone(grupos['resumo/resumo']['taxa_geracao_tokens_s'])  # sem tempo do backend

    def test_versao_identifica_fluxo_e_validacao(self):
        from identificacao_interacoes.duas_etapas import versao_fluxo
        from identificacao_interacoes.validacao import VERSAO_VALIDACAO

        versao = versao_fluxo(configuracao(estrategia='duas_etapas'))
        self.assertTrue(versao.startswith('duas-etapas/2#'))
        self.assertTrue(versao.endswith(VERSAO_VALIDACAO))
