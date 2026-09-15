import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.erros import (
    DimensaoInvalidaError,
    ErroBancoError,
)
from conversao_interacoes_vetores.modelos import (
    InteracaoParaVetorizar,
    StatusEmbedding,
)
from conversao_interacoes_vetores.pipeline import processar_lote
from conversao_interacoes_vetores.relatorio import COLUNAS_RELATORIO
from conversao_interacoes_vetores.servico import ServicoEmbeddings
from tests.helpers import (
    MedidorFalso,
    ProvedorFalso,
    RelatorioFalso,
    RepositorioFalso,
    configuracao,
    texto_com_palavras,
)


def criar_servico(provedor=None, config=None):
    return ServicoEmbeddings(
        provedor or ProvedorFalso(), config or configuracao(), MedidorFalso
    )


def interacao(nome="dipirona", texto="Evitar o uso com varfarina."):
    return InteracaoParaVetorizar(nome_normalizado=nome, trecho_interacoes=texto)


class TestFluxoFeliz(unittest.TestCase):
    def test_grava_os_vetores_e_registra_no_relatorio(self):
        repositorio = RepositorioFalso()
        relatorio = RelatorioFalso()

        resumo = processar_lote(
            [interacao()], criar_servico(), repositorio, relatorio
        )

        self.assertEqual(resumo, {StatusEmbedding.CONCLUIDO.value: 1})
        self.assertEqual(len(repositorio.gravacoes), 1)
        self.assertEqual(len(relatorio.linhas), 1)

    def test_relatorio_possui_todas_as_colunas_previstas(self):
        relatorio = RelatorioFalso()

        processar_lote([interacao()], criar_servico(), RepositorioFalso(), relatorio)

        self.assertEqual(set(relatorio.linhas[0]), set(COLUNAS_RELATORIO))

    def test_metricas_chegam_ao_relatorio(self):
        relatorio = RelatorioFalso()

        processar_lote([interacao()], criar_servico(), RepositorioFalso(), relatorio)

        linha = relatorio.linhas[0]
        self.assertEqual(linha["quantidade_chunks"], 1)
        self.assertEqual(linha["modelo"], "modelo/fake-small")
        self.assertEqual(linha["dimensao"], 4)
        self.assertEqual(linha["memoria_antes_mb"], 100.0)
        self.assertEqual(linha["pico_memoria_mb"], 120.0)
        self.assertGreaterEqual(linha["tempo_banco_segundos"], 0)
        self.assertGreaterEqual(linha["tempo_total_segundos"], 0)
        self.assertEqual(len(linha["texto_hash"]), 64)

    def test_processa_varios_registros_sequencialmente(self):
        repositorio = RepositorioFalso()

        resumo = processar_lote(
            [interacao("a"), interacao("b"), interacao("c")],
            criar_servico(),
            repositorio,
            RelatorioFalso(),
        )

        self.assertEqual(resumo, {StatusEmbedding.CONCLUIDO.value: 3})
        self.assertEqual(
            [gravacao[0] for gravacao in repositorio.gravacoes], ["a", "b", "c"]
        )

    def test_texto_longo_grava_todos_os_chunks(self):
        repositorio = RepositorioFalso()
        servico = criar_servico(
            config=configuracao(tamanho_chunk=10, sobreposicao_chunk=2)
        )

        processar_lote(
            [interacao(texto=texto_com_palavras(40))],
            servico,
            repositorio,
            RelatorioFalso(),
        )

        chunks = repositorio.gravacoes[0][2]
        self.assertGreater(len(chunks), 1)
        self.assertEqual(
            [c.chunk_index for c in chunks], list(range(len(chunks)))
        )


