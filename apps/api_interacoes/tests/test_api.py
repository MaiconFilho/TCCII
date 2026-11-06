import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api_interacoes.app import criar_app
from apps.api_interacoes.configuracao import ConfiguracaoApi
from apps.api_interacoes.erros import BancoIndisponivelError
from apps.api_interacoes.repositorio import RepositorioConsulta


RAIZ = Path(__file__).resolve().parents[3]
DATA = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
PUBLICACAO = datetime(2026, 6, 30, 12, tzinfo=timezone.utc)


def situacao(nome, **extra):
    base = {
        "nome_normalizado": nome, "nome_exibicao": nome.upper(), "status_bula": "CONCLUIDO",
        "numero_registro": "1", "expediente": "2", "sha256_pdf": "h", "data_publicacao_anvisa": PUBLICACAO,
        "tem_extracao": True, "status_extracao": "CONCLUIDO", "tem_texto": True, "extracao_coerente": True,
        "processamento_id": None, "status_processamento": None, "concluido_em": None,
        "versao_processamento": None, "modelo": None, "registro_processado": None,
        "expediente_processado": None, "sha256_processado": None, "publicacao_processada": None,
        "ultimo_status_fonte_atual": None, "tem_historico": False,
    }
    base.update(extra)
    return base


def processado(nome, status, pid):
    return situacao(
        nome, processamento_id=pid, status_processamento=status, concluido_em=DATA,
        versao_processamento="v1", modelo="m", registro_processado="1", expediente_processado="2",
        sha256_processado="h", publicacao_processada=PUBLICACAO,
    )


def linha_relacao(iid, origem, alvo, tipo, grupo=0, evidencia=None, criterio=None, sentido=None, pid=1):
    linha = {
        "id": iid, "grupo": grupo, "medicamento_origem": origem, "alvo": alvo, "tipo_alvo": tipo,
        "resumo": f"Resumo sobre {alvo}.", "evidencia": evidencia or f"Evidência sobre {alvo}.",
        "origem_exibicao": origem.upper(), "processamento_id": pid,
        "status_processamento": "CONCLUIDO_COM_RELACOES", "concluido_em": DATA,
        "versao_processamento": "v1", "modelo": "m", "numero_registro": "1", "expediente": "2",
        "sha256_pdf": "h", "data_publicacao_anvisa": PUBLICACAO,
    }
    if sentido:
        linha.update(criterio_vinculo=criterio, sentido_par=sentido)
    return linha


