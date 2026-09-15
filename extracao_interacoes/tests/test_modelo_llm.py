import threading
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import (
    ErroCarregamentoModeloError,
    ErroInferenciaError,
)
from extracao_interacoes.modelo_llm import (
    LlamaCppProvider,
    calcular_threads_seguros,
)

from helpers import configuracao
from extracao_interacoes.esquemas_llm import esquema_para_geracao
from extracao_interacoes.modelos import RespostaClassificacao, RespostaLimite, RespostaConfirmacao


class ModeloFalso:
    def __init__(self, conteudo='{"ok": true}', finish_reason="stop") -> None:
        self.conteudo = conteudo
        self.finish_reason = finish_reason
        self.kwargs = None

    def tokenize(self, dados, add_bos=False):
        return list(dados.split())

    def create_chat_completion(self, **kwargs):
        self.kwargs = kwargs
        return {
            "choices": [
                {
                    "message": {"content": self.conteudo},
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }


class ModeloConcorrencia(ModeloFalso):
    def __init__(self):
        super().__init__()
        self.ativas = 0
        self.maximo = 0
        self.trava = threading.Lock()

    def create_chat_completion(self, **kwargs):
        with self.trava:
            self.ativas += 1
            self.maximo = max(self.maximo, self.ativas)
        time.sleep(0.03)
        try:
            return super().create_chat_completion(**kwargs)
        finally:
            with self.trava:
                self.ativas -= 1


class TestLlamaCppProvider(unittest.TestCase):
    def test_confirmacao_usa_resposta_curta_sem_mudar_outras_etapas(self):
        modelo = ModeloFalso('{"confirmado": true}')
        provedor = LlamaCppProvider(configuracao(), modelo)
        provedor.analisar(
            [{"role": "user", "content": "teste"}],
            esquema=esquema_para_geracao(RespostaConfirmacao),
        )
        self.assertEqual(modelo.kwargs["max_tokens"], 32)

    def test_carrega_arquivo_oficial_q4_k_m_uma_vez(self) -> None:
        downloader = Mock(return_value="C:/cache/modelo.gguf")
        fabrica = Mock(return_value=ModeloFalso())
        config = configuracao()

        provedor = LlamaCppProvider.carregar(
            config, downloader=downloader, fabrica_modelo=fabrica
        )

        downloader.assert_called_once_with(
            repo_id="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
            filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
        )
        self.assertIs(provedor.modelo, fabrica.return_value)

    def test_configuracao_prioriza_cpu_mmap_e_sem_mlock(self) -> None:
        fabrica = Mock(return_value=ModeloFalso())
        LlamaCppProvider.carregar(
            configuracao(),
            downloader=Mock(return_value="modelo.gguf"),
            fabrica_modelo=fabrica,
        )
        kwargs = fabrica.call_args.kwargs
        self.assertEqual(kwargs["n_gpu_layers"], 0)
        self.assertTrue(kwargs["use_mmap"])
        self.assertFalse(kwargs["use_mlock"])
        self.assertEqual(kwargs["n_ctx"], 4096)

    def test_threads_zero_escolhe_valor_conservador(self) -> None:
        self.assertEqual(calcular_threads_seguros(0, cpus=16), 4)
        self.assertEqual(calcular_threads_seguros(0, cpus=2), 1)
        self.assertEqual(calcular_threads_seguros(3, cpus=16), 3)

    def test_inferencia_retorna_json_tokens_e_truncamento(self) -> None:
        modelo = ModeloFalso('{"tipo_ocorrencia":"NAO_ENCONTRADO"}', "length")
        provedor = LlamaCppProvider(configuracao(), modelo)

        resposta = provedor.analisar([{"role": "user", "content": "teste"}])

        self.assertEqual(resposta.tokens_entrada, 12)
        self.assertEqual(resposta.tokens_saida, 4)
        self.assertTrue(resposta.truncada)
        self.assertEqual(modelo.kwargs["temperature"], 0)
        self.assertEqual(modelo.kwargs["max_tokens"], 256)

    def test_contagem_de_tokens_usa_tokenizador_do_gguf(self) -> None:
        provedor = LlamaCppProvider(configuracao(), ModeloFalso())
        self.assertEqual(provedor.contar_tokens("um dois três"), 3)

    def test_inferencia_envia_schema_condicional_ao_backend(self) -> None:
        modelo = ModeloFalso()
        provedor = LlamaCppProvider(configuracao(), modelo)
        esquema = esquema_para_geracao(RespostaClassificacao)
        provedor.analisar([{"role": "user", "content": "teste"}], esquema=esquema)
        self.assertEqual(
            modelo.kwargs["response_format"],
            {"type": "json_object", "schema": esquema},
        )

    def test_geracao_de_continuacao_exige_campos_nulos(self) -> None:
        esquema = esquema_para_geracao(RespostaClassificacao)
        secao, outros = esquema["anyOf"]
        self.assertEqual(secao["properties"]["tipo_ocorrencia"], {"const": "SECAO_CORPO"})
        self.assertEqual(secao["properties"]["titulo_encontrado"]["minLength"], 1)
        self.assertNotIn("SECAO_CORPO", outros["properties"]["tipo_ocorrencia"]["enum"])
        for campo in ("linha_titulo", "titulo_encontrado"):
            self.assertIn(campo, outros["required"])
            self.assertEqual(outros["properties"][campo], {"type": "null"})
        self.assertFalse(outros["additionalProperties"])

    def test_geracao_distingue_proxima_secao_continuacao_e_fim_documento(self) -> None:
        proxima, continua, fim = esquema_para_geracao(RespostaLimite)["anyOf"]
        self.assertEqual(proxima["properties"]["proximo_titulo"]["minLength"], 1)
        self.assertEqual(continua["properties"]["encontrou_fim"], {"const": False})
        self.assertEqual(fim["properties"]["fim_documento"], {"const": True})
        for ramo in (continua, fim):
            self.assertEqual(ramo["properties"]["linha_fim_exclusiva"], {"type": "null"})

    def test_smoke_test_valida_resposta(self) -> None:
        provedor = LlamaCppProvider(configuracao(), ModeloFalso('{"ok": true}'))
        provedor.testar()

    def test_smoke_test_invalido_falha_antes_dos_pdfs(self) -> None:
        provedor = LlamaCppProvider(configuracao(), ModeloFalso("inválido"))
        with self.assertRaises(ErroInferenciaError):
            provedor.testar()

    def test_erro_download_preserva_encadeamento(self) -> None:
        def falhar(**_kwargs):
            raise OSError("sem rede")

        with self.assertRaises(ErroCarregamentoModeloError) as contexto:
            LlamaCppProvider.carregar(
                configuracao(), downloader=falhar, fabrica_modelo=Mock()
            )
        self.assertIsInstance(contexto.exception.__cause__, OSError)

    def test_backend_invalido_falha_no_carregamento(self) -> None:
        with self.assertRaises(ErroCarregamentoModeloError):
            LlamaCppProvider.carregar(
                configuracao(backend="transformers"),
                downloader=Mock(),
                fabrica_modelo=Mock(),
            )

    def test_provedor_serializa_inferencias_simultaneas(self) -> None:
        modelo = ModeloConcorrencia()
        provedor = LlamaCppProvider(configuracao(concorrencia=1), modelo)
        mensagens = [{"role": "user", "content": "teste"}]
        threads = [
            threading.Thread(target=provedor.analisar, args=(mensagens,))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(modelo.maximo, 1)


if __name__ == "__main__":
    unittest.main()