class TestIdempotenciaNoLote(unittest.TestCase):
    def test_registro_ja_existente_e_ignorado_sem_gravar(self):
        servico = criar_servico()
        texto = "Evitar o uso com varfarina."
        hashes = servico.calcular_hashes("dipirona", servico.preparar_chunks(texto))
        repositorio = RepositorioFalso(hashes=hashes)

        resumo = processar_lote(
            [interacao(texto=texto)], servico, repositorio, RelatorioFalso()
        )

        self.assertEqual(resumo, {StatusEmbedding.IGNORADO_JA_EXISTENTE.value: 1})
        self.assertEqual(repositorio.gravacoes, [])

    def test_reprocessar_regrava_o_registro(self):
        servico = criar_servico()
        texto = "Evitar o uso com varfarina."
        hashes = servico.calcular_hashes("dipirona", servico.preparar_chunks(texto))
        repositorio = RepositorioFalso(hashes=hashes)

        resumo = processar_lote(
            [interacao(texto=texto)],
            servico,
            repositorio,
            RelatorioFalso(),
            reprocessar=True,
        )

        self.assertEqual(resumo, {StatusEmbedding.REPROCESSADO.value: 1})
        self.assertEqual(len(repositorio.gravacoes), 1)

    def test_texto_alterado_e_regravado_mesmo_sem_reprocessar(self):
        servico = criar_servico()
        hashes = servico.calcular_hashes(
            "dipirona", servico.preparar_chunks("Texto antigo.")
        )
        repositorio = RepositorioFalso(hashes=hashes)

        resumo = processar_lote(
            [interacao(texto="Texto novo e diferente.")],
            servico,
            repositorio,
            RelatorioFalso(),
        )

        self.assertEqual(resumo, {StatusEmbedding.REPROCESSADO.value: 1})
        self.assertEqual(len(repositorio.gravacoes), 1)


class TestFalhas(unittest.TestCase):
    def test_sem_texto_nao_chega_ao_banco(self):
        repositorio = RepositorioFalso()

        resumo = processar_lote(
            [interacao(texto=None)], criar_servico(), repositorio, RelatorioFalso()
        )

        self.assertEqual(resumo, {StatusEmbedding.SEM_TEXTO.value: 1})
        self.assertEqual(repositorio.gravacoes, [])

    def test_erro_de_banco_vira_status_proprio_e_o_lote_continua(self):
        repositorio = RepositorioFalso(erro=ErroBancoError("conexão perdida"))
        relatorio = RelatorioFalso()

        with self.assertLogs("conversao_interacoes_vetores.pipeline", level="ERROR"):
            resumo = processar_lote(
                [interacao("a"), interacao("b")],
                criar_servico(),
                repositorio,
                relatorio,
            )

        self.assertEqual(resumo, {StatusEmbedding.ERRO_BANCO.value: 2})
        self.assertEqual(len(relatorio.linhas), 2)

    def test_dimensao_invalida_na_gravacao_e_reportada(self):
        repositorio = RepositorioFalso(erro=DimensaoInvalidaError("384 != 768"))

        with self.assertLogs("conversao_interacoes_vetores.pipeline", level="ERROR"):
            resumo = processar_lote(
                [interacao()], criar_servico(), repositorio, RelatorioFalso()
            )

        self.assertEqual(resumo, {StatusEmbedding.DIMENSAO_INVALIDA.value: 1})

    def test_falha_ao_consultar_hashes_nao_interrompe_o_lote(self):
        class RepositorioSemConsulta(RepositorioFalso):
            def hashes_existentes(self, nome_normalizado):
                raise RuntimeError("timeout")

        repositorio = RepositorioSemConsulta()

        with self.assertLogs("conversao_interacoes_vetores.pipeline", level="ERROR"):
            resumo = processar_lote(
                [interacao("a"), interacao("b")],
                criar_servico(),
                repositorio,
                RelatorioFalso(),
            )

        self.assertEqual(resumo, {StatusEmbedding.ERRO_BANCO.value: 2})
        self.assertEqual(repositorio.gravacoes, [])

    def test_vetor_anterior_e_preservado_quando_a_gravacao_falha(self):
        servico = criar_servico()
        hashes = servico.calcular_hashes(
            "dipirona", servico.preparar_chunks("Texto antigo.")
        )
        repositorio = RepositorioFalso(
            hashes=hashes, erro=ErroBancoError("falha no commit")
        )

        with self.assertLogs("conversao_interacoes_vetores.pipeline", level="ERROR"):
            processar_lote(
                [interacao(texto="Texto novo.")],
                servico,
                repositorio,
                RelatorioFalso(),
            )

        # Nada foi gravado: o repositório mantém os hashes (e vetores) antigos.
        self.assertEqual(repositorio.gravacoes, [])
        self.assertEqual(repositorio.hashes, hashes)


if __name__ == "__main__":
    unittest.main()
