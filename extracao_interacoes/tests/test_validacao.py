import json
import unittest

from extracao_interacoes.erros import (
    RespostaInvalidaError,
    RespostaTruncadaError,
)
from extracao_interacoes.validacao import validar_resposta


def resposta_encontrada(titulo: str, trecho: str) -> str:
    return json.dumps(
        {
            "encontrado": True,
            "titulo_encontrado": titulo,
            "trecho_interacoes": trecho,
        },
        ensure_ascii=False,
    )


class TestValidacaoResposta(unittest.TestCase):
    def test_aceita_secao_com_numeracao_variavel_ou_sem_numero(self) -> None:
        titulos = [
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "5. INTERAÇÕES MEDICAMENTOSAS",
            "7. INTERAÇÕES COM OUTROS MEDICAMENTOS",
            "INTERAÇÕES MEDICAMENTOSAS",
            "Interações medicamentosas e outras formas de interação",
        ]
        for titulo in titulos:
            with self.subTest(titulo=titulo):
                trecho = (
                    f"{titulo}\nO uso concomitante pode alterar a resposta ao tratamento."
                )
                documento = f"APRESENTAÇÃO\nTexto inicial.\n\n{trecho}\n\n8. CUIDADOS"

                resultado = validar_resposta(
                    resposta_encontrada(titulo, trecho),
                    documento,
                )

                self.assertTrue(resultado.encontrado)
                self.assertEqual(resultado.titulo_encontrado, titulo)

    def test_ocorrencia_somente_no_sumario_pode_resultar_em_nao_encontrado(self) -> None:
        documento = "SUMÁRIO\n6. INTERAÇÕES MEDICAMENTOSAS ........ 12\nCONTEÚDO SEM ESSA SEÇÃO"
        resposta = '{"encontrado": false, "titulo_encontrado": null, "trecho_interacoes": null}'

        resultado = validar_resposta(resposta, documento)

        self.assertFalse(resultado.encontrado)

    def test_mencao_isolada_pode_resultar_em_nao_encontrado(self) -> None:
        documento = "ADVERTÊNCIAS\nConsulte interações medicamentosas antes do uso."
        resposta = '{"encontrado": false, "titulo_encontrado": null, "trecho_interacoes": null}'

        self.assertFalse(validar_resposta(resposta, documento).encontrado)

    def test_ausencia_da_secao_aceita_campos_nulos(self) -> None:
        resposta = '{"encontrado": false, "titulo_encontrado": null, "trecho_interacoes": null}'

        resultado = validar_resposta(resposta, "BULA SEM A SEÇÃO PROCURADA")

        self.assertIsNone(resultado.trecho_interacoes)

    def test_remove_cerca_markdown_externa(self) -> None:
        titulo = "INTERAÇÕES MEDICAMENTOSAS"
        trecho = f"{titulo}\nNão foram observadas interações clinicamente relevantes."
        conteudo = f"```json\n{resposta_encontrada(titulo, trecho)}\n```"

        resultado = validar_resposta(conteudo, trecho)

        self.assertEqual(resultado.trecho_interacoes, trecho)

    def test_rejeita_json_invalido(self) -> None:
        with self.assertRaisesRegex(RespostaInvalidaError, "JSON inválido"):
            validar_resposta("{encontrado: sim}", "texto")

    def test_encontrado_deve_ser_booleano(self) -> None:
        resposta = '{"encontrado": "true", "titulo_encontrado": null, "trecho_interacoes": null}'

        with self.assertRaisesRegex(RespostaInvalidaError, "Estrutura JSON"):
            validar_resposta(resposta, "texto")

    def test_rejeita_resumo_ou_conteudo_inventado(self) -> None:
        titulo = "5. INTERAÇÕES MEDICAMENTOSAS"
        documento = f"{titulo}\nO medicamento A reduz o efeito de B em uso concomitante."
        resumo = f"{titulo}\nHá várias interações importantes e o paciente deve ter cuidado."

        with self.assertRaisesRegex(RespostaInvalidaError, "continuamente"):
            validar_resposta(resposta_encontrada(titulo, resumo), documento)

    def test_rejeita_trecho_nao_contido_no_pdf(self) -> None:
        titulo = "INTERAÇÕES MEDICAMENTOSAS"
        trecho = f"{titulo}\nEsta frase não existe no documento original."

        with self.assertRaisesRegex(RespostaInvalidaError, "continuamente"):
            validar_resposta(resposta_encontrada(titulo, trecho), f"{titulo}\nOutro texto oficial completo.")

    def test_rejeita_resposta_truncada(self) -> None:
        with self.assertRaises(RespostaTruncadaError):
            validar_resposta("{}", "texto", resposta_truncada=True)

    def test_remove_marcadores_de_pagina_do_trecho_armazenado(self) -> None:
        titulo = "6. INTERAÇÕES MEDICAMENTOSAS"
        trecho_modelo = (
            f"{titulo}\nTexto antes da quebra.\n[[PÁGINA 2]]\nTexto depois da quebra."
        )
        documento = (
            f"[[PÁGINA 1]]\n{titulo}\nTexto antes da quebra.\n"
            "[[PÁGINA 2]]\nTexto depois da quebra."
        )

        resultado = validar_resposta(
            resposta_encontrada(titulo, trecho_modelo),
            documento,
        )

        self.assertNotIn("[[PÁGINA", resultado.trecho_interacoes or "")

    def test_rejeita_titulo_sem_relacao_com_interacoes(self) -> None:
        titulo = "6. ADVERTÊNCIAS"
        trecho = f"{titulo}\nEste é um conteúdo oficial suficientemente longo."

        with self.assertRaisesRegex(RespostaInvalidaError, "não se relaciona"):
            validar_resposta(resposta_encontrada(titulo, trecho), trecho)


if __name__ == "__main__":
    unittest.main()