class RepositorioFalso:
    def __init__(self) -> None:
        self.situacoes_ = {
            "varfarina": processado("varfarina", "CONCLUIDO_COM_RELACOES", 1),
            "losec": processado("losec", "CONCLUIDO_SEM_RELACOES", 2),
            "pendente": situacao("pendente"),
            "antigo": situacao("antigo", tem_historico=True),
            "quebrado": situacao("quebrado", ultimo_status_fonte_atual="FALHA", tem_historico=True),
            "sem secao": situacao("sem secao", status_extracao="SEM_SECAO_INTERACOES", tem_texto=False),
            "incoerente": situacao("incoerente", extracao_coerente=False),
            "revisado": situacao("revisado", ultimo_status_fonte_atual="REVISAO_NECESSARIA", tem_historico=True),
            "retirado": situacao("retirado", tem_historico=True, tem_retirada_fonte_atual=True),
        }
        self.relacoes = [
            linha_relacao(10, "varfarina", "ácido acetilsalicílico", "PRINCIPIO_ATIVO"),
            linha_relacao(11, "varfarina", "saladas verdes", "GRUPO_ALIMENTAR"),
            linha_relacao(12, "varfarina", "anticoagulantes", "CLASSE_FARMACOLOGICA"),
            linha_relacao(13, "aas", "varfarina", "PRINCIPIO_ATIVO", grupo=1),
        ]
        self.par = []
        self.ocupados: set[str] = set()
        self.chamadas_sugerir = []
        self.falhar = False

    def verificar(self):
        return (not self.falhar, not self.falhar)

    def sugerir(self, termo, limite):
        self.chamadas_sugerir.append((termo, limite))
        return [{"nome_normalizado": "varfarina", "nome_exibicao": "Varfarina"}][:limite]

    def situacoes(self, nomes):
        if self.falhar:
            raise BancoIndisponivelError("O serviço de consulta está indisponível no momento.")
        return {n: self.situacoes_[n] for n in nomes if n in self.situacoes_}

    def em_processamento(self, nomes):
        return self.ocupados & set(nomes)

    def relacoes_do_medicamento(self, nome, limite, deslocamento):
        linhas = [r for r in self.relacoes if r["medicamento_origem"] == nome or r["grupo"] == 1 and nome == "varfarina"]
        return linhas[deslocamento:deslocamento + limite], len(linhas)

    def relacoes_para_contagem(self, nome):
        return [r for r in self.relacoes if r["medicamento_origem"] == nome or r["grupo"] == 1 and nome == "varfarina"]

    def relacoes_por_ids(self, nome, ids):
        return [r for r in self.relacoes if r["id"] in ids and r["medicamento_origem"] == nome]

    def resumo_geral(self, processamento_id):
        return None

    def relacoes_do_par(self, a, b):
        return self.par

    def relacionados(self, interacoes, limite):
        if 10 in interacoes:
            return {10: ([{"medicamento_relacionado": "aas", "nome_exibicao": "AAS", "criterio_vinculo": "PRINCIPIO_ATIVO_EXATO"}], 3)}
        return {}

    def cobertura(self):
        return {"elegiveis": 10, "concluidas": 2, "em_revisao": 0}


