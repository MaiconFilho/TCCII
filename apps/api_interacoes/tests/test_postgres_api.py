"""API contra PostgreSQL real, somente no banco isolado de testes."""

import unittest
from datetime import datetime, timezone

import psycopg
from fastapi.testclient import TestClient

from apps.api_interacoes.app import criar_app
from apps.api_interacoes.configuracao import ConfiguracaoApi
from pipelines.identificacao_interacoes.modelos import (
    RelacaoIdentificada,
    ResultadoIdentificacao,
    StatusProcessamento,
    TipoAlvo,
)
from pipelines.identificacao_interacoes.repositorio import RepositorioIdentificacao
from pipelines.identificacao_interacoes.resolucao import CatalogoMedicamentos
from pipelines.identificacao_interacoes.tests import banco_teste


DSN = banco_teste.dsn_de_teste()
AGORA = datetime(2026, 10, 2, tzinfo=timezone.utc)

TEXTO_MAREVAN = (
    "6. INTERAÇÕES MEDICAMENTOSAS\n"
    "O ácido acetilsalicílico aumenta o risco de sangramento.\n"
    "Saladas verdes em grande quantidade reduzem o efeito.\n"
    "Anti-inflamatórios podem aumentar o risco de sangramento."
)
TEXTO_AAS = "O uso com varfarina sódica aumenta o risco de sangramento."
TEXTO_LOSEC = "Não foram realizados estudos de interação."


def relacao(texto: str, frase: str, alvo: str, tipo: TipoAlvo) -> RelacaoIdentificada:
    inicio = texto.index(frase)
    return RelacaoIdentificada(alvo, tipo, f"Resumo: {frase}", frase, inicio, inicio + len(frase), 0)


