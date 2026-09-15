import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conversao_interacoes_vetores.erros import (
    ErroCarregamentoModeloError,
    ErroInferenciaError,
    ModeloInvalidoError,
    VetorInvalidoError,
)
from conversao_interacoes_vetores.modelo_embeddings import (
    ProvedorEmbeddingsTransformers,
    calcular_threads_seguros,
    validar_vetores,
)
from tests.dobros_torch import ModeloFalso, TokenizerFalso, TorchFalso
from tests.helpers import configuracao


class Contador:
    """Conta quantas vezes uma fábrica de modelo/tokenizer foi chamada."""

    def __init__(self, retorno) -> None:
        self.retorno = retorno
        self.chamadas = 0
        self.argumentos: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.chamadas += 1
        self.argumentos.append((args, kwargs))
        return self.retorno


def carregar(config=None, modelo=None, tokenizer=None, torch=None, cache=None):
    torch = torch or TorchFalso()
    fabrica_tokenizer = Contador(tokenizer or TokenizerFalso())
    fabrica_modelo = Contador(modelo or ModeloFalso(hidden_size=4))
    provedor = ProvedorEmbeddingsTransformers.carregar(
        config or configuracao(),
        diretorio_cache=cache,
        fabrica_tokenizer=fabrica_tokenizer,
        fabrica_modelo=fabrica_modelo,
        torch_modulo=torch,
    )
    return provedor, fabrica_tokenizer, fabrica_modelo, torch


class TestCarregamentoUnico(unittest.TestCase):
    def test_o_modelo_e_carregado_uma_unica_vez(self):
        provedor, fabrica_tokenizer, fabrica_modelo, _ = carregar()

        provedor.codificar(["um texto", "outro texto"])
        provedor.codificar(["mais um texto"])

        self.assertEqual(fabrica_modelo.chamadas, 1)
        self.assertEqual(fabrica_tokenizer.chamadas, 1)

    def test_nenhum_modelo_novo_por_registro(self):
        modelo = ModeloFalso(hidden_size=4)
        provedor, _, fabrica_modelo, _ = carregar(modelo=modelo)

        for _ in range(5):
            provedor.codificar(["texto"])

        self.assertEqual(fabrica_modelo.chamadas, 1)
        self.assertEqual(modelo.chamadas, 5)

    def test_modelo_fica_em_modo_avaliacao_e_no_dispositivo_configurado(self):
        modelo = ModeloFalso(hidden_size=4)
        carregar(modelo=modelo)

        self.assertTrue(modelo.em_avaliacao)
        self.assertEqual(modelo.dispositivo, "cpu")

    def test_threads_sao_fixadas_no_torch(self):
        _, _, _, torch = carregar(config=configuracao(threads=3))

        self.assertEqual(torch.threads, 3)

    def test_threads_zero_usa_heuristica_segura(self):
        self.assertEqual(calcular_threads_seguros(0, cpus=8), 4)
        self.assertEqual(calcular_threads_seguros(0, cpus=2), 1)
        self.assertEqual(calcular_threads_seguros(6, cpus=2), 6)

    def test_configuracao_invalida_impede_o_carregamento(self):
        with self.assertRaises(ModeloInvalidoError):
            carregar(config=configuracao(concorrencia=4))

    def test_falha_da_fabrica_vira_erro_de_carregamento(self):
        def quebrada(*_args, **_kwargs):
            raise OSError("sem conexão")

        with self.assertLogs(
            "conversao_interacoes_vetores.modelo_embeddings", level="ERROR"
        ), self.assertRaises(ErroCarregamentoModeloError):
            ProvedorEmbeddingsTransformers.carregar(
                configuracao(),
                fabrica_tokenizer=quebrada,
                fabrica_modelo=quebrada,
                torch_modulo=TorchFalso(),
            )

    def test_cache_local_e_repassado_as_fabricas(self):
        _, fabrica_tokenizer, _, _ = carregar(cache=Path("modelos_hf"))

        _, kwargs = fabrica_tokenizer.argumentos[0]
        self.assertIn("cache_dir", kwargs)


class TestDimensaoELimites(unittest.TestCase):
    def test_dimensao_vem_do_hidden_size_do_modelo(self):
        provedor, _, _, _ = carregar(modelo=ModeloFalso(hidden_size=384))

        self.assertEqual(provedor.dimensao, 384)

    def test_modelo_sem_hidden_size_e_recusado(self):
        modelo = ModeloFalso(hidden_size=4)
        modelo.config.hidden_size = 0

        with self.assertRaises(ModeloInvalidoError):
            carregar(modelo=modelo)

    def test_limite_de_tokens_desconta_especiais_e_prefixo(self):
        provedor, _, _, _ = carregar(
            config=configuracao(max_tokens_entrada=512, prefixo_passagem="passage: ")
        )

        # 512 - 2 tokens especiais - 1 token do prefixo ("passage:").
        self.assertEqual(provedor.limite_tokens, 509)

    def test_limite_respeita_a_configuracao_mais_restritiva(self):
        provedor, _, _, _ = carregar(
            config=configuracao(max_tokens_entrada=128, tamanho_chunk=100)
        )

        self.assertLessEqual(provedor.limite_tokens, 128)