class TestApi(unittest.TestCase):
    def setUp(self) -> None:
        self.repositorio = RepositorioFalso()
        self.cliente = TestClient(criar_app(ConfiguracaoApi(dsn="x"), self.repositorio))

    def test_saude(self) -> None:
        self.assertEqual(self.cliente.get("/api/saude").json()["status"], "ok")
        self.repositorio.falhar = True
        corpo = self.cliente.get("/api/saude").json()
        self.assertEqual((corpo["status"], corpo["banco"]), ("degradado", "indisponivel"))

    def test_autocomplete_normaliza_e_limita(self) -> None:
        resposta = self.cliente.get("/api/medicamentos/sugestoes", params={"q": "  VÁRFarina "})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["termo"], "varfarina")
        self.assertEqual(self.repositorio.chamadas_sugerir[-1], ("varfarina", 6))
        self.cliente.get("/api/medicamentos/sugestoes", params={"q": "v", "limite": 50})
        self.assertEqual(self.repositorio.chamadas_sugerir[-1], ("v", 20))

    def test_autocomplete_valida_entrada(self) -> None:
        for params in ({"q": ""}, {"q": "   "}, {"q": "x" * 101}, {"q": "a", "limite": 0}):
            with self.subTest(params=params):
                resposta = self.cliente.get("/api/medicamentos/sugestoes", params=params)
                self.assertEqual(resposta.status_code, 422)
                self.assertEqual(resposta.json()["erro"], "parametro_invalido")

    def test_consulta_individual_inclui_alvos_sem_vinculo_apenas_da_propria_bula(self) -> None:
        corpo = self.cliente.get("/api/interacoes", params={"medicamento": "varfarina"}).json()
        self.assertTrue(corpo["interacao_identificada"])
        self.assertTrue(corpo["mensagem"].startswith("Interação identificada nas fontes analisadas."))
        tipos = {r["alvo"]: (r["tipo_alvo"], r["sentido"]) for r in corpo["relacoes"]}
        self.assertEqual(tipos["saladas verdes"], ("GRUPO_ALIMENTAR", "DESCRITA_NA_BULA_PESQUISADA"))
        self.assertEqual(tipos["anticoagulantes"][0], "CLASSE_FARMACOLOGICA")
        self.assertNotIn("varfarina", tipos)
        primeira = corpo["relacoes"][0]
        self.assertEqual(corpo["total_interacoes"], 3)
        self.assertNotIn("totais_por_sentido", corpo)
        self.assertNotIn("em_outras_bulas", str(corpo))
        self.assertEqual(corpo["resumo_geral"]["status"], "PENDENTE")
        self.assertEqual(primeira["gravidade"], "desconhecida")
        self.assertIsNone(primeira["fonte"]["url_pdf"])
        self.assertEqual(primeira["fonte"]["data_publicacao_bula"][:10], "2026-06-30")
        self.assertEqual(primeira["processamento"]["data_processamento"][:10], "2026-10-02")
        self.assertEqual(primeira["total_medicamentos_relacionados"], 3)
        self.assertFalse(corpo["cobertura"]["lista_exaustiva"])
        self.assertIn("não é exaustiva", corpo["mensagem"])

    def test_paginacao(self) -> None:
        corpo = self.cliente.get(
            "/api/interacoes", params={"medicamento": "varfarina", "pagina": 2, "tamanho_pagina": 3}
        ).json()
        self.assertEqual(corpo["paginacao"], {"pagina": 2, "tamanho_pagina": 3, "total": 3, "total_paginas": 1})
        self.assertEqual(corpo["relacoes"], [])
        for params in ({"pagina": 0}, {"tamanho_pagina": 101}):
            resposta = self.cliente.get("/api/interacoes", params={"medicamento": "varfarina", **params})
            self.assertEqual(resposta.status_code, 422)

    def test_resumos_prontos_e_evidencia_literal_separados(self) -> None:
        self.repositorio.resumo_geral = lambda pid: {
            "status": "CONCLUIDO", "texto": "Primeiro parágrafo.\n\nSegundo parágrafo.",
            "gerado_em": DATA, "processamento_id": pid,
        }
        self.repositorio.relacoes[0]["resumo_card"] = "Resumo de apresentação já gerado."
        corpo = self.cliente.get("/api/interacoes", params={"medicamento": "varfarina"}).json()
        self.assertEqual(corpo["resumo_geral"]["status"], "CONCLUIDO")
        self.assertEqual(corpo["resumo_geral"]["processamento_id"], 1)
        self.assertEqual(corpo["relacoes"][0]["resumo_card"], "Resumo de apresentação já gerado.")
        self.assertEqual(corpo["relacoes"][0]["evidencia"], self.repositorio.relacoes[0]["evidencia"])

    def test_paginacao_filtra_negacoes_antes_do_limite(self) -> None:
        self.repositorio.relacoes.insert(0, linha_relacao(
            1, "varfarina", "azitromicina", "PRINCIPIO_ATIVO", evidencia="Azitromicina não estudado."))
        corpo = self.cliente.get("/api/interacoes", params={"medicamento": "varfarina", "tamanho_pagina": 1}).json()
        self.assertEqual(corpo["relacoes"][0]["id"], 10)
        self.assertEqual(corpo["paginacao"]["total"], 3)

    def test_reescrita_separada_preserva_campos_originais_e_evidencia(self):
        self.repositorio.resumo_geral = lambda pid: {
            "status": "CONCLUIDO", "texto": "Resumo geral original.", "texto_simples": "Resumo geral simples.",
            "gerado_em": DATA, "processamento_id": pid,
        }
        linha = self.repositorio.relacoes[0]
        linha.update(resumo_card="Card original.", resumo_card_simples="Card simples.")
        corpo = self.cliente.get("/api/interacoes", params={"medicamento": "varfarina"}).json()
        self.assertEqual(corpo["relacoes"][0]["resumo_card"], "Card original.")
        self.assertEqual(corpo["relacoes"][0]["resumo_card_simples"], "Card simples.")
        self.assertEqual(corpo["relacoes"][0]["evidencia"], linha["evidencia"])
        self.assertEqual(corpo["resumo_geral"]["texto"], "Resumo geral original.")
        self.assertEqual(corpo["resumo_geral"]["texto_simples"], "Resumo geral simples.")

    def test_totais_contam_interacoes_agrupadas_sem_repetir_evidencias(self) -> None:
        self.repositorio.relacoes.extend([
            linha_relacao(14, "varfarina", "ÁCIDO ACETILSALICÍLICO", "PRINCIPIO_ATIVO"),
            linha_relacao(15, "aas", "varfarina", "PRINCIPIO_ATIVO", grupo=1),
        ])
        corpo = self.cliente.get("/api/interacoes", params={"medicamento": "varfarina"}).json()
        self.assertEqual(corpo["total_interacoes"], 3)
        self.assertEqual(corpo["paginacao"]["total"], 4)

    def test_concluido_sem_relacoes_versus_pendente(self) -> None:
        concluido = self.cliente.get("/api/interacoes", params={"medicamento": "losec"}).json()
        self.assertIs(concluido["interacao_identificada"], False)
        self.assertTrue(concluido["mensagem"].startswith("Não foi identificada interação nas fontes analisadas."))
        pendente = self.cliente.get("/api/interacoes", params={"medicamento": "pendente"}).json()
        self.assertIsNone(pendente["interacao_identificada"])
        self.assertEqual(pendente["analise_bula"]["estado"], "NAO_PROCESSADO")
        self.assertNotIn("Não foi identificada", pendente["mensagem"])

    def test_estados_explicitos(self) -> None:
        self.repositorio.ocupados.add("pendente")
        esperados = {
            "antigo": "DESATUALIZADO", "quebrado": "FALHA", "sem secao": "SEM_TOPICO_INTERACOES",
            "incoerente": "FONTE_INCOERENTE", "pendente": "EM_PROCESSAMENTO",
            "revisado": "REVISAO_NECESSARIA", "retirado": "RETIRADO",
        }
        for nome, estado in esperados.items():
            corpo = self.cliente.get("/api/interacoes", params={"medicamento": nome}).json()
            self.assertEqual(corpo["analise_bula"]["estado"], estado, nome)
            self.assertIsNone(corpo["interacao_identificada"], nome)

    def test_identificador_validado(self) -> None:
        resposta = self.cliente.get("/api/interacoes", params={"medicamento": "Varfarina"})
        self.assertEqual(resposta.status_code, 422)
        resposta = self.cliente.get("/api/interacoes", params={"medicamento": "nao existe"})
        self.assertEqual((resposta.status_code, resposta.json()["erro"]), (404, "medicamento_nao_encontrado"))

    def test_par_nos_dois_sentidos_agrupando_evidencia(self) -> None:
        evidencia = "O AAS e o ibuprofeno aumentam o risco de sangramento com varfarina."
        self.repositorio.par = [
            linha_relacao(20, "varfarina", "AAS", "MEDICAMENTO", evidencia=evidencia, criterio="NOME_MEDICAMENTO_EXATO", sentido="BULA_A_CITA_B"),
            linha_relacao(21, "varfarina", "ácido acetilsalicílico", "PRINCIPIO_ATIVO", evidencia=evidencia, criterio="PRINCIPIO_ATIVO_EXATO", sentido="BULA_A_CITA_B"),
            linha_relacao(30, "losec", "varfarina", "PRINCIPIO_ATIVO", criterio="PRINCIPIO_ATIVO_EXATO", sentido="BULA_B_CITA_A", pid=2),
            linha_relacao(31, "losec", "anticoagulantes", "CLASSE_FARMACOLOGICA", sentido="BULA_B_CITA_A", pid=2),
        ]
        corpo = self.cliente.get("/api/interacoes/par", params={"medicamento_a": "varfarina", "medicamento_b": "losec"}).json()
        self.assertTrue(corpo["interacao_identificada"])
        self.assertEqual([g["sentido"] for g in corpo["evidencias"]], ["BULA_A_CITA_B", "BULA_B_CITA_A"])
        self.assertNotIn("resumo_geral", corpo)
        self.repositorio.par[0]["resumo_card"] = "Resumo específico do par."
        atualizado = self.cliente.get("/api/interacoes/par", params={"medicamento_a": "varfarina", "medicamento_b": "losec"}).json()
        self.assertEqual(atualizado["evidencias"][0]["itens"][0]["resumo_card"], "Resumo específico do par.")
        self.assertEqual(len(corpo["evidencias"][0]["itens"]), 2)
        self.assertEqual(corpo["evidencias"][1]["fonte"]["medicamento"]["nome_normalizado"], "losec")
        self.assertEqual([a["itens"][0]["alvo"] for a in corpo["alertas_nao_confirmados"]], ["anticoagulantes"])

    def test_par_informa_componentes_e_o_que_fundamentou_o_vinculo(self) -> None:
        # Medicamento composto: a bula da origem cita só um dos componentes.
        self.repositorio.situacoes_["losec"]["principios_ativos"] = "paracetamol; cloridrato de pseudoefedrina"
        citacao = linha_relacao(50, "varfarina", "paracetamol", "PRINCIPIO_ATIVO",
                                criterio="PRINCIPIO_ATIVO_EXATO", sentido="BULA_A_CITA_B")
        citacao["fundamento_vinculo"] = "paracetamol"
        self.repositorio.par = [citacao]
        corpo = self.cliente.get("/api/interacoes/par", params={"medicamento_a": "varfarina", "medicamento_b": "losec"}).json()
        self.assertEqual(corpo["medicamento_b"]["principios_ativos"], ["paracetamol", "cloridrato de pseudoefedrina"])
        item = corpo["evidencias"][0]["itens"][0]
        self.assertEqual((item["criterio_vinculo"], item["fundamento_vinculo"]), ("PRINCIPIO_ATIVO_EXATO", "paracetamol"))

    def test_par_nao_repete_citacao_com_formatacao_diferente(self) -> None:
        primeiro = linha_relacao(40, "varfarina", "losec", "MEDICAMENTO",
                                 evidencia="Uso concomitante com LOSEC deve ser evitado.",
                                 criterio="NOME_MEDICAMENTO_EXATO", sentido="BULA_A_CITA_B")
        repetido = linha_relacao(41, "varfarina", "losec", "MEDICAMENTO",
                                 evidencia="Uso  concomitante com Losec deve ser\n evitado.",
                                 criterio="NOME_MEDICAMENTO_EXATO", sentido="BULA_A_CITA_B")
        efeito_distinto = linha_relacao(42, "varfarina", "losec", "MEDICAMENTO",
                                       evidencia="Uso concomitante com losec deve ser evitado.",
                                       criterio="NOME_MEDICAMENTO_EXATO", sentido="BULA_A_CITA_B")
        efeito_distinto["resumo"] = "Ajustar a dose durante o uso conjunto."
        self.repositorio.par = [primeiro, repetido, efeito_distinto]

        corpo = self.cliente.get("/api/interacoes/par", params={
            "medicamento_a": "varfarina", "medicamento_b": "losec",
        }).json()
        self.assertEqual(len(corpo["evidencias"]), 1)
        self.assertEqual(len(corpo["evidencias"][0]["itens"]), 2)

    def test_par_omite_ausencia_de_estudo_e_preserva_citacao_positiva(self) -> None:
        self.repositorio.par = [
            linha_relacao(43, "varfarina", "losec", "MEDICAMENTO",
                          evidencia="LOSEC não estudado.", criterio="NOME_MEDICAMENTO_EXATO",
                          sentido="BULA_A_CITA_B"),
            linha_relacao(44, "varfarina", "losec", "MEDICAMENTO",
                          evidencia="LOSEC pode aumentar o efeito da varfarina.",
                          criterio="NOME_MEDICAMENTO_EXATO", sentido="BULA_A_CITA_B"),
        ]
        corpo = self.cliente.get("/api/interacoes/par", params={
            "medicamento_a": "varfarina", "medicamento_b": "losec",
        }).json()
        self.assertTrue(corpo["interacao_identificada"])
        self.assertEqual(len(corpo["evidencias"]), 1)
        self.assertNotIn("não estudado", corpo["evidencias"][0]["evidencia"].lower())

    def test_ausencia_de_estudo_sozinha_nao_confirma_interacao(self) -> None:
        self.repositorio.par = [
            linha_relacao(45, "varfarina", "losec", "MEDICAMENTO",
                          evidencia="LOSEC não estudado.", criterio="NOME_MEDICAMENTO_EXATO",
                          sentido="BULA_A_CITA_B"),
        ]
        corpo = self.cliente.get("/api/interacoes/par", params={
            "medicamento_a": "varfarina", "medicamento_b": "losec",
        }).json()
        self.assertFalse(corpo["interacao_identificada"])
        self.assertEqual(corpo["evidencias"], [])


    def test_alerta_de_classe_nao_confirma_par(self) -> None:
        self.repositorio.par = [
            linha_relacao(31, "losec", "anticoagulantes", "CLASSE_FARMACOLOGICA", sentido="BULA_B_CITA_A", pid=2),
        ]
        corpo = self.cliente.get("/api/interacoes/par", params={"medicamento_a": "varfarina", "medicamento_b": "losec"}).json()
        self.assertIs(corpo["interacao_identificada"], False)
        self.assertEqual(corpo["evidencias"], [])
        self.assertIn("não confirmam interação específica", corpo["mensagem"])

    def test_mencao_nao_vinculada_impede_conclusao_negativa(self) -> None:
        self.repositorio.situacoes_["losec"]["principios_ativos"] = "varfarina sódica"
        self.repositorio.par = [
            linha_relacao(40, "varfarina", "varfarina", "PRINCIPIO_ATIVO", sentido="BULA_A_CITA_B"),
            linha_relacao(41, "varfarina", "digoxina", "PRINCIPIO_ATIVO", sentido="BULA_A_CITA_B"),
        ]
        corpo = self.cliente.get("/api/interacoes/par", params={"medicamento_a": "varfarina", "medicamento_b": "losec"}).json()
        self.assertIsNone(corpo["interacao_identificada"])
        self.assertEqual(corpo["evidencias"], [])
        self.assertEqual([m["itens"][0]["alvo"] for m in corpo["mencoes_nao_vinculadas"]], ["varfarina"])
        self.assertIn("sem vínculo gravado", corpo["mensagem"])

    def test_par_incompleto_nao_conclui_negativo(self) -> None:
        corpo = self.cliente.get("/api/interacoes/par", params={"medicamento_a": "varfarina", "medicamento_b": "pendente"}).json()
        self.assertIsNone(corpo["interacao_identificada"])
        self.assertFalse(corpo["cobertura_completa"])
        resposta = self.cliente.get("/api/interacoes/par", params={"medicamento_a": "losec", "medicamento_b": "losec"})
        self.assertEqual(resposta.status_code, 422)

    def test_erros_nao_expoem_detalhes_internos(self) -> None:
        self.repositorio.falhar = True
        resposta = self.cliente.get("/api/interacoes", params={"medicamento": "varfarina"})
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(
            resposta.json()["mensagem"],
            "O serviço de consulta está indisponível no momento.",
        )
        self.repositorio.falhar = False
        self.repositorio.relacoes_para_contagem = lambda *a: (_ for _ in ()).throw(
            RuntimeError("senha=segredo C:\\caminho\\interno")
        )
        cliente = TestClient(criar_app(ConfiguracaoApi(dsn="x"), self.repositorio), raise_server_exceptions=False)
        with self.assertLogs("api_interacoes", level="ERROR") as registro:
            resposta = cliente.get("/api/interacoes", params={"medicamento": "varfarina"})
        self.assertIn("segredo", registro.output[0])  # o detalhe fica só no log do servidor
        self.assertEqual(resposta.status_code, 500)
        self.assertNotIn("segredo", resposta.text)
        self.assertNotIn("caminho", resposta.text)

    def test_cors_configuravel(self) -> None:
        cliente = TestClient(criar_app(ConfiguracaoApi(dsn="x", origens_cors=("http://localhost:3000",)), self.repositorio))
        permitido = cliente.get("/api/saude", headers={"Origin": "http://localhost:3000"})
        self.assertEqual(permitido.headers.get("access-control-allow-origin"), "http://localhost:3000")
        negado = cliente.get("/api/saude", headers={"Origin": "http://malicioso.example"})
        self.assertIsNone(negado.headers.get("access-control-allow-origin"))

    def test_openapi_documenta_endpoints_e_contratos(self) -> None:
        esquema = self.cliente.get("/openapi.json").json()
        for caminho in ("/api/saude", "/api/medicamentos/sugestoes", "/api/interacoes", "/api/interacoes/par"):
            self.assertIn(caminho, esquema["paths"])
        self.assertIn("RespostaPar", esquema["components"]["schemas"])
        self.assertIn("desconhecida", str(esquema["components"]["schemas"]["Relacao"]))


