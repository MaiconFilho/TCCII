import copy
import json
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from pipelines.identificacao_interacoes.modelos import ConfiguracaoIdentificacao
from pipelines.resumos_interacoes.__main__ import argumentos
from pipelines.resumos_interacoes.prompts import agrupar_cards, extrativo, grupos_por_efeito
from pipelines.resumos_interacoes.servico import GeradorResumos, chave_efeito, molde, preencher, processar
from pipelines.resumos_interacoes.validacao import limpar, validar_resposta, validar_contexto_geral
from pipelines.resumos_interacoes.repositorio import necessita_correcao, necessita_correcao_geral

EVIDENCIA = "O uso de ácido acetilsalicílico com varfarina pode aumentar o risco de sangramento."
# Trecho com lista (não literal): o card de ácido acetilsalicílico vai ao LLM.
TRECHO = ("O uso de ácido acetilsalicílico, anti-inflamatórios, clopidogrel, dipiridamol, heparina ou "
          "trombolíticos com varfarina pode aumentar o risco de sangramento.")
# Lista de agentes (não extrativa): cada alvo recebe um card condensado.
LISTA = ("• A atividade da varfarina pode ser potencializada por amiodarona, cimetidina, fluconazol, "
         "metronidazol, paracetamol e, possivelmente, outros analgésicos anti-inflamatórios.")
# Um card de aumento gera um único grupo de efeito, logo um único parágrafo.
GERAL = ("A bula de Marevan descreve esta interação: " + EVIDENCIA + " A bula descreve essa interação durante o uso concomitante dos medicamentos,"
         " conforme o trecho de interações identificado.")


def resposta(texto, truncada=False):
    return SimpleNamespace(conteudo=json.dumps({"texto": texto}), truncada=truncada)


class ProvedorFalso:
    def __init__(self, saidas=None):
        self.saidas = list(saidas or [resposta(EVIDENCIA), resposta(GERAL)])
        self.entradas = []

    def contar_tokens(self, texto):
        return len(texto) // 4

    def analisar(self, mensagens, *, esquema, max_tokens=192):
        self.entradas.append((mensagens, esquema))
        valor = self.saidas.pop(0)
        if isinstance(valor, Exception):
            raise valor
        return valor


class RepositorioFalso:
    def __init__(self):
        self.atual = {"nome_normalizado": "marevan", "processamento_id": 1, "hash_trecho": "x",
                      "interacoes": [{"id": 10, "alvo": "ácido acetilsalicílico",
                                      "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": TRECHO}]}
        self.historico = []
        self.ocupado = False
        self.cards_gravados = []
        self.modelos_card = []

    def iniciar(self, fonte):
        pass

    def gravar_card(self, fonte, item, texto, modelo, tempo, erro=None, *, origem=None):
        self.cards_gravados.append((item["id"], texto, erro))
        self.modelos_card.append(modelo)
        if texto:
            for atual in self.atual["interacoes"]:
                if atual["id"] == item["id"]:
                    atual["resumo_card"] = texto

    @contextmanager
    def bloquear(self, nome):
        yield not self.ocupado

    def fonte(self, nome):
        return copy.deepcopy(self.atual)

    def gravar(self, *args):
        self.historico.append(copy.deepcopy(args))
        fonte, modelo, inicio, resultado, geral, cards = args
        if resultado["status"] == "CONCLUIDO":
            self.atual["resumo_geral"] = geral
            for item in self.atual["interacoes"]:
                if item["id"] in cards:
                    item["resumo_card"] = cards[item["id"]]


class GeradorSemGeral(GeradorResumos):
    """Falha do resumo geral fora dos parágrafos, que têm rede de segurança própria."""

    def gerar(self, nome, dados, *, geral=False, **opcoes):
        if geral:
            raise RuntimeError("erro no geral")
        return super().gerar(nome, dados, **opcoes)