@unittest.skipUnless(DSN, banco_teste.motivo_para_pular())
class TestApiPostgresReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        banco_teste.preparar(DSN)

    def setUp(self) -> None:
        self.conexao = banco_teste.conectar(DSN)
        banco_teste.limpar(self.conexao)
        inserir = banco_teste.inserir_bula
        inserir(self.conexao, "marevan", nome_anvisa="MAREVAN", principios="varfarina sódica", trecho=TEXTO_MAREVAN)
        inserir(self.conexao, "aas", nome_anvisa="AAS", principios="ácido acetilsalicílico", trecho=TEXTO_AAS)
        inserir(self.conexao, "aspirina", nome_anvisa="ASPIRINA", principios="ácido acetilsalicílico")
        inserir(self.conexao, "losec", nome_anvisa="LOSEC", principios="omeprazol", trecho=TEXTO_LOSEC)
        inserir(self.conexao, "pendente", nome_anvisa="PENDENTE", principios="x", trecho="Evite álcool com este produto.")
        inserir(self.conexao, "acido folico", nome_anvisa="ÁCIDO FÓLICO", principios="ácido fólico")
        self.identificacao = RepositorioIdentificacao(DSN)
        self.gravar("marevan", [
            relacao(TEXTO_MAREVAN, "O ácido acetilsalicílico aumenta o risco de sangramento.", "ácido acetilsalicílico", TipoAlvo.PRINCIPIO_ATIVO),
            relacao(TEXTO_MAREVAN, "Saladas verdes em grande quantidade reduzem o efeito.", "saladas verdes", TipoAlvo.GRUPO_ALIMENTAR),
            relacao(TEXTO_MAREVAN, "Anti-inflamatórios podem aumentar o risco de sangramento.", "anti-inflamatórios", TipoAlvo.CLASSE_FARMACOLOGICA),
        ])
        self.gravar("aas", [relacao(TEXTO_AAS, TEXTO_AAS, "varfarina sódica", TipoAlvo.PRINCIPIO_ATIVO)])
        self.gravar("losec", [], StatusProcessamento.CONCLUIDO_SEM_RELACOES)
        self.cliente_ctx = TestClient(criar_app(ConfiguracaoApi(dsn=DSN, cache_cobertura_segundos=0)))
        self.cliente = self.cliente_ctx.__enter__()

    def tearDown(self) -> None:
        self.cliente_ctx.__exit__(None, None, None)
        self.identificacao.fechar()
        self.conexao.close()

    def gravar(self, nome, relacoes, status=StatusProcessamento.CONCLUIDO_COM_RELACOES, versao="v1"):
        fonte = self.identificacao.selecionar_fontes(nome_normalizado=nome)[0]
        resultado = ResultadoIdentificacao(
            status=status, nome_normalizado=nome, relacoes=relacoes,
            quantidade_janelas=1, janelas_concluidas=1,
        )
        catalogo = CatalogoMedicamentos(self.identificacao.carregar_catalogo())
        vinculos = [catalogo.resolver(r.alvo, r.tipo_alvo, nome) for r in relacoes]
        return self.identificacao.gravar_resultado(
            fonte, resultado, vinculos, versao=versao, modelo="m", iniciado_em=AGORA, tempo_total_segundos=1
        )[0]

    def get(self, caminho, **params):
        resposta = self.cliente.get(caminho, params=params)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta.json()

    def test_saude(self) -> None:
        self.assertEqual(self.get("/api/saude")["status"], "ok")

    def test_autocomplete_prioriza_prefixo_acentos_e_escapa_curinga(self) -> None:
        nomes = [s["nome_normalizado"] for s in self.get("/api/medicamentos/sugestoes", q="AS")["sugestoes"]]
        self.assertEqual(nomes, ["aspirina", "aas"])
        folico = self.get("/api/medicamentos/sugestoes", q="Ácido fó")["sugestoes"]
        self.assertEqual(folico, [{"nome_normalizado": "acido folico", "nome_exibicao": "ÁCIDO FÓLICO"}])
        self.assertEqual(self.get("/api/medicamentos/sugestoes", q="%")["sugestoes"], [])

    def test_consulta_individual_sem_eliminar_alvos_sem_vinculo(self) -> None:
        corpo = self.get("/api/interacoes", medicamento="marevan")
        alvos = {r["alvo"]: r for r in corpo["relacoes"]}
        self.assertEqual(set(alvos), {"ácido acetilsalicílico", "saladas verdes", "anti-inflamatórios"})
        self.assertEqual(alvos["saladas verdes"]["medicamentos_relacionados"], [])
        self.assertEqual(
            sorted(m["nome_normalizado"] for m in alvos["ácido acetilsalicílico"]["medicamentos_relacionados"]),
            ["aas", "aspirina"],
        )
        self.assertTrue(all(r["fonte"]["medicamento"]["nome_normalizado"] == "marevan" for r in corpo["relacoes"]))
        self.assertTrue(corpo["interacao_identificada"])
        self.assertTrue(all(r["evidencia"] in TEXTO_MAREVAN + TEXTO_AAS for r in corpo["relacoes"]))

    def test_medicamento_so_citado_por_outra_bula(self) -> None:
        corpo = self.get("/api/interacoes", medicamento="aspirina")
        self.assertEqual(corpo["analise_bula"]["estado"], "EXTRACAO_INDISPONIVEL")
        self.assertEqual(corpo["relacoes"], [])
        self.assertIsNone(corpo["interacao_identificada"])

    def test_paginacao_estavel(self) -> None:
        ids = []
        for pagina in (1, 2, 3, 4):
            corpo = self.get("/api/interacoes", medicamento="marevan", pagina=pagina, tamanho_pagina=1)
            self.assertEqual(corpo["paginacao"]["total"], 3)
            ids += [r["id"] for r in corpo["relacoes"]]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(self.get("/api/interacoes", medicamento="marevan", pagina=9, tamanho_pagina=1)["relacoes"], [])

    def test_par_nos_dois_sentidos(self) -> None:
        corpo = self.get("/api/interacoes/par", medicamento_a="marevan", medicamento_b="aas")
        self.assertTrue(corpo["interacao_identificada"])
        self.assertEqual([e["sentido"] for e in corpo["evidencias"]], ["BULA_A_CITA_B", "BULA_B_CITA_A"])
        invertido = self.get("/api/interacoes/par", medicamento_a="aas", medicamento_b="marevan")
        self.assertEqual(len(invertido["evidencias"]), 2)

    def test_par_concluido_sem_relacao_com_alerta_de_classe(self) -> None:
        corpo = self.get("/api/interacoes/par", medicamento_a="marevan", medicamento_b="losec")
        self.assertIs(corpo["interacao_identificada"], False)
        self.assertTrue(corpo["mensagem"].startswith("Não foi identificada interação nas fontes analisadas."))
        self.assertEqual([a["itens"][0]["alvo"] for a in corpo["alertas_nao_confirmados"]], ["anti-inflamatórios"])

    def test_sal_diferente_vincula_pela_forma_sem_sal(self) -> None:
        texto = "O uso com varfarina aumenta o risco de sangramento."
        banco_teste.inserir_bula(self.conexao, "aas generico", principios="ácido acetilsalicílico", trecho=texto)
        self.gravar("aas generico", [relacao(texto, texto, "varfarina", TipoAlvo.PRINCIPIO_ATIVO)])
        criterios = [l["criterio_vinculo"] for l in self.conexao.execute(
            "SELECT im.criterio_vinculo FROM interacoes_medicamentos im JOIN interacoes i ON i.id = im.interacao_id "
            "WHERE i.medicamento_origem = 'aas generico'"
        ).fetchall()]
        # 'varfarina' no texto e 'varfarina sódica' no cadastro (migração 003).
        self.assertEqual(criterios, ["PRINCIPIO_ATIVO_SEM_SAL"])
        corpo = self.get("/api/interacoes/par", medicamento_a="aas generico", medicamento_b="marevan")
        self.assertTrue(corpo["interacao_identificada"])
        self.assertEqual(
            [(e["sentido"], e["itens"][0]["criterio_vinculo"]) for e in corpo["evidencias"]],
            [("BULA_A_CITA_B", "PRINCIPIO_ATIVO_SEM_SAL")],
        )
        # 'aas generico' entrou no cadastro depois do processamento do Marevan:
        # a menção ao princípio ativo ainda não tem vínculo.
        self.assertEqual(
            [m["itens"][0]["alvo"] for m in corpo["mencoes_nao_vinculadas"]], ["ácido acetilsalicílico"]
        )

        # Atualização de vínculos sem LLM: o Marevan passa a citar o novo medicamento.
        # Chama o repositório direto (o pipeline exige dependências da identificação).
        catalogo = CatalogoMedicamentos(self.identificacao.carregar_catalogo())
        (processamento,) = self.identificacao.processamentos_para_vinculos(limite=None, nome_normalizado="marevan")
        desejados = {
            i["id"]: catalogo.resolver(i["alvo"], TipoAlvo(i["tipo_alvo"]), "marevan")
            for i in self.identificacao.interacoes_do_processamento(processamento["id"])
        }
        contagem = self.identificacao.atualizar_vinculos(processamento["id"], "marevan", desejados)
        self.assertEqual(contagem["criados"], 1)
        corpo = self.get("/api/interacoes/par", medicamento_a="aas generico", medicamento_b="marevan")
        self.assertTrue(corpo["interacao_identificada"])
        self.assertEqual([e["sentido"] for e in corpo["evidencias"]], ["BULA_A_CITA_B", "BULA_B_CITA_A"])
        self.assertEqual(corpo["mencoes_nao_vinculadas"], [])

    def test_pendente_versus_concluido_sem_relacoes(self) -> None:
        pendente = self.get("/api/interacoes", medicamento="pendente")
        self.assertEqual(pendente["analise_bula"]["estado"], "NAO_PROCESSADO")
        self.assertIsNone(pendente["interacao_identificada"])
        losec = self.get("/api/interacoes", medicamento="losec")
        self.assertEqual(losec["analise_bula"]["estado"], "CONCLUIDO_SEM_RELACOES")
        self.assertIs(losec["interacao_identificada"], False)
        par = self.get("/api/interacoes/par", medicamento_a="marevan", medicamento_b="pendente")
        self.assertIsNone(par["interacao_identificada"])

    def test_em_processamento_por_advisory_lock(self) -> None:
        self.assertTrue(self.identificacao.bloquear_medicamento("pendente"))
        corpo = self.get("/api/interacoes", medicamento="pendente")
        self.assertEqual(corpo["analise_bula"]["estado"], "EM_PROCESSAMENTO")
        self.identificacao.liberar_medicamento("pendente")

    def test_fonte_alterada_vira_desatualizado_e_preserva_outras_bulas(self) -> None:
        self.conexao.execute(
            "UPDATE bulas_interacoes SET trecho_interacoes = trecho_interacoes || ' Nova.' WHERE nome_normalizado = 'marevan'"
        )
        corpo = self.get("/api/interacoes", medicamento="marevan")
        self.assertEqual(corpo["analise_bula"]["estado"], "DESATUALIZADO")
        self.assertEqual(corpo["relacoes"], [])
        self.assertEqual(corpo["total_interacoes"], 0)
        # A consulta individual contém somente a própria bula; a outra fonte
        # permanece disponível na consulta de AAS e na consulta do par.
        outra = self.get("/api/interacoes", medicamento="aas")
        self.assertEqual([r["alvo"] for r in outra["relacoes"]], ["varfarina sódica"])
        par = self.get("/api/interacoes/par", medicamento_a="marevan", medicamento_b="aas")
        self.assertEqual([e["sentido"] for e in par["evidencias"]], ["BULA_B_CITA_A"])

    def test_cobertura_incompleta_informada(self) -> None:
        cobertura = self.get("/api/interacoes", medicamento="losec")["cobertura"]
        self.assertEqual((cobertura["bulas_elegiveis"], cobertura["bulas_analisadas"]), (4, 3))
        self.assertFalse(cobertura["lista_exaustiva"])

    def test_revisao_nao_e_publicada_como_conclusao(self) -> None:
        texto = "Evite álcool com este produto."
        self.gravar("pendente", [relacao(texto, texto, "álcool", TipoAlvo.OUTRA_SUBSTANCIA)],
                    StatusProcessamento.REVISAO_NECESSARIA)
        corpo = self.get("/api/interacoes", medicamento="pendente")
        self.assertEqual(corpo["analise_bula"]["estado"], "REVISAO_NECESSARIA")
        self.assertEqual(corpo["relacoes"], [])
        self.assertIsNone(corpo["interacao_identificada"])
        self.assertEqual(corpo["cobertura"]["bulas_em_revisao"], 1)

    def test_revisao_posterior_preserva_versao_concluida(self) -> None:
        texto = "Evite álcool com este produto."
        self.gravar("pendente", [relacao(texto, texto, "álcool", TipoAlvo.OUTRA_SUBSTANCIA)])
        self.gravar("pendente", [], StatusProcessamento.REVISAO_NECESSARIA, versao="v2")
        corpo = self.get("/api/interacoes", medicamento="pendente")
        self.assertEqual(corpo["analise_bula"]["estado"], "CONCLUIDO_COM_RELACOES")
        self.assertEqual([r["alvo"] for r in corpo["relacoes"]], ["álcool"])
        self.assertEqual(corpo["cobertura"]["bulas_em_revisao"], 0)

    def test_retirada_explicita_some_da_api_sem_apagar(self) -> None:
        vigente = self.identificacao.processamento_vigente("losec")["id"]
        self.identificacao.invalidar_processamento(vigente, "Conclusão negativa incorreta.")
        corpo = self.get("/api/interacoes", medicamento="losec")
        self.assertEqual(corpo["analise_bula"]["estado"], "RETIRADO")
        self.assertIsNone(corpo["interacao_identificada"])
        linha = self.conexao.execute(
            "SELECT status, invalidado_em IS NOT NULL AS retirado FROM processamentos_interacoes WHERE id = %s",
            (vigente,),
        ).fetchone()
        self.assertEqual((linha["status"], linha["retirado"]), ("CONCLUIDO_SEM_RELACOES", True))

    def test_conexao_da_api_e_somente_leitura(self) -> None:
        repositorio = self.cliente.app.state.repositorio
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            with repositorio.pool.connection() as conexao:
                conexao.execute("CREATE TABLE tentativa_escrita (id int)")


if __name__ == "__main__":
    unittest.main()
