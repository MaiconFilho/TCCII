"""Integração com PostgreSQL real, somente em banco isolado de testes.

Mostram o comportamento das transações, chaves e advisory locks no servidor;
não provam a ausência de toda condição de corrida possível.
"""

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from identificacao_interacoes.aplicar_migracoes import aplicar, listar_migracoes
from identificacao_interacoes.dicionario import ServicoDicionario
from identificacao_interacoes.pipeline import processar_lote
from identificacao_interacoes.relatorio import RelatorioIdentificacao
from identificacao_interacoes.erros import (
    ErroBancoError,
    FonteAlteradaError,
    MedicamentoEmAndamentoError,
    ProcessamentoJaExisteError,
)
from identificacao_interacoes.modelos import (
    CriterioVinculo,
    RelacaoIdentificada,
    ResultadoIdentificacao,
    StatusProcessamento,
    TipoAlvo,
    Vinculo,
    calcular_hash_trecho,
)
from identificacao_interacoes.repositorio import RepositorioIdentificacao
from identificacao_interacoes.resolucao import CatalogoMedicamentos
from identificacao_interacoes.tests import banco_teste


DSN = banco_teste.dsn_de_teste()
TEXTO = (
    "6. INTERAÇÕES MEDICAMENTOSAS\n"
    "A varfarina pode ter o efeito aumentado; monitore o tempo de protrombina.\n"
    "Evite saladas verdes em grande quantidade."
)
AGORA = datetime(2026, 10, 2, tzinfo=timezone.utc)


def relacoes_padrao() -> list[RelacaoIdentificada]:
    linha = TEXTO.splitlines()[1]
    inicio = TEXTO.index(linha)
    alimento = TEXTO.splitlines()[2]
    return [
        RelacaoIdentificada("varfarina", TipoAlvo.PRINCIPIO_ATIVO, "Pode ter o efeito aumentado.", linha, inicio, inicio + len(linha), 0),
        RelacaoIdentificada("saladas verdes", TipoAlvo.GRUPO_ALIMENTAR, "Evitar saladas verdes em grande quantidade.", alimento, TEXTO.index(alimento), len(TEXTO), 0),
    ]


def resultado(status=StatusProcessamento.CONCLUIDO_COM_RELACOES, relacoes=None) -> ResultadoIdentificacao:
    relacoes = relacoes_padrao() if relacoes is None else relacoes
    return ResultadoIdentificacao(
        status=status, nome_normalizado="origem", relacoes=relacoes,
        quantidade_janelas=1, janelas_concluidas=1, chamadas_llm=1,
    )