class TestInferenciaEmLotes(unittest.TestCase):
    def test_processa_em_lotes_do_tamanho_configurado(self):
        modelo = ModeloFalso(hidden_size=4)
        provedor, _, _, _ = carregar(
            config=configuracao(tamanho_lote=2), modelo=modelo
        )

        vetores = provedor.codificar(["a b", "c d", "e f", "g h", "i j"])

        self.assertEqual(len(vetores), 5)
        self.assertEqual(modelo.chamadas, 3)  # 2 + 2 + 1

    def test_lista_vazia_nao_chama_o_modelo(self):
        modelo = ModeloFalso(hidden_size=4)
        provedor, _, _, _ = carregar(modelo=modelo)

        self.assertEqual(provedor.codificar([]), [])
        self.assertEqual(modelo.chamadas, 0)

    def test_prefixo_de_passagem_e_aplicado_na_entrada(self):
        tokenizer = TokenizerFalso()
        provedor, _, _, _ = carregar(tokenizer=tokenizer)

        provedor.codificar(["varfarina"])

        self.assertTrue(tokenizer.chamadas[0][0].startswith("passage: "))

    def test_sem_prefixo_o_texto_segue_intacto(self):
        tokenizer = TokenizerFalso()
        provedor, _, _, _ = carregar(
            config=configuracao(prefixo_passagem=""), tokenizer=tokenizer
        )

        provedor.codificar(["varfarina"])

        self.assertEqual(tokenizer.chamadas[0][0], "varfarina")

    def test_ordem_dos_vetores_acompanha_a_ordem_dos_textos(self):
        modelo = ModeloFalso(hidden_size=4)
        provedor, _, _, _ = carregar(
            config=configuracao(
                tamanho_lote=1, normalizar=False, prefixo_passagem=""
            ),
            modelo=modelo,
        )

        vetores = provedor.codificar(["aa", "bbbb"])

        self.assertNotEqual(vetores[0], vetores[1])
        self.assertEqual(vetores[0][0], 2.0)
        self.assertEqual(vetores[1][0], 4.0)

    def test_falha_do_modelo_vira_erro_de_inferencia(self):
        class ModeloQuebrado(ModeloFalso):
            def __call__(self, **_entradas):
                raise RuntimeError("falha de inferência")

        provedor, _, _, _ = carregar(modelo=ModeloQuebrado(hidden_size=4))

        with self.assertLogs(
            "conversao_interacoes_vetores.modelo_embeddings", level="ERROR"
        ):
            with self.assertRaises(ErroInferenciaError):
                provedor.codificar(["texto"])

    def test_smoke_test_devolve_um_vetor(self):
        provedor, _, _, _ = carregar()

        provedor.testar()  # não deve levantar exceção


class TestNormalizacao(unittest.TestCase):
    def test_normalizacao_produz_norma_unitaria(self):
        provedor, _, _, _ = carregar(config=configuracao(normalizar=True))

        vetor = provedor.codificar(["aaa bbb"])[0]
        norma = math.sqrt(sum(valor * valor for valor in vetor))

        self.assertAlmostEqual(norma, 1.0, places=6)

    def test_sem_normalizacao_a_norma_nao_e_unitaria(self):
        provedor, _, _, _ = carregar(config=configuracao(normalizar=False))

        vetor = provedor.codificar(["aaa bbb"])[0]
        norma = math.sqrt(sum(valor * valor for valor in vetor))

        self.assertNotAlmostEqual(norma, 1.0, places=3)

    def test_tokens_de_padding_nao_entram_na_media(self):
        provedor, _, _, _ = carregar(config=configuracao(normalizar=False))

        isolado = provedor.codificar(["aa"])[0]
        com_vizinho_maior = provedor.codificar(["aa", "bbbbbb"])[0]

        self.assertEqual(isolado, com_vizinho_maior)


class TestValidacaoDeVetores(unittest.TestCase):
    def test_dimensao_incorreta_e_rejeitada(self):
        with self.assertRaises(VetorInvalidoError):
            validar_vetores([[0.1, 0.2, 0.3]], dimensao=4)

    def test_nan_e_rejeitado(self):
        with self.assertRaises(VetorInvalidoError):
            validar_vetores([[0.1, float("nan"), 0.3, 0.4]], dimensao=4)

    def test_infinito_e_rejeitado(self):
        with self.assertRaises(VetorInvalidoError):
            validar_vetores([[0.1, float("inf"), 0.3, 0.4]], dimensao=4)

    def test_vetor_totalmente_nulo_e_rejeitado(self):
        with self.assertRaises(VetorInvalidoError):
            validar_vetores([[0.0, 0.0, 0.0, 0.0]], dimensao=4)

    def test_vetor_valido_passa(self):
        validar_vetores([[0.1, 0.2, 0.3, 0.4]], dimensao=4)


if __name__ == "__main__":
    unittest.main()