class TestSugestoesAproximadas(unittest.TestCase):
    def test_corrige_erro_no_prefixo_sem_mudar_busca_literal(self) -> None:
        repositorio = RepositorioConsulta.__new__(RepositorioConsulta)
        catalogo = [
            {"nome_normalizado": "sinvastatina", "nome_exibicao": "Sinvastatina"},
            {"nome_normalizado": "sinvastatina ezetimiba", "nome_exibicao": "Sinvastatina Ezetimiba"},
            {"nome_normalizado": "paracetamol", "nome_exibicao": "Paracetamol"},
        ]
        consultas = []

        def consultar(sql, parametros=None):
            consultas.append((sql, parametros))
            if parametros is None:
                return catalogo
            termo = parametros["contem"].strip("%")
            return [item for item in catalogo if termo in item["nome_normalizado"]][:parametros["limite"]]

        repositorio._consultar = consultar
        self.assertEqual(repositorio.sugerir("sinvasti", 6)[0]["nome_normalizado"], "sinvastatina")
        self.assertEqual(repositorio.sugerir("sinvastina", 6)[0]["nome_normalizado"], "sinvastatina")
        self.assertEqual(repositorio.sugerir("parace", 6)[0]["nome_normalizado"], "paracetamol")
        self.assertEqual(len(consultas), 5)  # a busca literal dispensa o catálogo aproximado
        self.assertEqual(repositorio.sugerir("zzzzzzzz", 6), [])