@unittest.skipUnless(DSN, banco_teste.motivo_para_pular())
class TestPostgresReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        banco_teste.preparar(DSN)

    def setUp(self) -> None:
        self.conexao = banco_teste.conectar(DSN)
        banco_teste.limpar(self.conexao)
        banco_teste.inserir_bula(self.conexao, "origem", nome_anvisa="ORIGEM", principios="substancia x", trecho=TEXTO)
        banco_teste.inserir_bula(self.conexao, "varfarina a", principios="varfarina")
        banco_teste.inserir_bula(self.conexao, "marevan", principios="varfarina sódica")
        self.repositorio = RepositorioIdentificacao(DSN)

    def tearDown(self) -> None:
        self.repositorio.fechar()
        self.conexao.close()

    def contar(self, tabela: str) -> int:
        return self.conexao.execute(f"SELECT COUNT(*) AS n FROM {tabela}").fetchone()["n"]

    def gravar(self, repositorio=None, versao="v1", reprocessar=False, res=None):
        repositorio = repositorio or self.repositorio
        fonte = repositorio.selecionar_fontes(nome_normalizado="origem")[0]
        res = res or resultado()
        catalogo = CatalogoMedicamentos(repositorio.carregar_catalogo())
        vinculos = [catalogo.resolver(r.alvo, r.tipo_alvo, "origem") for r in res.relacoes]
        return repositorio.gravar_resultado(
            fonte, res, vinculos, versao=versao, modelo="m", iniciado_em=AGORA,
            tempo_total_segundos=1.0, reprocessar=reprocessar,
        )

    def vigente(self):
        return self.conexao.execute(
            "SELECT id, status FROM processamentos_interacoes_vigentes WHERE medicamento_origem = 'origem'"
        ).fetchone()

    def test_migracao_e_idempotente(self) -> None:
        aplicar(DSN, listar_migracoes())
        aplicar(DSN, listar_migracoes())
        self.repositorio.validar_esquema()

    def test_hash_python_igual_ao_postgres(self) -> None:
        fonte = self.repositorio.selecionar_fontes(nome_normalizado="origem")[0]
        self.assertEqual(fonte.hash_trecho_banco, calcular_hash_trecho(TEXTO))
        self.assertTrue(fonte.identificadores_coerentes)

    def test_grava_relacoes_alimento_sem_vinculo_e_sal_com_criterio_proprio(self) -> None:
        processamento_id, vinculos = self.gravar()
        self.assertEqual(vinculos, 2)
        linhas = self.conexao.execute(
            """
            SELECT i.alvo, i.tipo_alvo,
                   ARRAY_REMOVE(ARRAY_AGG(im.medicamento_relacionado || ':' || im.criterio_vinculo
                                          ORDER BY im.medicamento_relacionado), NULL) AS relacionados
            FROM interacoes AS i
            LEFT JOIN interacoes_medicamentos AS im ON im.interacao_id = i.id
            WHERE i.processamento_id = %s
            GROUP BY i.id ORDER BY i.id
            """,
            (processamento_id,),
        ).fetchall()
        self.assertEqual(
            [(l["alvo"], l["tipo_alvo"], l["relacionados"]) for l in linhas],
            [
                ("varfarina", "PRINCIPIO_ATIVO",
                 ["marevan:PRINCIPIO_ATIVO_SEM_SAL", "varfarina a:PRINCIPIO_ATIVO_EXATO"]),
                ("saladas verdes", "GRUPO_ALIMENTAR", []),
            ],
        )
        self.assertEqual(self.vigente()["id"], processamento_id)
        # A evidência gravada é a passagem literal do trecho original.
        evidencias = [l["evidencia"] for l in self.conexao.execute("SELECT evidencia FROM interacoes").fetchall()]
        self.assertTrue(all(e in TEXTO for e in evidencias))

    def test_reexecucao_sem_duplicacao_e_lote_pula_processado(self) -> None:
        self.gravar()
        with self.assertRaises(ProcessamentoJaExisteError):
            self.gravar()
        self.assertEqual(self.contar("interacoes"), 2)
        self.assertEqual(self.repositorio.selecionar_fontes(limite=None), [])

    def test_reprocessamento_preserva_historico(self) -> None:
        primeiro, _ = self.gravar()
        segundo, _ = self.gravar(reprocessar=True)
        self.assertNotEqual(primeiro, segundo)
        self.assertEqual(self.contar("interacoes"), 4)
        self.assertEqual(self.vigente()["id"], segundo)

    def test_falha_posterior_preserva_resultado_anterior(self) -> None:
        primeiro, _ = self.gravar()
        fonte = self.repositorio.selecionar_fontes(nome_normalizado="origem")[0]
        self.repositorio.registrar_falha(
            fonte, None, versao="v2", modelo="m", iniciado_em=AGORA, tempo_total_segundos=1, detalhe="x"
        )
        self.assertEqual(self.vigente()["id"], primeiro)

    def test_mudanca_da_fonte_desativa_resultado_antigo_sem_apagar(self) -> None:
        self.gravar()
        self.conexao.execute(
            "UPDATE bulas_interacoes SET trecho_interacoes = trecho_interacoes || ' Nova frase.' WHERE nome_normalizado = 'origem'"
        )
        self.assertIsNone(self.vigente())
        self.assertEqual(self.contar("interacoes"), 2)
        self.assertEqual(len(self.repositorio.selecionar_fontes(limite=None)), 1)

    def test_bula_nova_desativa_resultado_antigo(self) -> None:
        self.gravar()
        self.conexao.execute("UPDATE bulas SET sha256_pdf = 'outro' WHERE nome_normalizado = 'origem'")
        self.assertIsNone(self.vigente())
        historico = self.conexao.execute("SELECT sha256_pdf FROM processamentos_interacoes").fetchone()
        self.assertEqual(historico["sha256_pdf"], "pdf")

    def test_fonte_alterada_entre_selecao_e_gravacao(self) -> None:
        fonte = self.repositorio.selecionar_fontes(nome_normalizado="origem")[0]
        self.conexao.execute("UPDATE bulas SET expediente = '999' WHERE nome_normalizado = 'origem'")
        with self.assertRaises(FonteAlteradaError):
            self.repositorio.gravar_resultado(
                fonte, resultado(), [[], []], versao="v1", modelo="m",
                iniciado_em=AGORA, tempo_total_segundos=1,
            )
        self.assertEqual(self.contar("processamentos_interacoes"), 0)

    def test_rollback_completo_em_falha_no_meio_da_gravacao(self) -> None:
        fonte = self.repositorio.selecionar_fontes(nome_normalizado="origem")[0]
        inexistente = [Vinculo("nao existe", CriterioVinculo.PRINCIPIO_ATIVO_EXATO, "x")]
        with self.assertRaises(ErroBancoError):
            self.repositorio.gravar_resultado(
                fonte, resultado(), [[], inexistente], versao="v1", modelo="m",
                iniciado_em=AGORA, tempo_total_segundos=1,
            )
        for tabela in ("processamentos_interacoes", "interacoes", "interacoes_medicamentos"):
            self.assertEqual(self.contar(tabela), 0, tabela)

    def test_bloqueio_de_sessao_entre_processos(self) -> None:
        outro = RepositorioIdentificacao(DSN)
        try:
            self.assertTrue(self.repositorio.bloquear_medicamento("origem"))
            self.assertFalse(outro.bloquear_medicamento("origem"))
            self.assertTrue(outro.bloquear_medicamento("varfarina a"))
            self.repositorio.liberar_medicamento("origem")
            self.assertTrue(outro.bloquear_medicamento("origem"))
        finally:
            outro.fechar()

    def test_gravacoes_concorrentes_gravam_uma_unica_versao(self) -> None:
        repositorios = [RepositorioIdentificacao(DSN) for _ in range(3)]
        barreira = threading.Barrier(len(repositorios))
        saidas: list[str] = []

        def executar(repositorio):
            barreira.wait()
            try:
                self.gravar(repositorio)
                saidas.append("gravou")
            except ProcessamentoJaExisteError:
                saidas.append("ignorado")

        try:
            threads = [threading.Thread(target=executar, args=(r,)) for r in repositorios]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        finally:
            for repositorio in repositorios:
                repositorio.fechar()
        self.assertEqual(sorted(saidas), ["gravou", "ignorado", "ignorado"])
        self.assertEqual(self.contar("processamentos_interacoes"), 1)
        self.assertEqual(self.contar("interacoes"), 2)

    def test_atualizacao_de_vinculos_sem_apagar(self) -> None:
        processamento_id, _ = self.gravar()
        banco_teste.inserir_bula(self.conexao, "varfarina nova", principios="varfarina")
        self.conexao.execute("UPDATE bulas SET principios_ativos = 'outra coisa' WHERE nome_normalizado = 'varfarina a'")
        catalogo = CatalogoMedicamentos(self.repositorio.carregar_catalogo())
        desejados = {
            i["id"]: catalogo.resolver(i["alvo"], TipoAlvo(i["tipo_alvo"]), "origem")
            for i in self.repositorio.interacoes_do_processamento(processamento_id)
        }
        contagem = self.repositorio.atualizar_vinculos(processamento_id, "origem", desejados)
        self.assertEqual((contagem["criados"], contagem["invalidados"]), (1, 1))
        linhas = {
            l["medicamento_relacionado"]: l["invalidado_em"]
            for l in self.conexao.execute("SELECT * FROM interacoes_medicamentos").fetchall()
        }
        self.assertIsNotNone(linhas["varfarina a"])
        self.assertIsNone(linhas["varfarina nova"])
        self.conexao.execute("UPDATE bulas SET principios_ativos = 'varfarina' WHERE nome_normalizado = 'varfarina a'")
        catalogo = CatalogoMedicamentos(self.repositorio.carregar_catalogo())
        desejados = {k: catalogo.resolver("varfarina", TipoAlvo.PRINCIPIO_ATIVO, "origem") if v or k == 1 else [] for k, v in desejados.items()}
        contagem = self.repositorio.atualizar_vinculos(processamento_id, "origem", desejados)
        self.assertEqual(contagem["reativados"], 1)
        # marevan (sem sal), varfarina a (reativado) e varfarina nova; nada apagado.
        self.assertEqual(self.contar("interacoes_medicamentos"), 3)

    def test_atualizacao_de_vinculos_respeita_processamento_em_andamento(self) -> None:
        processamento_id, _ = self.gravar()
        outro = RepositorioIdentificacao(DSN)
        try:
            self.assertTrue(outro.bloquear_medicamento("origem"))
            with self.assertRaises(MedicamentoEmAndamentoError):
                self.repositorio.atualizar_vinculos(processamento_id, "origem", {1: []})
        finally:
            outro.fechar()

    def test_exclusao_protegida_por_chave_estrangeira(self) -> None:
        self.gravar()
        with self.assertRaises(psycopg.errors.IntegrityError):
            self.conexao.execute("DELETE FROM bulas WHERE nome_normalizado = 'varfarina a'")
        with self.assertRaises(psycopg.errors.IntegrityError):
            self.conexao.execute("DELETE FROM processamentos_interacoes")

    def test_dicionario_de_ponta_a_ponta_e_lote_por_versao(self) -> None:
        servico = ServicoDicionario(CatalogoMedicamentos(self.repositorio.carregar_catalogo()), ignorados=frozenset())
        versao = servico.versao_processamento
        fontes = self.repositorio.selecionar_fontes(limite=None, versao=versao)
        self.assertEqual([f.nome_normalizado for f in fontes], ["origem"])
        with tempfile.TemporaryDirectory() as pasta, \
                RelatorioIdentificacao(Path(pasta) / "dicionario.csv") as relatorio:
            resumo = processar_lote(
                fontes, servico, CatalogoMedicamentos(self.repositorio.carregar_catalogo()),
                self.repositorio, relatorio,
            )
        self.assertEqual(resumo, {"CONCLUIDO_COM_RELACOES": 1})
        linha = self.conexao.execute(
            """
            SELECT v.modelo, v.chamadas_llm, i.alvo, i.evidencia,
                   ARRAY_AGG(im.criterio_vinculo ORDER BY im.medicamento_relacionado) AS criterios
            FROM processamentos_interacoes_vigentes AS v
            JOIN interacoes AS i ON i.processamento_id = v.id
            JOIN interacoes_medicamentos AS im ON im.interacao_id = i.id
            WHERE v.medicamento_origem = 'origem'
            GROUP BY v.modelo, v.chamadas_llm, i.id, i.alvo, i.evidencia
            """
        ).fetchall()
        self.assertEqual(len(linha), 1)
        self.assertEqual((linha[0]["modelo"], linha[0]["chamadas_llm"]), ("nenhum (busca por nomes do banco)", 0))
        self.assertEqual(linha[0]["criterios"], ["PRINCIPIO_ATIVO_SEM_SAL", "PRINCIPIO_ATIVO_EXATO"])
        self.assertIn(linha[0]["evidencia"], TEXTO)
        # A mesma versão não volta no lote; outra versão volta.
        self.assertEqual(self.repositorio.selecionar_fontes(limite=None, versao=versao), [])
        self.assertEqual(len(self.repositorio.selecionar_fontes(limite=None, versao="outra")), 1)

    def test_status_e_janelas_coerentes_por_restricao(self) -> None:
        fonte = self.repositorio.selecionar_fontes(nome_normalizado="origem")[0]
        parcial = resultado()
        parcial.janelas_concluidas = 0
        with self.assertRaises(ErroBancoError):
            self.repositorio.gravar_resultado(
                fonte, parcial, [[], []], versao="v1", modelo="m",
                iniciado_em=AGORA, tempo_total_segundos=1,
            )


if __name__ == "__main__":
    unittest.main()
