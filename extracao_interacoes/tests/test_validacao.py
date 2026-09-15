import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import (
    RespostaInvalidaError,
    RespostaTruncadaError,
    SecaoSomenteTituloError,
)
from extracao_interacoes.modelos import RespostaClassificacao
from extracao_interacoes.validacao import (
    analisar_json,
    copiar_e_validar_trecho,
    localizar_fallback_estrutural,
    normalizar_linha,
    titulo_relacionado_a_interacoes,
    validar_candidato,
)

from helpers import criar_documento, resposta_secao


class TestValidacao(unittest.TestCase):
    def setUp(self) -> None:
        self.documento = criar_documento(
            [
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "A administração concomitante pode alterar o efeito terapêutico.",
                "Consulte o profissional de saúde antes de associar medicamentos.",
                "6. ADVERTÊNCIAS",
                "Texto da próxima seção.",
            ]
        )

    def test_json_de_classificacao_valido(self) -> None:
        resposta = analisar_json(
            json.dumps(resposta_secao(), ensure_ascii=False),
            RespostaClassificacao,
        )
        self.assertEqual(resposta.linha_titulo, "L000001")

    def test_json_invalido_e_rejeitado(self) -> None:
        with self.assertRaises(RespostaInvalidaError):
            analisar_json("não é json", RespostaClassificacao)

    def test_json_com_campo_extra_e_rejeitado(self) -> None:
        dados = resposta_secao()
        dados["texto_inventado"] = "x"
        with self.assertRaises(RespostaInvalidaError):
            analisar_json(json.dumps(dados), RespostaClassificacao)

    def test_resposta_truncada_e_rejeitada(self) -> None:
        with self.assertRaises(RespostaTruncadaError):
            analisar_json("{}", RespostaClassificacao, resposta_truncada=True)

    def test_candidato_precisa_apontar_para_linha_da_janela(self) -> None:
        resposta = RespostaClassificacao.model_validate(resposta_secao())
        with self.assertRaises(RespostaInvalidaError):
            validar_candidato(resposta, self.documento, {"L000002"})

    def test_titulo_precisa_ser_literal_e_relacionado(self) -> None:
        resposta = RespostaClassificacao.model_validate(
            resposta_secao(titulo="5. OUTRO ASSUNTO")
        )
        with self.assertRaises(RespostaInvalidaError):
            validar_candidato(resposta, self.documento, {"L000001"})

    def test_copia_literal_preserva_acentos_e_quebras(self) -> None:
        trecho = copiar_e_validar_trecho(
            self.documento,
            "L000001",
            "L000004",
            "5. INTERAÇÕES MEDICAMENTOSAS",
            "6. ADVERTÊNCIAS",
        )
        self.assertEqual(trecho, "\n".join(l.texto_original for l in self.documento.linhas[:3]))
        self.assertNotIn("6. ADVERTÊNCIAS", trecho)

    def test_fim_antes_do_inicio_e_rejeitado(self) -> None:
        with self.assertRaises(RespostaInvalidaError):
            copiar_e_validar_trecho(
                self.documento, "L000003", "L000002", "interações"
            )

    def test_numero_omitido_pela_llm_e_preservado_na_copia_original(self) -> None:
        trecho = copiar_e_validar_trecho(
            self.documento, "L000001", "L000004", "INTERAÇÕES MEDICAMENTOSAS"
        )
        self.assertEqual(trecho, "\n".join(l.texto_original for l in self.documento.linhas[:3]))

    def test_limite_distante_nao_pode_incluir_outro_topico(self) -> None:
        documento = criar_documento([
            "5. INTERAÇÕES MEDICAMENTOSAS",
            "O uso concomitante deve ser comunicado ao profissional.",
            "6. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO",
            "Conservar em temperatura ambiente.",
        ])
        with self.assertRaises(RespostaInvalidaError):
            copiar_e_validar_trecho(
                documento, "L000001", "L000005", "INTERAÇÕES MEDICAMENTOSAS"
            )

    def test_secao_somente_titulo_e_rejeitada(self) -> None:
        documento = criar_documento(["INTERAÇÕES MEDICAMENTOSAS", "6. ADVERTÊNCIAS"])
        with self.assertRaises(SecaoSomenteTituloError):
            copiar_e_validar_trecho(
                documento,
                "L000001",
                "L000002",
                "INTERAÇÕES MEDICAMENTOSAS",
            )

    def test_corpo_formado_somente_por_outro_titulo_e_rejeitado(self) -> None:
        documento = criar_documento(
            [
                "INTERAÇÕES MEDICAMENTOSAS",
                "6. CUIDADOS DE ARMAZENAMENTO DO MEDICAMENTO",
            ]
        )
        with self.assertRaises(SecaoSomenteTituloError):
            copiar_e_validar_trecho(
                documento,
                "L000001",
                "L000003",
                "INTERAÇÕES MEDICAMENTOSAS",
            )

    def test_fallback_exige_titulo_explicito_e_proximo_titulo_inequivoco(self) -> None:
        candidatos = localizar_fallback_estrutural(self.documento)
        self.assertEqual(candidatos, [("L000001", "L000004", "5. INTERAÇÕES MEDICAMENTOSAS")])

    def test_fallback_nao_trata_maiusculas_arbitrarias_como_novo_titulo(self) -> None:
        documento = criar_documento(
            [
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "A SAÚDE DA MULHER® PODE SER AFETADA DURANTE O TRATAMENTO",
                "Esta frase continua e contém informação oficial relevante.",
                "6. ADVERTÊNCIAS",
            ]
        )
        candidato = localizar_fallback_estrutural(documento)[0]
        self.assertEqual(candidato[1], "L000004")

    def test_fallback_nao_extrai_entrada_de_sumario(self) -> None:
        documento = criar_documento(
            [
                "SUMÁRIO",
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "6. ADVERTÊNCIAS ................................ 12",
                "7. REAÇÕES ADVERSAS ............................ 18",
                "Texto posterior que não transforma a entrada em seção real.",
            ]
        )
        self.assertEqual(localizar_fallback_estrutural(documento), [])

    def test_normalizacao_nao_altera_texto_original(self) -> None:
        original = "  Interações\u00a0  medicamentosas  "
        self.assertEqual(normalizar_linha(original), "Interações medicamentosas")
        self.assertTrue(titulo_relacionado_a_interacoes(original))


if __name__ == "__main__":
    unittest.main()