class TestInicializacaoSemInferencia(unittest.TestCase):
    def test_api_nao_carrega_bibliotecas_de_inferencia(self) -> None:
        codigo = (
            "import sys\n"
            "from apps.api_interacoes.app import criar_app\n"
            "from apps.api_interacoes.configuracao import ConfiguracaoApi\n"
            "criar_app(ConfiguracaoApi(dsn='postgresql://ninguem@127.0.0.1:1/x'))\n"
            "proibidos = ('llama_cpp', 'torch', 'transformers', 'pymupdf', 'fitz', 'selenium',"
            " 'pipelines.extracao_interacoes', 'pipelines.conversao_interacoes_vetores', 'pipelines.identificacao_interacoes.servico',"
            " 'pipelines.identificacao_interacoes.provedor', 'pipelines.resumos_interacoes.provedor',"
            " 'pipelines.resumos_interacoes.reescrita', 'huggingface_hub')\n"
            "carregados = sorted(m for m in sys.modules if any(m == p or m.startswith(p + '.') for p in proibidos))\n"
            "print(carregados)\n"
            "sys.exit(1 if carregados else 0)\n"
        )
        execucao = subprocess.run(
            [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(execucao.returncode, 0, execucao.stdout + execucao.stderr)


if __name__ == "__main__":
    unittest.main()
