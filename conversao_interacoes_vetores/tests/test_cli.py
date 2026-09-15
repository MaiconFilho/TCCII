import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# A CLI fica na raiz do modulo, ao lado de tests/, em qualquer um dos layouts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main_embeddings as cli


class TestArgumentos(unittest.TestCase):
    def test_padrao_processa_apenas_um_registro(self):
        args = cli.criar_argumentos([])

        self.assertEqual(args.limite, 1)
        self.assertFalse(args.todos)
        self.assertFalse(args.reprocessar)
        self.assertIsNone(args.nome_normalizado)

    def test_limite_configuravel(self):
        self.assertEqual(cli.criar_argumentos(["--limite", "10"]).limite, 10)

    def test_todos_e_reconhecido(self):
        self.assertTrue(cli.criar_argumentos(["--todos"]).todos)

    def test_selecao_por_nome_normalizado(self):
        args = cli.criar_argumentos(["--nome-normalizado", "a saude da mulher"])

        self.assertEqual(args.nome_normalizado, "a saude da mulher")

    def test_reprocessar_com_nome(self):
        args = cli.criar_argumentos(
            ["--reprocessar", "--nome-normalizado", "a saude da mulher"]
        )

        self.assertTrue(args.reprocessar)
        self.assertEqual(args.nome_normalizado, "a saude da mulher")

    def test_opcoes_de_modelo_e_chunking(self):
        args = cli.criar_argumentos(
            [
                "--batch-size", "4",
                "--modelo", "outro/modelo",
                "--device", "cpu",
                "--chunk-size", "200",
                "--chunk-overlap", "20",
            ]
        )

        self.assertEqual(args.batch_size, 4)
        self.assertEqual(args.modelo, "outro/modelo")
        self.assertEqual(args.device, "cpu")
        self.assertEqual(args.chunk_size, 200)
        self.assertEqual(args.chunk_overlap, 20)

    def test_limite_zero_e_recusado(self):
        with self.assertRaises(SystemExit):
            cli.criar_argumentos(["--limite", "0"])

    def test_device_invalido_e_recusado(self):
        with self.assertRaises(SystemExit):
            cli.criar_argumentos(["--device", "tpu"])


class TestConfiguracaoDoAmbiente(unittest.TestCase):
    def test_valores_padrao_sao_conservadores(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            configuracao = cli.configuracao_do_ambiente()

        self.assertEqual(configuracao.modelo_id, "intfloat/multilingual-e5-small")
        self.assertEqual(configuracao.dispositivo, "cpu")
        self.assertEqual(configuracao.tamanho_lote, 8)
        self.assertEqual(configuracao.concorrencia, 1)
        self.assertTrue(configuracao.normalizar)
        configuracao.validar()

    def test_variaveis_do_env_sao_respeitadas(self):
        ambiente = {
            "EMBEDDING_MODEL_ID": "outro/modelo",
            "EMBEDDING_DEVICE": "cuda",
            "EMBEDDING_BATCH_SIZE": "4",
            "EMBEDDING_MAX_INPUT_TOKENS": "256",
            "EMBEDDING_NORMALIZE": "false",
            "EMBEDDING_CHUNK_SIZE": "200",
            "EMBEDDING_CHUNK_OVERLAP": "20",
            "EMBEDDING_THREADS": "2",
            "EMBEDDING_MAX_PROCESS_MEMORY_MB": "4000",
            "EMBEDDING_INFERENCE_CONCURRENCY": "1",
        }
        with mock.patch.dict(os.environ, ambiente, clear=True):
            configuracao = cli.configuracao_do_ambiente()

        self.assertEqual(configuracao.modelo_id, "outro/modelo")
        self.assertEqual(configuracao.dispositivo, "cuda")
        self.assertEqual(configuracao.tamanho_lote, 4)
        self.assertEqual(configuracao.max_tokens_entrada, 256)
        self.assertFalse(configuracao.normalizar)
        self.assertEqual(configuracao.tamanho_chunk, 200)
        self.assertEqual(configuracao.sobreposicao_chunk, 20)
        self.assertEqual(configuracao.threads, 2)
        self.assertEqual(configuracao.limite_memoria_mb, 4000)

    def test_prefixo_recebe_o_espaco_removido_pelo_dotenv(self):
        with mock.patch.dict(
            os.environ, {"EMBEDDING_PASSAGE_PREFIX": "passage:"}, clear=True
        ):
            self.assertEqual(cli.prefixo_do_ambiente(), "passage: ")

    def test_prefixo_vazio_e_preservado(self):
        with mock.patch.dict(
            os.environ, {"EMBEDDING_PASSAGE_PREFIX": ""}, clear=True
        ):
            self.assertEqual(cli.prefixo_do_ambiente(), "")

    def test_concorrencia_diferente_de_um_e_recusada(self):
        with mock.patch.dict(
            os.environ, {"EMBEDDING_INFERENCE_CONCURRENCY": "4"}, clear=True
        ):
            with self.assertRaises(ValueError):
                cli.configuracao_do_ambiente().validar()

    def test_valor_nao_numerico_gera_erro_claro(self):
        with mock.patch.dict(
            os.environ, {"EMBEDDING_BATCH_SIZE": "oito"}, clear=True
        ):
            with self.assertRaises(ValueError):
                cli.configuracao_do_ambiente()

    def test_normalize_invalido_gera_erro_claro(self):
        with mock.patch.dict(
            os.environ, {"EMBEDDING_NORMALIZE": "talvez"}, clear=True
        ):
            with self.assertRaises(ValueError):
                cli.configuracao_do_ambiente()


class TestAplicacaoDosArgumentos(unittest.TestCase):
    def test_argumentos_sobrescrevem_o_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            base = cli.configuracao_do_ambiente()
        args = cli.criar_argumentos(
            ["--modelo", "outro/modelo", "--batch-size", "2", "--chunk-size", "100"]
        )

        configuracao = cli.aplicar_argumentos(base, args)

        self.assertEqual(configuracao.modelo_id, "outro/modelo")
        self.assertEqual(configuracao.tamanho_lote, 2)
        self.assertEqual(configuracao.tamanho_chunk, 100)
        # O que não foi informado permanece como estava.
        self.assertEqual(configuracao.dispositivo, base.dispositivo)

    def test_sem_argumentos_a_configuracao_nao_muda(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            base = cli.configuracao_do_ambiente()

        self.assertIs(cli.aplicar_argumentos(base, cli.criar_argumentos([])), base)

    def test_sobreposicao_zero_e_aplicada(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            base = cli.configuracao_do_ambiente()

        configuracao = cli.aplicar_argumentos(
            base, cli.criar_argumentos(["--chunk-overlap", "0"])
        )

        self.assertEqual(configuracao.sobreposicao_chunk, 0)


if __name__ == "__main__":
    unittest.main()