class TestResumos(unittest.TestCase):
    def setUp(self):
        self.repo = RepositorioFalso()
        self.provedor = ProvedorFalso()
        self.config = ConfiguracaoIdentificacao()
        self.gerador = GeradorResumos(self.provedor, self.config, prompt_simplificado=True)

    def test_gera_card_e_geral_sem_alterar_evidencia(self):
        antes = copy.deepcopy(self.repo.atual)
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(resultado["chamadas_llm"], 2)
        self.assertEqual(resultado["quantidade_interacoes"], 1)
        self.assertEqual(self.repo.cards_gravados, [(10, EVIDENCIA, None)])
        self.assertEqual(self.repo.historico[0][-2], GERAL)
        self.assertEqual(self.repo.atual["interacoes"][0]["evidencia"], antes["interacoes"][0]["evidencia"])
        self.assertIn(TRECHO, self.provedor.entradas[0][0][-1]["content"])

    def test_idempotencia_nao_infera_nem_grava_novamente(self):
        processar(self.repo, "marevan", self.gerador)
        segundo = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(segundo["status"], "IGNORADO_JA_PROCESSADO")
        self.assertEqual(len(self.repo.historico), 1)
        self.assertEqual(len(self.provedor.entradas), 2)

    def test_reprocessar_explicito_substitui_campos(self):
        processar(self.repo, "marevan", self.gerador)
        antigo = copy.deepcopy(self.repo.historico[0])
        self.gerador.provedor = ProvedorFalso()
        processar(self.repo, "marevan", self.gerador, reprocessar=True)
        self.assertEqual(len(self.repo.historico), 2)
        self.assertEqual(self.repo.historico[0], antigo)

    def test_apenas_geral_faltando_reutiliza_cards(self):
        self.repo.atual["interacoes"][0]["resumo_card"] = EVIDENCIA
        self.gerador.provedor = ProvedorFalso([resposta(GERAL)])
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(resultado["chamadas_llm"], 1)
        self.assertEqual(self.repo.historico[0][-1], {})
        self.assertEqual(self.repo.atual["resumo_geral"], GERAL)

    def test_apenas_card_faltando_preserva_geral(self):
        self.repo.atual["resumo_geral"] = GERAL
        self.gerador.provedor = ProvedorFalso([resposta(EVIDENCIA)])
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(resultado["chamadas_llm"], 1)
        self.assertEqual(self.repo.atual["resumo_geral"], GERAL)

    def test_nulos_vazios_e_espacos_sao_pendentes(self):
        from pipelines.resumos_interacoes.repositorio import completo
        for vazio in (None, "", "  \n\t"):
            with self.subTest(vazio=vazio):
                fonte = {"resumo_geral": vazio, "interacoes": [{"resumo_card": EVIDENCIA}]}
                self.assertFalse(completo(fonte))
                fonte = {"resumo_geral": GERAL, "interacoes": [{"resumo_card": vazio}]}
                self.assertFalse(completo(fonte))

    def test_falha_geral_preserva_cards_gravados(self):
        resultado = processar(self.repo, "marevan", GeradorSemGeral(self.provedor, self.config))
        self.assertEqual(resultado["status"], "FALHA")
        self.assertIn("erro no geral", resultado["erro"])
        self.assertEqual(self.repo.historico[0][-1], {})
        self.assertTrue(resultado["gravacao_parcial"])
        self.assertEqual(self.repo.atual["interacoes"][0]["resumo_card"], EVIDENCIA)

    def test_paragrafo_que_falha_duas_vezes_usa_os_textos_dos_cards(self):
        self.provedor.saidas[1:] = [resposta(""), resposta("")]
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(self.repo.atual["resumo_geral"], "A bula de Marevan descreve estas interações: " + EVIDENCIA)

    def test_paragrafo_truncado_fica_ate_a_ultima_frase_completa(self):
        cortado = SimpleNamespace(conteudo='{"texto": "' + EVIDENCIA + " A bula cita outros casos. Ainda com", truncada=True)
        self.assertEqual(validar_resposta(cortado, EVIDENCIA, paragrafo=True),
                         EVIDENCIA + " A bula cita outros casos.")
        with self.assertRaisesRegex(ValueError, "truncada"):
            validar_resposta(cortado, EVIDENCIA)  # no card, truncado continua rejeitado

    def test_efeito_e_a_palavra_de_efeito_antes_do_medicamento(self):
        def chave(trecho, alvo):
            return chave_efeito({"evidencia": trecho, "alvo": alvo})
        oposto = "As concentrações podem aumentar (por exemplo, ciclosporina) ou diminuir (por exemplo, lamotrigina)."
        self.assertNotEqual(chave(oposto, "ciclosporina"), chave(oposto, "lamotrigina"))
        lista = ("Substâncias inibidoras desta enzima como paroxetina, fluoxetina e tioridazina podem levar "
                 "a um aumento dos níveis de nebivolol e a uma redução da frequência cardíaca.")
        self.assertEqual(chave(lista, "paroxetina"), chave(lista, "tioridazina"))

    def test_limpar_pontua_rotulos_e_remove_titulo_da_secao(self):
        # Evidências reais (Aerotrat e Acertanlo): título e rótulos ficavam colados na frase.
        casos = [
            ("INTERAÇÕES MEDICAMENTOSAS\nO salbutamol e as drogas ß-bloqueadoras não seletivas, como o "
             "propranolol, não devem ser prescritos\npara uso concomitante.",
             "O salbutamol e as drogas ß-bloqueadoras não seletivas, como o propranolol, não devem ser "
             "prescritos para uso concomitante."),
            ("Medicamentos que aumentam o risco de angioedema\nO uso concomitante de inibidores da ECA com "
             "sacubitril/valsartana está contraindicado pois aumenta o\nrisco de angioedema (veja itens 4 e 5).",
             "Medicamentos que aumentam o risco de angioedema. O uso concomitante de inibidores da ECA com "
             "sacubitril/valsartana está contraindicado pois aumenta o risco de angioedema (veja itens 4 e 5)."),
            ("É contraindicado o uso concomitante com:\nAlisquireno\nEm pacientes diabéticos, risco de hipercalemia.",
             "É contraindicado o uso concomitante com: Alisquireno. Em pacientes diabéticos, risco de hipercalemia."),
            ("Baclofeno\nAumento do efeito anti-hipertensivo.", "Baclofeno. Aumento do efeito anti-hipertensivo."),
            # Rótulo que a própria frase já nomeia sai.
            ("Lítio\nAumentos reversíveis das concentrações séricas de  lítio e toxicidade foram relatados.",
             "Aumentos reversíveis das concentrações séricas de lítio e toxicidade foram relatados."),
            # Quebra do PDF no meio da frase não vira ponto, mesmo antes de maiúscula.
            ("a utilização combinada de\nInibidores da ECA aumenta eventos adversos.",
             "a utilização combinada de Inibidores da ECA aumenta eventos adversos."),
            # Todo texto termina pontuado (os cards de um mesmo alvo são exibidos lado a lado).
            ("Diuréticos poupadores de potássio, potássio (sais)", "Diuréticos poupadores de potássio, potássio (sais)."),
        ]
        for evidencia, esperado in casos:
            with self.subTest(evidencia=evidencia):
                self.assertEqual(limpar(evidencia), esperado)

    def test_rejeita_eco_da_correcao_e_nome_generico_do_exemplo(self):
        # Respostas reais: geral da Aerotrat e card de alisquireno da Acertanlo.
        with self.assertRaisesRegex(ValueError, "instruções do prompt"):
            validar_resposta(resposta(EVIDENCIA + " A tentativa anterior foi rejeitada."), EVIDENCIA, paragrafo=True)
        with self.assertRaisesRegex(ValueError, "genérico"):
            validar_resposta(resposta("Amiodarona pode potencializar a atividade da varfarina e o efeito de X."), LISTA)
        com_vitamina = "O uso de vitamina C com varfarina pode aumentar o risco de sangramento."
        self.assertEqual(validar_resposta(resposta(com_vitamina), com_vitamina), com_vitamina)

    def test_nova_tentativa_acrescenta_o_motivo_ao_fim_do_pedido(self):
        self.gerador.provedor = ProvedorFalso([resposta(""), resposta(EVIDENCIA)])
        self.gerador.gerar("marevan", [{"alvo": "ácido acetilsalicílico", "tipo_alvo": "PRINCIPIO_ATIVO",
                                        "evidencia": TRECHO}])
        primeira, segunda = (e[0] for e in self.gerador.provedor.entradas)
        self.assertEqual(segunda[:-1], primeira[:-1])  # sistema e exemplos idênticos
        self.assertEqual(segunda[-1]["content"], primeira[-1]["content"]
                         + "\nAtenção: Resumo vazio ou fora do tamanho permitido (40–600).")

    def test_prompt_recebe_so_evidencia_real_sem_exemplos_ficticios(self):
        self.gerador.gerar("marevan", [{"alvo": "ácido acetilsalicílico", "tipo_alvo": "PRINCIPIO_ATIVO",
                                        "evidencia": TRECHO}])
        mensagens = self.provedor.entradas[0][0]
        self.assertEqual([m["role"] for m in mensagens], ["system", "user"])
        self.assertIn("ALVO: ácido acetilsalicílico", mensagens[-1]["content"])
        self.assertNotRegex(" ".join(m["content"] for m in mensagens), r"\b[ABDX]\b")

    def test_primeira_resposta_fica_se_a_nova_tentativa_falhar_apos_citar_outro_medicamento(self):
        # Caso real (Acertanlo): a nova tentativa trouxe "o efeito de X".
        primeira = "Amiodarona, como fluconazol, pode aumentar a ação da varfarina."
        self.gerador.provedor = ProvedorFalso([
            resposta(primeira), resposta("Amiodarona pode potencializar o efeito de X."),
        ])
        dados = [{"alvo": "amiodarona", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": LISTA}]
        self.assertEqual(self.gerador.gerar("marevan", dados, outros=("fluconazol",)), primeira)

    def test_limpar_remove_marcadores_de_lista(self):
        self.assertEqual(limpar("- Fentanila: o uso concomitante\n   pode resultar em hipotensão."),
                         "Fentanila: o uso concomitante pode resultar em hipotensão.")
        self.assertEqual(limpar("-digoxina: aumento das concentrações."), "digoxina: aumento das concentrações.")

    def test_card_que_cita_outro_medicamento_do_trecho_tem_uma_nova_tentativa(self):
        self.repo.atual["interacoes"] = [
            {"id": n, "alvo": alvo, "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": LISTA}
            for n, alvo in enumerate(("amiodarona", "fluconazol"), 1)
        ]
        self.gerador.provedor = ProvedorFalso([
            resposta("Amiodarona, como fluconazol, pode aumentar a ação da varfarina."),
            resposta("Amiodarona pode aumentar a ação da varfarina."),
            resposta(GERAL),
        ])
        processar(self.repo, "marevan", self.gerador)
        self.assertIn("(fluconazol)", self.gerador.provedor.entradas[1][0][-1]["content"])
        self.assertEqual([texto for _, texto, _ in self.repo.cards_gravados], [
            "Amiodarona pode aumentar a ação da varfarina.", "Fluconazol pode aumentar a ação da varfarina.",
        ])

    def test_nova_tentativa_que_ainda_cita_outro_medicamento_e_aceita(self):
        dados = [{"alvo": "amiodarona", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": LISTA}]
        texto = "Amiodarona, como fluconazol, pode aumentar a ação da varfarina."
        self.gerador.provedor = ProvedorFalso([resposta(texto)] * 2)
        self.assertEqual(self.gerador.gerar("marevan", dados, outros=("fluconazol",)), texto)

    def test_erro_individual_nao_perde_outros_cards(self):
        self.repo.atual["interacoes"].append({**self.repo.atual["interacoes"][0], "id": 11})
        self.gerador.provedor = ProvedorFalso([RuntimeError("falha de inferência"), resposta(EVIDENCIA)])
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "FALHA")
        self.assertEqual(resultado["quantidade_interacoes"], 1)
        self.assertIn("falha de inferência", resultado["erro"])
        self.assertEqual(self.repo.atual["interacoes"][1]["resumo_card"], EVIDENCIA)

    def test_limite_em_muitas_interacoes_nao_gera_geral_parcial(self):
        self.repo.atual["interacoes"] = [{**self.repo.atual["interacoes"][0], "id": n} for n in range(40)]
        self.gerador.provedor = ProvedorFalso([resposta(EVIDENCIA)] * 3)
        resultado = processar(self.repo, "marevan", self.gerador, limite_interacoes=3)
        self.assertEqual(resultado["status"], "PARCIAL")
        self.assertEqual(resultado["chamadas_llm"], 3)
        self.assertIsNone(self.repo.atual.get("resumo_geral"))

    def test_timeout_registrado_e_progresso(self):
        self.gerador.provedor = ProvedorFalso([TimeoutError("tempo excedido")])
        mensagens = []
        resultado = processar(self.repo, "marevan", self.gerador, informar=mensagens.append)
        self.assertEqual(resultado["status"], "FALHA")
        self.assertTrue(any("ERRO" in m for m in mensagens))

    def test_contexto_excedido_nao_trunca_nem_chama_modelo(self):
        self.gerador.config = replace(self.config, tamanho_contexto=500)
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "FALHA")
        self.assertIn("nenhum texto foi cortado", resultado["erro"])
        self.assertEqual(self.provedor.entradas, [])

    def test_fonte_alterada_nao_publica(self):
        original = self.repo.fonte
        chamadas = []
        def fonte(nome):
            chamadas.append(nome)
            valor = original(nome)
            if len(chamadas) > 1:
                valor["processamento_id"] = 2
            return valor
        self.repo.fonte = fonte
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "FALHA")
        self.assertEqual(self.repo.historico[0][-1], {})

    def test_lock_ocupado_nao_grava(self):
        self.repo.ocupado = True
        self.assertEqual(processar(self.repo, "marevan", self.gerador)["status"], "EM_ANDAMENTO")
        self.assertEqual(self.repo.historico, [])

    def test_validacoes_de_formato(self):
        for valor in ("", "x", '{"texto": "erro"}', "```json " + EVIDENCIA,
                      "Você é um especialista em farmacologia. " + EVIDENCIA,
                      ("Esta é uma frase repetida muitas vezes. " * 4)):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                validar_resposta(resposta(valor), EVIDENCIA)
        with self.assertRaises(ValueError):
            validar_resposta(SimpleNamespace(conteudo="{quebrado", truncada=False), EVIDENCIA)
        with self.assertRaises(ValueError):
            validar_resposta(resposta(EVIDENCIA, truncada=True), EVIDENCIA)
        with self.assertRaisesRegex(ValueError, "enumera"):
            validar_resposta(resposta("1. " + EVIDENCIA + "\n2. " + EVIDENCIA), EVIDENCIA, paragrafo=True)
        with self.assertRaisesRegex(ValueError, "enumera"):
            validar_resposta(resposta("• " + EVIDENCIA + "\n• " + EVIDENCIA), EVIDENCIA, paragrafo=True)
        sem_detalhe = "O trecho fornecido não permite detalhar o efeito desta interação."
        with self.assertRaises(ValueError):
            validar_resposta(resposta(sem_detalhe), EVIDENCIA, paragrafo=True)
        with self.assertRaisesRegex(ValueError, "título"):
            validar_resposta(resposta("Interações entre medicamentos: " + EVIDENCIA), EVIDENCIA, paragrafo=True)

    def test_quebra_de_linha_literal_no_json_e_marcador_sao_limpos(self):
        bruto = SimpleNamespace(conteudo='{"texto": "• O uso de ácido acetilsalicílico com varfarina\n   pode aumentar o risco de sangramento."}',
                                truncada=False)
        self.assertEqual(validar_resposta(bruto, EVIDENCIA), EVIDENCIA)

    def test_restaura_grafia_dos_nomes_da_evidencia(self):
        fonte = "A atividade da varfarina pode ser potencializada por ácido mefenâmico, piroxicam, sulindaco e outros."
        bruto = "O ácido mefenámico pode potencializar a atividade da varfarina."
        self.assertEqual(validar_resposta(resposta(bruto), fonte),
                         "O ácido mefenâmico pode potencializar a atividade da varfarina.")

    def test_aceita_card_explicativo_com_palavras_proprias(self):
        # Sem checagem de fidelidade lexical: o card explica com as palavras do LLM.
        texto = ("Amiodarona pode aumentar a ação da varfarina no organismo, "
                 "deixando o efeito anticoagulante mais intenso.")
        self.assertEqual(validar_resposta(resposta(texto), LISTA), texto)
        geral = "Vários remédios citados na bula podem intensificar a ação da varfarina, como amiodarona e fluconazol."
        self.assertEqual(validar_resposta(resposta(geral), LISTA, paragrafo=True), geral)

    def test_evidencia_insuficiente_tem_resposta_controlada(self):
        texto = "O trecho fornecido não permite detalhar o efeito desta interação."
        self.assertEqual(validar_resposta(resposta(texto), "Interação com ácido acetilsalicílico."), texto)

    def test_rejeita_certeza_absoluta_e_gravidade_inventada(self):
        for texto in (EVIDENCIA + " Sempre acontece, com certeza.", EVIDENCIA + " A interação é fatal."):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                validar_resposta(resposta(texto), EVIDENCIA)

    def test_trecho_curto_com_um_so_medicamento_e_a_propria_frase_sem_llm(self):
        self.repo.atual["interacoes"][0]["evidencia"] = "• " + EVIDENCIA.replace(" com ", "\n   com ")
        self.gerador.provedor = ProvedorFalso([resposta(GERAL)])
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(resultado["chamadas_llm"], 1)  # só o parágrafo do geral
        self.assertEqual(self.repo.cards_gravados, [(10, EVIDENCIA, None)])
        self.assertEqual(self.repo.modelos_card, ["trecho_literal"])

    def test_trecho_curto_com_varios_medicamentos_vai_ao_llm_e_cita_so_o_do_card(self):
        trecho = "A co-administração de paroxetina, fluoxetina e quinidina pode aumentar os níveis de nebivolol."
        self.assertTrue(extrativo(trecho))
        self.repo.atual["interacoes"] = [
            {"id": n, "alvo": alvo, "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": trecho}
            for n, alvo in enumerate(("paroxetina", "fluoxetina", "quinidina"), 1)
        ]
        self.gerador.provedor = ProvedorFalso([
            resposta("Paroxetina pode aumentar a quantidade de nebivolol no sangue."),
            resposta("Paroxetina, fluoxetina e quinidina podem aumentar a quantidade de nebivolol no sangue."),
        ])
        processar(self.repo, "nebilet", self.gerador)
        self.assertEqual([texto for _, texto, _ in self.repo.cards_gravados], [
            "Paroxetina pode aumentar a quantidade de nebivolol no sangue.",
            "Fluoxetina pode aumentar a quantidade de nebivolol no sangue.",
            "Quinidina pode aumentar a quantidade de nebivolol no sangue.",
        ])

    def test_mesma_oracao_com_aumento_e_reducao_nao_reaproveita(self):
        trecho = ("Consequentemente, as concentrações podem aumentar (por exemplo, ciclosporina) "
                  "ou diminuir (por exemplo, lamotrigina).")
        self.repo.atual["interacoes"] = [
            {"id": n, "alvo": alvo, "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": trecho}
            for n, alvo in enumerate(("ciclosporina", "lamotrigina"), 1)
        ]
        self.gerador.provedor = ProvedorFalso([
            resposta("Ciclosporina pode ter sua concentração no sangue aumentada."),
            resposta("Lamotrigina pode ter sua concentração no sangue diminuída."),
            resposta(GERAL.replace("Marevan", "Triquilar")), resposta(GERAL.replace("Marevan", "Triquilar")),
        ])
        resultado = processar(self.repo, "triquilar", self.gerador)
        self.assertEqual(resultado["chamadas_llm"], 4)
        self.assertEqual(self.repo.cards_gravados[1], (2, "Lamotrigina pode ter sua concentração no sangue diminuída.", None))

    def test_molde_que_ja_cita_o_outro_medicamento_nao_e_reaproveitado(self):
        trecho = "A administração concomitante de carbidopa com levodopa previne este efeito."
        self.repo.atual["interacoes"] = [
            {"id": n, "alvo": alvo, "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": trecho}
            for n, alvo in enumerate(("carbidopa", "levodopa"), 1)
        ]
        # Aqui citar o outro é inevitável: cada card usa a nova tentativa, que é aceita.
        carbidopa = resposta("Carbidopa, usada junto com levodopa, impede esse efeito.")
        levodopa = resposta("Levodopa, usada junto com carbidopa, tem esse efeito evitado.")
        self.gerador.provedor = ProvedorFalso([carbidopa, carbidopa, levodopa, levodopa, resposta(GERAL)])
        processar(self.repo, "vitaminas", self.gerador)
        self.assertEqual(self.repo.cards_gravados[1][1], "Levodopa, usada junto com carbidopa, tem esse efeito evitado.")
        self.assertIn("ALVO: levodopa", self.gerador.provedor.entradas[2][0][-1]["content"])
        self.assertNotIn("Card já escrito", self.gerador.provedor.entradas[2][0][-1]["content"])

    def test_lista_curta_e_condensada_para_o_alvo(self):
        lista = ("• A atividade anticoagulante da varfarina pode ser inibida por alguns fármacos, tais como: "
                 "aminoglutetimida, barbiturato, carbamazepina, griseofulvina, primidona, rifampicina e contraceptivos orais.")
        self.assertLess(len(lista), 400)
        self.assertFalse(extrativo(lista))
        self.assertTrue(extrativo(EVIDENCIA))
        condensado = "A atividade anticoagulante da varfarina pode ser inibida por carbamazepina."
        self.gerador.provedor = ProvedorFalso([resposta(condensado)])
        dados = [{"alvo": "carbamazepina", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": lista}]
        self.assertEqual(self.gerador.gerar("marevan", dados), condensado)

    def test_muitas_interacoes_processadas_uma_a_uma_antes_do_geral(self):
        self.repo.atual["interacoes"] = [{**self.repo.atual["interacoes"][0], "id": n} for n in range(30)]
        self.gerador.provedor = ProvedorFalso([resposta(EVIDENCIA)] * 30 + [resposta(GERAL)])
        mensagens = []
        resultado = processar(self.repo, "marevan", self.gerador, informar=mensagens.append)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(resultado["chamadas_llm"], 31)
        self.assertEqual(len(self.repo.cards_gravados), 30)
        self.assertTrue(any(m.startswith("[30/30]") for m in mensagens))
        # Cada card recebe só a própria interação; o geral vem depois, com os cards
        # idênticos agrupados numa única linha.
        self.assertTrue(all(e[0][-1]["content"].count("Evidência literal:") == 1
                            for e in self.gerador.provedor.entradas[:30]))
        self.assertEqual(self.gerador.provedor.entradas[30][0][-1]["content"].count("risco de sangramento"), 1)

    def test_agrupa_cards_que_so_diferem_pelo_alvo(self):
        cards = [{"alvo": alvo, "resumo": f"A atividade da varfarina pode ser potencializada por {alvo}."}
                 for alvo in ("amiodarona", "fluconazol", "paracetamol")]
        cards.append({"alvo": "rifampicina", "resumo": "O trecho fornecido não permite detalhar o efeito desta interação."})
        self.assertEqual(agrupar_cards(cards), [
            "A atividade da varfarina pode ser potencializada por amiodarona, fluconazol e paracetamol.",
            "A bula cita interação com rifampicina, sem detalhar o efeito.",
        ])

    def test_geral_tem_um_paragrafo_por_grupo_de_efeito(self):
        aumento = "A atividade da varfarina pode ser potencializada por amiodarona."
        reducao = "A atividade anticoagulante da varfarina pode ser inibida por carbamazepina."
        ambos = "Tanto a potencialização quanto a inibição do efeito anticoagulante têm sido relatadas com fenitoína."
        self.assertEqual(grupos_por_efeito([ambos, reducao, aumento]), [[aumento], [reducao], [ambos]])
        cards = [{"alvo": "fenitoína", "resumo": ambos}, {"alvo": "carbamazepina", "resumo": reducao},
                 {"alvo": "amiodarona", "resumo": aumento}]
        paragrafos = ["Na bula de Marevan, " + aumento + " A bula descreve esse efeito.",
                      "Na bula de Marevan, " + reducao + " A bula descreve esse efeito.",
                      "Na bula de Marevan, " + ambos + " A bula descreve os dois efeitos."]
        self.gerador.provedor = ProvedorFalso([resposta(p) for p in paragrafos])
        self.assertEqual(self.gerador.gerar("marevan", cards, geral=True), "\n\n".join(paragrafos))
        # Cada parágrafo só vê as linhas do próprio grupo.
        self.assertNotIn("carbamazepina", self.gerador.provedor.entradas[0][0][-1]["content"])

    def test_benchmark_nao_grava_e_mede_geral(self):
        from pipelines.resumos_interacoes.benchmark import executar
        resultado = executar(self.repo, "marevan", self.gerador, limite=5)
        self.assertEqual(resultado["falhas"], 0)
        self.assertEqual(resultado["geral"]["texto"], GERAL)
        self.assertFalse(resultado["gravacao"])
        self.assertEqual(self.repo.cards_gravados, [])
        self.assertEqual(self.repo.historico, [])

    def test_molde_troca_so_o_medicamento_e_ajusta_artigo(self):
        casos = [
            ("A amiodarona pode aumentar a ação da varfarina.", "Paracetamol pode aumentar a ação da varfarina."),
            ("O efeito da varfarina pode ser intensificado pela amiodarona.",
             "O efeito da varfarina pode ser intensificado por paracetamol."),
            ("Com a amiodarona, a varfarina pode agir mais.", "Com paracetamol, a varfarina pode agir mais."),
            ("A varfarina associada à amiodarona pode agir mais.", "A varfarina associada a paracetamol pode agir mais."),
            ("Amiodarona pode aumentar a ação da varfarina.", "Paracetamol pode aumentar a ação da varfarina."),
        ]
        for texto, esperado in casos:
            with self.subTest(texto=texto):
                self.assertEqual(preencher(molde(texto, "amiodarona"), "paracetamol"), esperado)
        self.assertIsNone(molde("Amiodarona e a amiodarona.", "amiodarona"))
        self.assertIsNone(molde("A varfarina pode agir mais.", "amiodarona"))

    def test_mesmo_trecho_e_efeito_reaproveita_o_texto_do_primeiro(self):
        self.repo.atual["interacoes"] = [
            {"id": n, "alvo": alvo, "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": LISTA}
            for n, alvo in enumerate(("amiodarona", "fluconazol", "paracetamol"), 1)
        ]
        self.gerador.provedor = ProvedorFalso([
            resposta("A amiodarona pode aumentar a ação da varfarina, deixando o efeito mais intenso."),
            resposta("Na bula de Marevan, amiodarona, fluconazol e paracetamol podem aumentar a ação da varfarina."),
        ])
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(resultado["chamadas_llm"], 2)  # um card escrito pelo LLM + um parágrafo
        self.assertIn("ALVO: amiodarona",
                      self.gerador.provedor.entradas[0][0][-1]["content"])
        self.assertEqual([texto for _, texto, _ in self.repo.cards_gravados], [
            "Amiodarona pode aumentar a ação da varfarina, deixando o efeito mais intenso.",
            "Fluconazol pode aumentar a ação da varfarina, deixando o efeito mais intenso.",
            "Paracetamol pode aumentar a ação da varfarina, deixando o efeito mais intenso.",
        ])

    def test_mesmo_trecho_com_efeitos_diferentes_nao_reaproveita(self):
        trecho = ("A atividade da varfarina pode ser potencializada por amiodarona, cimetidina, fluconazol e "
                  "metronidazol. A atividade da varfarina pode ser reduzida por carbamazepina, rifampicina, "
                  "barbitúricos e primidona.")
        self.assertFalse(extrativo(trecho))
        self.repo.atual["interacoes"] = [
            {"id": n, "alvo": alvo, "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": trecho}
            for n, alvo in enumerate(("amiodarona", "fluconazol", "carbamazepina", "rifampicina"), 1)
        ]
        self.gerador.provedor = ProvedorFalso([
            resposta("Amiodarona pode aumentar a ação da varfarina."),
            resposta("Carbamazepina pode diminuir a ação da varfarina."),
        ])
        processar(self.repo, "marevan", self.gerador)
        self.assertEqual([texto for _, texto, _ in self.repo.cards_gravados], [
            "Amiodarona pode aumentar a ação da varfarina.", "Fluconazol pode aumentar a ação da varfarina.",
            "Carbamazepina pode diminuir a ação da varfarina.", "Rifampicina pode diminuir a ação da varfarina.",
        ])

    def test_rejeita_eco_da_instrucao_de_exemplo(self):
        # Resposta real do Qwen2.5-3B no benchmark.
        texto = ("O efeito descrito para nortriptilina é o mesmo que para amiodarona: "
                 "'A nortriptilina pode potencializar a atividade da varfarina.'")
        with self.assertRaisesRegex(ValueError, "instruções do prompt"):
            validar_resposta(resposta(texto), LISTA)
        with self.assertRaisesRegex(ValueError, "aspas"):
            validar_resposta(resposta("A atividade da varfarina pode causar 'cura garantida'."), LISTA)

    def test_retomada_reaproveita_card_ja_gravado_sem_chamar_o_modelo(self):
        self.repo.atual["interacoes"] = [
            {"id": 1, "alvo": "amiodarona", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": LISTA,
             "resumo_card": "A amiodarona pode aumentar a ação da varfarina."},
            {"id": 2, "alvo": "fluconazol", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": LISTA},
        ]
        self.gerador.provedor = ProvedorFalso([])
        processar(self.repo, "marevan", self.gerador, limite_interacoes=1)
        self.assertEqual(self.repo.cards_gravados[0], (2, "Fluconazol pode aumentar a ação da varfarina.", None))

    def test_uma_unica_retentativa_corrige_resposta_invalida(self):
        self.gerador.provedor = ProvedorFalso([resposta(""), resposta(EVIDENCIA), resposta(GERAL)])
        resultado = processar(self.repo, "marevan", self.gerador)
        self.assertEqual(resultado["status"], "CONCLUIDO")
        self.assertEqual(resultado["chamadas_llm"], 3)

    def test_cli_piloto_e_todos_explicito(self):
        self.assertFalse(argumentos([]).todos)
        self.assertIsNone(argumentos([]).limite)
        self.assertEqual(argumentos(["--limite", "3"]).limite, 3)
        self.assertTrue(argumentos(["--todos"]).todos)
        with self.assertRaises(SystemExit):
            argumentos(["--todos", "--limite", "2"])
        self.assertEqual(argumentos(["--todos", "--refazer-antes", "2026-10-13 16:30"]).refazer_antes,
                         datetime(2026, 10, 13, 16, 30))
        self.assertIsNone(argumentos(["--todos"]).refazer_antes)
        with self.assertRaises(SystemExit):
            argumentos(["--todos", "--refazer-antes", "ontem"])

    def test_bloqueia_ausencia_inventada_nas_duas_tentativas(self):
        from pipelines.identificacao_interacoes.tests.test_contexto_evidencia import BLOCO
        errado = ("A interação de linezolida com Fluidsoro é desconhecida e não tem sido relatado em estudos. "
                  "Portanto, não há informações sobre como linezolida pode influenciar o efeito de Fluidsoro.")
        self.gerador.provedor = ProvedorFalso([resposta(errado), resposta(errado)])
        with self.assertRaisesRegex(ValueError, "desconhecida"):
            self.gerador.gerar("fluidsoro", [{"alvo": "linezolida", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": BLOCO}])
        for texto in ("Linezolida não apresenta interação e não há informações disponíveis.",
                      "Não há interação entre linezolida e nafazolina nasal; a associação é segura.",
                      "Linezolida não foi estudada e não existem dados sobre a associação.",
                      "Linezolida com nafazolina nasal aumenta os efeitos adversos cardiovasculares.",
                      "Linezolida pode aumentar o risco de efeitos adversos cardiovasculares.",
                      "O trecho fornecido não permite detalhar o efeito desta interação."):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                validar_resposta(resposta(texto), BLOCO)

    def test_ausencia_que_esta_na_fonte_nao_e_inventada(self):
        texto = "Não se sabe como escetamina e nafazolina nasal podem afetar uns aos outros."
        self.assertEqual(validar_resposta(resposta(texto), texto), texto)

    def test_reparo_seletivo_refaz_geral_preserva_outros_e_e_retomavel(self):
        from pipelines.identificacao_interacoes.tests.test_contexto_evidencia import BLOCO, LISTA
        correto = ("Linezolida com nafazolina nasal pode aumentar o risco de efeitos cardiovasculares, "
                   "como aumento da frequência cardíaca, pressão arterial e ritmo cardíaco irregular.")
        self.repo.atual["resumo_geral"] = GERAL
        self.repo.atual["interacoes"] = [
            {"id": 1, "alvo": "linezolida", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": BLOCO,
             "evidencia_original": LISTA, "resumo_card": "A interação de linezolida é desconhecida e não foi estudada."},
            {"id": 2, "alvo": "varfarina", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": EVIDENCIA,
             "resumo_card": EVIDENCIA}]
        self.gerador.provedor = ProvedorFalso([resposta(correto), resposta(correto)])
        # O repositório real publica o recorte ampliado junto com o card.
        gravar = self.repo.gravar_card
        def gravar_card(fonte, item, *args, **kwargs):
            gravar(fonte, item, *args, **kwargs)
            self.repo.atual["interacoes"][0].pop("evidencia_original", None)
        self.repo.gravar_card = gravar_card
        resultado = processar(self.repo, "fluidsoro", self.gerador, corrigir_inconsistentes=True)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual([r[0] for r in self.repo.cards_gravados], [1])
        self.assertEqual(self.repo.atual["interacoes"][1]["resumo_card"], EVIDENCIA)
        self.assertIn("Efeitos da interação:", self.gerador.provedor.entradas[0][0][-1]["content"])
        self.assertFalse(necessita_correcao(self.repo.atual["interacoes"][0]))
        self.assertEqual(processar(self.repo, "fluidsoro", self.gerador, corrigir_inconsistentes=True)["status"], "IGNORADO_JA_PROCESSADO")

    def test_card_antigo_errado_nao_serve_de_molde(self):
        from pipelines.resumos_interacoes.servico import CardsDoMesmoTrecho
        itens = [{"id": n, "alvo": alvo, "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": LISTA,
                  "resumo_card": f"A interação de {alvo} é desconhecida."}
                 for n, alvo in enumerate(("amiodarona", "fluconazol"))]
        self.assertEqual(CardsDoMesmoTrecho(itens).moldes, {})

    def test_reparo_por_padrao_nao_depende_de_campo_vazio(self):
        item = {"evidencia": EVIDENCIA, "resumo_card": "Não há informações sobre essa interação desconhecida."}
        self.assertTrue(necessita_correcao(item))
        self.assertFalse(necessita_correcao({**item, "resumo_card": EVIDENCIA}))
        self.assertFalse(necessita_correcao({**item, "resumo_card": None}))

    def test_efeito_compartilhado_validado_recebe_rotulo_do_alvo_da_lista(self):
        from pipelines.identificacao_interacoes.tests.test_contexto_evidencia import BLOCO, EFEITO
        efeito = EFEITO.split(": ", 1)[1]
        self.gerador.provedor = ProvedorFalso([resposta(efeito)])
        dados = [{"alvo": "linezolida", "tipo_alvo": "PRINCIPIO_ATIVO", "evidencia": BLOCO}]
        texto = self.gerador.gerar("fluidsoro", dados)
        self.assertTrue(texto.startswith("Linezolida: "))
        self.assertIn("nafazolina nasal pode aumentar", texto)
        self.assertEqual(self.gerador.chamadas, 1)
        # Não cola um alvo que não aparece entre os integrantes da lista.
        self.gerador.provedor = ProvedorFalso([resposta(efeito), resposta(efeito)])
        with self.assertRaisesRegex(ValueError, "não menciona"):
            self.gerador.gerar("fluidsoro", [{**dados[0], "alvo": "inexistente"}])

    def test_geral_rejeita_frase_isolada_sobre_fentanil(self):
        errado = ("O citrato de fentanil, um medicamento, pode apresentar um efeito analgésico "
                  "reduzido quando interage com outros fármacos.")
        correto = "A bula de Fluidsoro descreve redução do efeito analgésico na interação com citrato de fentanil."
        with self.assertRaises(ValueError):
            validar_contexto_geral(errado, "fluidsoro")
        with self.assertRaises(ValueError):
            validar_contexto_geral(correto + "\n\n" + errado, "fluidsoro")
        validar_contexto_geral(correto, "fluidsoro")
        self.gerador.provedor = ProvedorFalso([resposta(errado), resposta(correto)])
        texto = self.gerador.gerar("fluidsoro", [{"alvo": "fentanil", "resumo": "Citrato de fentanil: efeito analgésico reduzido."}], geral=True)
        self.assertEqual(texto, correto)
        self.assertIn("MEDICAMENTO PESQUISADO: Fluidsoro", self.gerador.provedor.entradas[0][0][-1]["content"])
        self.assertEqual(self.gerador.chamadas, 2)

    def test_fallback_do_geral_tambem_preserva_medicamento_pesquisado(self):
        errado = "O citrato de fentanil pode apresentar um efeito analgésico reduzido com outros fármacos."
        self.gerador.provedor = ProvedorFalso([resposta(errado), resposta(errado)])
        texto = self.gerador.gerar("fluidsoro", [{"alvo": "fentanil", "resumo": "Citrato de fentanil: efeito analgésico reduzido."}], geral=True)
        self.assertTrue(texto.startswith("A bula de Fluidsoro descreve estas interações:"))
        self.assertIn("Citrato de fentanil: efeito analgésico reduzido.", texto)
        self.assertNotIn("outros fármacos", texto)
        validar_contexto_geral(texto, "fluidsoro")

    def test_corrige_somente_geral_sem_refazer_cards_preenchidos(self):
        self.repo.atual["interacoes"][0]["resumo_card"] = EVIDENCIA
        self.repo.atual["resumo_geral"] = "A varfarina pode interagir com vários outros fármacos."
        self.assertTrue(necessita_correcao_geral(self.repo.atual))
        self.gerador.provedor = ProvedorFalso([resposta(GERAL)])
        resultado = processar(self.repo, "marevan", self.gerador, corrigir_inconsistentes=True)
        self.assertEqual(resultado["status"], "CONCLUIDO", resultado)
        self.assertEqual(resultado["quantidade_interacoes"], 0)
        self.assertEqual(resultado["chamadas_llm"], 1)
        self.assertEqual(self.repo.cards_gravados, [])
        self.assertEqual(self.repo.atual["resumo_geral"], GERAL)
        self.assertFalse(necessita_correcao_geral(self.repo.atual))
        self.assertEqual(processar(self.repo, "marevan", self.gerador, corrigir_inconsistentes=True)["status"], "IGNORADO_JA_PROCESSADO")

    def test_nome_no_primeiro_paragrafo_nao_encobre_segundo_fora_de_contexto(self):
        fonte = {"nome_normalizado": "fluidsoro", "resumo_geral": (
            "A bula de Fluidsoro cita interações cardiovasculares.\n\nO fentanil interage com outros medicamentos.")}
        self.assertTrue(necessita_correcao_geral(fonte))

    def test_cli_reparo_nao_combina_com_reprocessamento_total(self):
        args = argumentos(["--todos", "--corrigir-inconsistentes", "--listar"])
        self.assertTrue(args.corrigir_inconsistentes)
        self.assertTrue(args.listar)
        with self.assertRaises(SystemExit):
            argumentos(["--corrigir-inconsistentes", "--reprocessar"])
        with self.assertRaises(SystemExit):
            argumentos(["--alvo-benchmark", "linezolida"])
        with self.assertRaises(SystemExit):
            argumentos(["--todos", "--prompt-simplificado"])
        self.assertTrue(argumentos(["--benchmark-modelo", "--nome-normalizado", "marevan",
                                   "--prompt-simplificado"]).prompt_simplificado)

    def test_prompt_original_permanece_padrao_fora_do_piloto(self):
        gerador = GeradorResumos(ProvedorFalso([resposta(EVIDENCIA)]), self.config)
        gerador.gerar("marevan", [{"alvo": "ácido acetilsalicílico", "tipo_alvo": "PRINCIPIO_ATIVO",
                                   "evidencia": TRECHO}])
        self.assertFalse(gerador.prompt_simplificado)
        mensagens = gerador.provedor.entradas[0][0]
        self.assertEqual(len(mensagens), 6)
        self.assertIn("ação de Marevan", mensagens[2]["content"])


if __name__ == "__main__":
    unittest.main()
