import unittest
from contextlib import nullcontext

from extracao_interacoes.erros import ContextoExcedidoError
from extracao_interacoes.modelo_llm import ModeloLLM, montar_mensagens
from extracao_interacoes.modelos import ConfiguracaoModelo


class TokenizerFalso:
    pad_token_id = None
    eos_token_id = 2

    def __init__(self, tokens_entrada: int, resposta: str) -> None:
        self.tokens_entrada = tokens_entrada
        self.resposta = resposta
        self.mensagens = None

    def apply_chat_template(self, mensagens, **_kwargs):
        self.mensagens = mensagens
        return [[0] * self.tokens_entrada]

    def decode(self, _tokens, **_kwargs) -> str:
        return self.resposta


class ModeloFalso:
    device = "cpu"

    def __init__(self, tokens_saida: list[int]) -> None:
        self.tokens_saida = tokens_saida
        self.chamadas = []

    def generate(self, input_ids, **kwargs):
        self.chamadas.append(kwargs)
        return [input_ids[0] + self.tokens_saida]


class TorchFalso:
    @staticmethod
    def inference_mode():
        return nullcontext()


class TestModeloLLM(unittest.TestCase):
    def test_contexto_considera_prompt_documento_e_reserva_da_resposta(self) -> None:
        configuracao = ConfiguracaoModelo("modelo", 100, 30)
        tokenizer = TokenizerFalso(71, "{}")
        modelo = ModeloFalso([2])
        cliente = ModeloLLM(configuracao, tokenizer, modelo, TorchFalso())

        with self.assertRaises(ContextoExcedidoError) as contexto:
            cliente.gerar("[[PÁGINA 1]]\ntexto completo")

        self.assertEqual(contexto.exception.tokens_entrada, 71)
        self.assertEqual(modelo.chamadas, [])

    def test_geracao_deterministica_envia_documento_completo(self) -> None:
        configuracao = ConfiguracaoModelo("modelo", 100, 10)
        tokenizer = TokenizerFalso(20, '{"encontrado": false}')
        modelo = ModeloFalso([9, 2])
        cliente = ModeloLLM(configuracao, tokenizer, modelo, TorchFalso())
        documento = "[[PÁGINA 1]]\nINÍCIO\n[[PÁGINA 2]]\nFINAL"

        resultado = cliente.gerar(documento)

        self.assertIn(documento, tokenizer.mensagens[1]["content"])
        self.assertFalse(modelo.chamadas[0]["do_sample"])
        self.assertEqual(modelo.chamadas[0]["max_new_tokens"], 10)
        self.assertFalse(resultado.truncada)

    def test_detecta_resposta_que_atinge_limite_sem_eos(self) -> None:
        configuracao = ConfiguracaoModelo("modelo", 100, 3)
        cliente = ModeloLLM(
            configuracao,
            TokenizerFalso(20, "resposta parcial"),
            ModeloFalso([7, 8, 9]),
            TorchFalso(),
        )

        resultado = cliente.gerar("documento")

        self.assertTrue(resultado.truncada)

    def test_segunda_tentativa_informa_erro_sem_remover_documento(self) -> None:
        documento = "[[PÁGINA 1]]\nTEXTO INTEGRAL"

        mensagens = montar_mensagens(documento, "O trecho não está no PDF.")

        self.assertIn("O trecho não está no PDF.", mensagens[1]["content"])
        self.assertIn(documento, mensagens[1]["content"])


if __name__ == "__main__":
    unittest.main()
