import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pymupdf
from extracao_interacoes.layout_pdf import separar_cabecalhos, ordenar_colunas
from extracao_interacoes.validacao import localizar_titulos_interacoes, localizar_fallback_estrutural, paginas_administrativas, copiar_e_validar_trecho
from helpers import criar_documento

class TestLayoutETriagem(unittest.TestCase):
    def test_titulo_sozinho_nao_e_dividido(self):
        for titulo in ('6. INTERAÇÕES MEDICAMENTOSAS','7. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO'):
            self.assertEqual(separar_cabecalhos(titulo),titulo)

    def test_titulo_colado_ao_corpo_e_subtitulo(self):
        for linha,esperado in (
            ('6. INTERAÇÕES MEDICAMENTOSAS: A associacao pode alterar o efeito.','6. INTERAÇÕES MEDICAMENTOSAS:\nA associacao pode alterar o efeito.'),
            ('6. INTERAÇÕES MEDICAMENTOSAS Interações farmacocinéticas','6. INTERAÇÕES MEDICAMENTOSAS\nInterações farmacocinéticas'),
            ('6. INTERAÇÕES MEDICAMENTOSASINTERAÇÕES FARMACODINÂMICAS','6. INTERAÇÕES MEDICAMENTOSAS\nINTERAÇÕES FARMACODINÂMICAS')):
            self.assertEqual(separar_cabecalhos(linha),esperado)

    def test_referencia_nao_e_partida_em_titulo_falso(self):
        texto='8. POSOLOGIA E MODO DE USAR e 5. ADVERTÊNCIAS E PRECAUÇÕES).'
        self.assertEqual(separar_cabecalhos(texto),texto)

    def test_complemento_do_titulo_e_preservado(self):
        texto='6. INTERAÇÕES MEDICAMENTOSAS E OUTRAS FORMAS DE INTERAÇÃO'
        self.assertEqual(separar_cabecalhos(texto),texto)

    def test_ordem_de_tres_colunas_desiguais(self):
        linhas=[]
        for x,lado in ((20,'esquerda'),(220,'centro'),(510,'direita')):
            for i in range(8):
                linhas.append((pymupdf.Rect(x,50+i*15,x+170,60+i*15),f'{lado} {i} texto de um paragrafo normal'))
        ordenadas=ordenar_colunas(sorted(linhas,key=lambda l:l[0].y0))
        self.assertIsNotNone(ordenadas)
        self.assertEqual([t.split()[0] for _,t in ordenadas],['esquerda']*8+['centro']*8+['direita']*8)

    def test_tabela_de_valores_nao_e_ordenada_como_prosa(self):
        linhas=[(pymupdf.Rect(x,50+i*15,x+60,60+i*15),str(i)) for x in (20,220) for i in range(8)]
        self.assertIsNone(ordenar_colunas(linhas))

    def test_respostas_curtas_validas_com_corpo(self):
        for corpo in ('Não aplicável.','Não são conhecidas.','Não apresenta.'):
            doc=criar_documento(['6. INTERAÇÕES MEDICAMENTOSAS',corpo,'7. CUIDADOS DE ARMAZENAMENTO'])
            self.assertEqual(len(localizar_fallback_estrutural(doc)),1)

    def test_historico_e_continuacao_nao_bloqueiam_secao_real(self):
        textos=['6. INTERAÇÕES MEDICAMENTOSAS','A associacao pode alterar o efeito de outros medicamentos.',
            '7. CUIDADOS DE ARMAZENAMENTO',
            'Dados da submissão eletrônica; Dados da petição/notificação; Itens de bula; Data de aprovação',
            '6. INTERAÇÕES MEDICAMENTOSAS',
            'Notificação de Alteração de texto de Bula RDC 60/12 VP VPS',
            '6. Interações medicamentosas','Padronização interna; VPS; 7. Cuidados de armazenamento']
        doc=criar_documento(textos,[1,1,1,2,2,3,3,3])
        self.assertEqual(paginas_administrativas(doc),{2,3})
        self.assertEqual(len(localizar_titulos_interacoes(doc)),1)
        self.assertEqual(len(localizar_fallback_estrutural(doc)),1)

    def test_ultima_pagina_do_historico_pode_nao_repetir_cabecalho(self):
        textos = [
            'Dados da submissao eletronica; Dados da peticao/notificacao; Itens de bula; Data de aprovacao',
            'Historico de alteracoes de texto de bula; Notificacao RDC 60/12; Expediente; VP; VPS; 01/01/2025; 02/02/2025',
            '10452 GENERICO Notificacao Bula RDC 60/12 VP VPS Apresentacoes relacionadas',
            '6. INTERACOES MEDICAMENTOSAS',
        ]
        doc = criar_documento(textos, [1, 1, 2, 2])
        self.assertEqual(paginas_administrativas(doc), {1, 2})
        self.assertFalse(localizar_titulos_interacoes(doc))

    def test_pontuacao_duplicada_entre_palavras_do_titulo(self):
        doc = criar_documento([
            '6. INTERACOES..MEDICAMENTOSAS',
            'Antiacidos podem alterar a absorcao de outros medicamentos.',
            '7. CUIDADOS DE ARMAZENAMENTO',
        ])
        candidatos = localizar_fallback_estrutural(doc)
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0][0], 'L000001')

    def test_eventos_adversos_e_limite_da_secao_de_interacoes(self):
        for limite in (
            'EVENTOS ADVERSOS E ALTERACOES DE EXAMES LABORATORIAIS',
            'EVENTOS ADVERSOS E ALTERACOES DE EXAMES LABORATORIAS',
        ):
            with self.subTest(limite=limite):
                doc = criar_documento([
                    'INTERACOES MEDICAMENTOSAS',
                    'O uso concomitante pode causar hipertensao severa persistente.',
                    limite,
                    'As reacoes adversas podem ser sistemicas.',
                ])
                candidatos = localizar_fallback_estrutural(doc)
                self.assertEqual(len(candidatos), 1)
                self.assertEqual(candidatos[0][1], 'L000003')
                trecho = copiar_e_validar_trecho(doc, *candidatos[0])
                self.assertNotIn('EVENTOS ADVERSOS', trecho)

    def test_tabela_clinica_com_datas_e_notificacao_nao_e_historico(self):
        doc=criar_documento(['6. INTERAÇÕES MEDICAMENTOSAS','Resultados 01/01/2025 e 02/02/2025: anticoagulantes aumentam o risco.',
            'Notificação de eventos segundo RDC; VPS.','7. CUIDADOS DE ARMAZENAMENTO'])
        self.assertFalse(paginas_administrativas(doc))

    def test_subtitulo_em_advertencias_nao_substitui_topico_principal(self):
        doc=criar_documento(['5. ADVERTÊNCIAS E PRECAUÇÕES','Interações medicamentosas','Ver tambem o item 6.',
            '6. INTERAÇÕES MEDICAMENTOSAS','Texto real da secao de interacoes medicamentosas.','7. CUIDADOS DE ARMAZENAMENTO'])
        self.assertEqual([t[0] for t in localizar_titulos_interacoes(doc)],[3])

    def test_referencia_entre_paginas_nao_trunca_secao(self):
        doc=criar_documento(['6. INTERAÇÕES MEDICAMENTOSAS','O uso combinado requer cuidado (veja os itens',
            '8. POSOLOGIA E MODO DE USAR','e 5. ADVERTÊNCIAS E PRECAUÇÕES).',
            'Ainda ha outras interacoes importantes no final.', '7. CUIDADOS DE ARMAZENAMENTO'])
        candidato=localizar_fallback_estrutural(doc)[0]
        self.assertEqual(candidato[1],'L000006')
        self.assertIn('Ainda ha outras',copiar_e_validar_trecho(doc,*candidato))

    def test_variantes_fechadas_nao_aceitam_referencia_na_prosa(self):
        for titulo in ('6.   6. INTERAÇÕES MEDICAMENTOSAS','6. INTERAÇÕES MEDICAMENTOSAS).','6. INTERAÇÕES MEDICAMENTOSAS?','5. INTERAÇÕES MEDICAMENTOSOSAS'):
            doc=criar_documento([titulo,'Pode alterar o efeito de outro medicamento.','7. CUIDADOS DE ARMAZENAMENTO'])
            self.assertTrue(localizar_fallback_estrutural(doc))
        self.assertFalse(localizar_titulos_interacoes(criar_documento(['INTERAÇÕES MEDICAMENTOSAS).'])))
