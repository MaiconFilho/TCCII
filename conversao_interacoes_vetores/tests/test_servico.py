import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.erros import ErroInferenciaError
from conversao_interacoes_vetores.hashing import calcular_hash_chunk
from conversao_interacoes_vetores.modelos import StatusEmbedding
from conversao_interacoes_vetores.servico import ServicoEmbeddings
from tests.helpers import (
    MedidorEstourado,
    MedidorFalso,
    ProvedorFalso,
    configuracao,
    texto_com_palavras,
)


def criar_servico(provedor=None, config=None, medidor=MedidorFalso):
    provedor = provedor or ProvedorFalso()
    return ServicoEmbeddings(provedor, config or configuracao(), medidor)


class TestTextoCurtoELongo(unittest.TestCase):
    def test_texto_curto_gera_um_chunk_com_o_texto_original(self):
        servico = criar_servico()
        texto = "Evitar o uso com varfarina."

        resultado = servico.gerar_para_interacao("dipirona", texto)

        self.assertEqual(resultado.status, StatusEmbedding.CONCLUIDO)
        self.assertEqual(resultado.quantidade_chunks, 1)
        self.assertEqual(resultado.chunks[0].chunk_index, 0)
        self.assertEqual(resultado.chunks[0].texto_chunk, texto)

    def test_texto_longo_gera_varios_chunks_ordenados(self):
        servico = criar_servico(config=configuracao(tamanho_chunk=10, sobreposicao_chunk=2))

        resultado = servico.gerar_para_interacao("dipirona", texto_com_palavras(40))

        self.assertGreater(resultado.quantidade_chunks, 1)
        self.assertEqual(
            [chunk.chunk_index for chunk in resultado.chunks],
            list(range(resultado.quantidade_chunks)),
        )

    def test_um_vetor_por_chunk_com_a_dimensao_do_modelo(self):
        servico = criar_servico(config=configuracao(tamanho_chunk=10, sobreposicao_chunk=2))

        resultado = servico.gerar_para_interacao("dipirona", texto_com_palavras(40))

        for chunk in resultado.chunks:
            self.assertEqual(chunk.dimensao, 4)

    def test_tamanho_do_chunk_nunca_excede_o_limite_do_modelo(self):
        provedor = ProvedorFalso(limite_tokens=5)
        servico = criar_servico(
            provedor=provedor,
            config=configuracao(tamanho_chunk=400, sobreposicao_chunk=1),
        )

        resultado = servico.gerar_para_interacao("dipirona", texto_com_palavras(20))

        for chunk in resultado.chunks:
            self.assertLessEqual(chunk.quantidade_tokens, 5)

    def test_sem_texto_devolve_status_proprio(self):
        servico = criar_servico()

        self.assertEqual(
            servico.gerar_para_interacao("dipirona", None).status,
            StatusEmbedding.SEM_TEXTO,
        )
        self.assertEqual(
            servico.gerar_para_interacao("dipirona", "   ").status,
            StatusEmbedding.SEM_TEXTO,
        )


class TestIdempotencia(unittest.TestCase):
    def _hashes_de(self, servico, nome, texto):
        return servico.calcular_hashes(nome, servico.preparar_chunks(texto))

    def test_hashes_identicos_evitam_nova_inferencia(self):
        provedor = ProvedorFalso()
        servico = criar_servico(provedor=provedor)
        texto = "Evitar o uso com varfarina."
        hashes = self._hashes_de(servico, "dipirona", texto)

        resultado = servico.gerar_para_interacao(
            "dipirona", texto, hashes_existentes=hashes
        )

        self.assertEqual(resultado.status, StatusEmbedding.IGNORADO_JA_EXISTENTE)
        self.assertEqual(provedor.chamadas, [])

    def test_reprocessar_forca_nova_inferencia(self):
        provedor = ProvedorFalso()
        servico = criar_servico(provedor=provedor)
        texto = "Evitar o uso com varfarina."
        hashes = self._hashes_de(servico, "dipirona", texto)

        resultado = servico.gerar_para_interacao(
            "dipirona", texto, hashes_existentes=hashes, reprocessar=True
        )

        self.assertEqual(resultado.status, StatusEmbedding.REPROCESSADO)
        self.assertEqual(len(provedor.chamadas), 1)

    def test_alteracao_do_texto_dispara_reprocessamento(self):
        provedor = ProvedorFalso()
        servico = criar_servico(provedor=provedor)
        hashes = self._hashes_de(servico, "dipirona", "Evitar com varfarina.")

        resultado = servico.gerar_para_interacao(
            "dipirona", "Evitar com varfarina e com heparina.", hashes_existentes=hashes
        )

        self.assertEqual(resultado.status, StatusEmbedding.REPROCESSADO)
        self.assertEqual(len(provedor.chamadas), 1)

    def test_mudanca_do_modelo_dispara_reprocessamento(self):
        texto = "Evitar o uso com varfarina."
        servico_antigo = criar_servico(provedor=ProvedorFalso(modelo="modelo/antigo"))
        hashes_antigos = self._hashes_de(servico_antigo, "dipirona", texto)
        servico_novo = criar_servico(provedor=ProvedorFalso(modelo="modelo/novo"))

        resultado = servico_novo.gerar_para_interacao(
            "dipirona", texto, hashes_existentes=hashes_antigos
        )

        self.assertEqual(resultado.status, StatusEmbedding.REPROCESSADO)

    def test_quantidade_diferente_de_chunks_dispara_reprocessamento(self):
        servico = criar_servico(config=configuracao(tamanho_chunk=10, sobreposicao_chunk=2))
        hashes_parciais = {
            0: calcular_hash_chunk("dipirona", 0, "qualquer", "modelo/fake-small")
        }

        resultado = servico.gerar_para_interacao(
            "dipirona", texto_com_palavras(40), hashes_existentes=hashes_parciais
        )

        self.assertEqual(resultado.status, StatusEmbedding.REPROCESSADO)

    def test_hash_do_documento_e_registrado(self):
        servico = criar_servico()

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(len(resultado.texto_hash), 64)


class TestFalhasEValidacoes(unittest.TestCase):
    def test_erro_de_inferencia_nao_devolve_chunks(self):
        provedor = ProvedorFalso(erro=ErroInferenciaError("modelo caiu"))
        servico = criar_servico(provedor=provedor)

        with self.assertLogs("conversao_interacoes_vetores.servico", level="ERROR"):
            resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.status, StatusEmbedding.ERRO_INFERENCIA)
        self.assertEqual(resultado.chunks, [])

    def test_limite_de_memoria_interrompe_antes_da_inferencia(self):
        provedor = ProvedorFalso()
        servico = criar_servico(provedor=provedor, medidor=MedidorEstourado)

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.status, StatusEmbedding.LIMITE_MEMORIA)
        self.assertEqual(provedor.chamadas, [])

    def test_dimensao_divergente_do_provedor_e_rejeitada(self):
        provedor = ProvedorFalso(dimensao=4, vetores=[[0.1, 0.2]])
        servico = criar_servico(provedor=provedor)

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.status, StatusEmbedding.DIMENSAO_INVALIDA)
        self.assertEqual(resultado.chunks, [])

    def test_vetor_com_nan_e_rejeitado(self):
        provedor = ProvedorFalso(
            dimensao=4, vetores=[[0.1, float("nan"), 0.3, 0.4]]
        )
        servico = criar_servico(provedor=provedor)

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.status, StatusEmbedding.DIMENSAO_INVALIDA)

    def test_quantidade_de_vetores_diferente_da_de_chunks_e_rejeitada(self):
        provedor = ProvedorFalso(dimensao=4, vetores=[[1.0, 0.0, 0.0, 0.0]])
        servico = criar_servico(
            provedor=provedor,
            config=configuracao(tamanho_chunk=10, sobreposicao_chunk=2),
        )

        resultado = servico.gerar_para_interacao("dipirona", texto_com_palavras(40))

        self.assertEqual(resultado.status, StatusEmbedding.DIMENSAO_INVALIDA)

    def test_erro_de_chunking_e_reportado(self):
        class ProvedorSemTokenizer(ProvedorFalso):
            def tokenizar(self, texto):
                raise RuntimeError("tokenizer fora do ar")

        servico = criar_servico(provedor=ProvedorSemTokenizer())

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.status, StatusEmbedding.ERRO_CHUNKING)


class TestMetricas(unittest.TestCase):
    def test_metricas_de_tempo_e_memoria_sao_preenchidas(self):
        servico = criar_servico()

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.memoria_antes_mb, 100.0)
        self.assertEqual(resultado.pico_memoria_mb, 120.0)
        self.assertEqual(resultado.memoria_depois_mb, 105.0)
        self.assertGreater(resultado.tempo_inferencia_segundos, 0)
        self.assertGreaterEqual(resultado.tempo_chunking_segundos, 0)

    def test_quantidade_de_tokens_e_somada_entre_os_chunks(self):
        servico = criar_servico(config=configuracao(tamanho_chunk=10, sobreposicao_chunk=0))

        resultado = servico.gerar_para_interacao("dipirona", texto_com_palavras(30))

        self.assertEqual(resultado.quantidade_tokens, 30)

    def test_modelo_e_dimensao_acompanham_o_provedor(self):
        servico = criar_servico(provedor=ProvedorFalso(dimensao=8, modelo="x/y"))

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.modelo, "x/y")
        self.assertEqual(resultado.dimensao, 8)


class TestConcorrencia(unittest.TestCase):
    def test_uma_inferencia_por_vez(self):
        simultaneos = []
        travas = threading.Lock()
        ativos = 0

        class ProvedorObservado(ProvedorFalso):
            def codificar(self, textos):
                nonlocal ativos
                with travas:
                    ativos += 1
                    simultaneos.append(ativos)
                resultado = super().codificar(textos)
                with travas:
                    ativos -= 1
                return resultado

        servico = criar_servico(provedor=ProvedorObservado())
        threads = [
            threading.Thread(
                target=servico.gerar_para_interacao,
                args=(f"medicamento {indice}", "Evitar varfarina."),
            )
            for indice in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(max(simultaneos), 1)

    def test_nenhum_nome_fica_marcado_como_em_andamento_ao_final(self):
        servico = criar_servico()

        servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertFalse(servico.esta_processando("dipirona"))

    def test_processamento_duplicado_do_mesmo_nome_e_bloqueado(self):
        servico = criar_servico()
        servico._em_andamento.add("dipirona")
        servico._bloqueio = threading.Lock()

        resultado = servico.gerar_para_interacao("dipirona", "Evitar varfarina.")

        self.assertEqual(resultado.status, StatusEmbedding.IGNORADO_JA_EXISTENTE)
        self.assertIn("em andamento", resultado.detalhe_erro)


if __name__ == "__main__":
    unittest.main()
