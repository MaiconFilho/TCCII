import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from extracao_interacoes.erros import (
    ErroInferenciaError,
    LimiteMemoriaError,
    PdfInvalidoError,
    PdfSemTextoError,
    RespostaInvalidaError,
)
from extracao_interacoes.modelos import (
    CandidatoSecao, MetodoExtracao, RespostaClassificacao,
    ResultadoExtracao, StatusExtracao,
)
from extracao_interacoes.segmentacao import criar_janelas
from extracao_interacoes.servico import ServicoExtracaoInteracoes

from helpers import (
    MedidorFalso,
    ProvedorFalso,
    configuracao,
    criar_documento,
    resposta_fim,
    resposta_nao_encontrado,
    resposta_secao,
)


class MedidorBloqueado(MedidorFalso):
    def executar(self, _funcao):
        raise LimiteMemoriaError("limite")


class TestServicoExtracaoInteracoes(unittest.TestCase):
    def criar_servico(self, documento, respostas, config=None, medidor=MedidorFalso):
        provedor = ProvedorFalso(respostas)
        servico = ServicoExtracaoInteracoes(
            provedor,
            config or configuracao(),
            leitor=lambda _caminho: documento,
            fabrica_medidor=medidor,
        )
        return servico, provedor

    def extrair(self, servico, nome="medicamento"):
        return servico.extrair(nome, "123", "456", Path("bula.pdf"))

    def test_a_saude_da_mulher_preserva_duas_frases_e_exclui_proxima_secao(self) -> None:
        documento = criar_documento(
            [
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "A SAÚDE DA MULHER® PODE SER AFETADA PELO USO CONCOMITANTE.",
                "A paciente deve comunicar todos os medicamentos ao profissional.",
                "6. ADVERTÊNCIAS E PRECAUÇÕES",
                "Texto que não pertence às interações.",
            ]
        )
        servico, provedor = self.criar_servico(
            documento,
            [resposta_secao(), resposta_fim("L000004", "6. ADVERTÊNCIAS E PRECAUÇÕES")],
        )

        resultado = self.extrair(servico, "a saude da mulher")

        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.metodo, MetodoExtracao.LLM)
        self.assertIn("A SAÚDE DA MULHER®", resultado.trecho_interacoes)
        self.assertIn("A paciente deve comunicar", resultado.trecho_interacoes)
        self.assertNotIn("6. ADVERTÊNCIAS", resultado.trecho_interacoes)
        self.assertEqual(len(provedor.esquemas), 2)
        self.assertIn("tipo_ocorrencia", provedor.esquemas[0]["anyOf"][0]["properties"])
        self.assertIn("encontrou_fim", provedor.esquemas[1]["anyOf"][0]["properties"])
        limite = provedor.esquemas[1]["anyOf"][0]["properties"]
        self.assertEqual(limite["linha_inicio"], {"const": "L000001"})
        self.assertEqual(limite["titulo_encontrado"], {"const": "5. INTERAÇÕES MEDICAMENTOSAS"})
        pares = {
            ramo["properties"]["linha_fim_exclusiva"]["const"]:
            ramo["properties"]["proximo_titulo"]["const"]
            for ramo in provedor.esquemas[1]["anyOf"]
            if "const" in ramo["properties"]["linha_fim_exclusiva"]
        }
        self.assertNotIn("L000001", pares)
        self.assertEqual(pares["L000004"], "6. ADVERTÊNCIAS E PRECAUÇÕES")
        self.assertIn("L000002", pares)  # Corpo tambem e opcao; nao ha filtro de titulos.

    def test_limite_nao_presume_fim_do_documento_sem_confirmacao(self) -> None:
        documento = criar_documento([
            "5. INTERAÇÕES MEDICAMENTOSAS",
            "O corpo da secao possui texto suficiente para a validacao.",
        ])
        continua = {
            "encontrou_fim": False,
            "linha_inicio": "L000001",
            "linha_fim_exclusiva": None,
            "titulo_encontrado": "5. INTERAÇÕES MEDICAMENTOSAS",
            "proximo_titulo": None,
            "fim_documento": False,
        }
        servico, provedor = self.criar_servico(documento, [continua])
        candidato = CandidatoSecao(RespostaClassificacao.model_validate(resposta_secao()), 0)
        janelas = criar_janelas(documento, provedor.contar_tokens)
        with self.assertRaises(RespostaInvalidaError):
            servico._localizar_fim(
                candidato, documento, janelas,
                ResultadoExtracao(status=StatusExtracao.RESPOSTA_INVALIDA),
                MedidorFalso(5500),
            )

    def test_secao_pode_ter_numero_5_6_outro_ou_nenhum(self) -> None:
        titulos = [
            "5. INTERAÇÕES MEDICAMENTOSAS",
            "6. INTERAÇÕES MEDICAMENTOSAS",
            "9. INTERAÇÕES MEDICAMENTOSAS",
            "INTERAÇÕES MEDICAMENTOSAS",
        ]
        for titulo in titulos:
            with self.subTest(titulo=titulo):
                documento = criar_documento(
                    [
                        titulo,
                        "O uso concomitante pode alterar o efeito do tratamento.",
                        "Consulte um profissional antes de combinar medicamentos.",
                        "10. ADVERTÊNCIAS",
                    ]
                )
                servico, _ = self.criar_servico(
                    documento,
                    [
                        resposta_secao(titulo=titulo),
                        resposta_fim(
                            "L000004",
                            "10. ADVERTÊNCIAS",
                            titulo_secao=titulo,
                        ),
                    ],
                )
                resultado = self.extrair(servico)
                self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
                self.assertTrue(resultado.trecho_interacoes.startswith(titulo))

    def test_sumario_mencao_e_continuacao_nao_viram_secao(self) -> None:
        documento = criar_documento(
            [
                "Texto geral da bula sem um título específico nesta janela.",
                "Há uma menção a interações dentro de outro assunto clínico.",
            ]
        )
        for tipo in ("SUMARIO", "MENCAO_ISOLADA", "CONTEUDO_CONTINUACAO"):
            with self.subTest(tipo=tipo):
                servico, _ = self.criar_servico(
                    documento,
                    [
                        {
                            "tipo_ocorrencia": tipo,
                            "linha_titulo": None,
                            "titulo_encontrado": None,
                            "confianca": "ALTA",
                        }
                    ],
                )
                resultado = self.extrair(servico)
                self.assertEqual(
                    resultado.status, StatusExtracao.SEM_SECAO_INTERACOES
                )

    def test_secao_atravessa_paginas_sem_perder_linhas(self) -> None:
        documento = criar_documento(
            [
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "Primeira frase oficial localizada no fim da primeira página.",
                "Segunda frase oficial localizada no começo da página seguinte.",
                "6. ADVERTÊNCIAS",
            ],
            paginas=[1, 1, 2, 2],
        )
        servico, _ = self.criar_servico(
            documento,
            [resposta_secao(), resposta_fim()],
        )
        resultado = self.extrair(servico)
        self.assertEqual(resultado.quantidade_paginas, 2)
        self.assertIn("fim da primeira página", resultado.trecho_interacoes)
        self.assertIn("começo da página seguinte", resultado.trecho_interacoes)

    def test_retentativa_de_json_marca_metodo(self) -> None:
        documento = criar_documento(
            [
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "O uso concomitante exige acompanhamento profissional cuidadoso.",
                "Outra frase oficial completa para validar o corpo da seção.",
                "6. ADVERTÊNCIAS",
            ]
        )
        servico, provedor = self.criar_servico(
            documento,
            ["inválido", resposta_secao(), resposta_fim()],
        )
        resultado = self.extrair(servico)

        self.assertEqual(resultado.metodo, MetodoExtracao.LLM_SEGUNDA_TENTATIVA)
        self.assertEqual(resultado.quantidade_chamadas_llm, 3)
        self.assertIn("resposta anterior", provedor.chamadas[1][-1]["content"].lower())

    def test_analisa_todas_as_janelas_antes_de_concluir_ausencia(self) -> None:
        documento = criar_documento([f"Linha clínica comum número {i}." for i in range(20)])
        config = configuracao(tokens_janela=18, sobreposicao_tokens=4)
        contador = ProvedorFalso([])
        quantidade = len(criar_janelas(documento, contador.contar_tokens, 18, 4))
        servico, provedor = self.criar_servico(
            documento,
            [resposta_nao_encontrado() for _ in range(quantidade)],
            config,
        )

        resultado = self.extrair(servico)

        self.assertEqual(resultado.status, StatusExtracao.SEM_SECAO_INTERACOES)
        self.assertEqual(len(provedor.chamadas), quantidade)
        self.assertEqual(resultado.quantidade_janelas, quantidade)

    def test_secao_pode_continuar_por_varias_janelas(self) -> None:
        textos = ["1. APRESENTAÇÃO"] + [f"texto inicial {i}" for i in range(3)]
        textos += ["5. INTERAÇÕES MEDICAMENTOSAS"]
        textos += [f"conteúdo oficial prolongado da seção número {i}" for i in range(15)]
        documento = criar_documento(textos)
        config = configuracao(tokens_janela=24, sobreposicao_tokens=4)
        contador = ProvedorFalso([])
        janelas = criar_janelas(documento, contador.contar_tokens, 24, 4)
        indice_titulo = next(
            janela.indice
            for janela in janelas
            if "L000005" in {linha.identificador for linha in janela.linhas}
        )
        classificacoes = []
        for janela in janelas:
            if janela.indice == indice_titulo:
                classificacoes.append(resposta_secao("L000005"))
            else:
                classificacoes.append(resposta_nao_encontrado())
        limites = [
            {
                "encontrou_fim": False,
                "linha_inicio": "L000005",
                "linha_fim_exclusiva": None,
                "titulo_encontrado": "5. INTERAÇÕES MEDICAMENTOSAS",
                "proximo_titulo": None,
                "fim_documento": False,
            }
            for _ in janelas[indice_titulo:-1]
        ]
        limites.append(
            {
                "encontrou_fim": True,
                "linha_inicio": "L000005",
                "linha_fim_exclusiva": None,
                "titulo_encontrado": "5. INTERAÇÕES MEDICAMENTOSAS",
                "proximo_titulo": None,
                "fim_documento": True,
            }
        )
        servico, _ = self.criar_servico(documento, classificacoes + limites, config)

        resultado = self.extrair(servico)

        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertIn("conteúdo oficial prolongado", resultado.trecho_interacoes)
        self.assertEqual(resultado.linha_fim_exclusiva, f"L{len(textos) + 1:06d}")

    def test_fallback_so_ocorre_depois_da_classificacao_llm(self) -> None:
        documento = criar_documento(
            [
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "Conteúdo oficial suficiente para validar a seção encontrada.",
                "Outro período completo sobre a administração concomitante.",
                "6. ADVERTÊNCIAS",
            ]
        )
        servico, provedor = self.criar_servico(documento, [resposta_nao_encontrado()])
        resultado = self.extrair(servico)

        self.assertEqual(len(provedor.chamadas), 1)
        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.metodo, MetodoExtracao.FALLBACK_ESTRUTURAL)
        self.assertTrue(resultado.avisos)

    def test_multiplos_candidatos_usam_desempate_curto(self) -> None:
        documento = criar_documento(
            [
                "5. INTERAÇÕES MEDICAMENTOSAS",
                "Primeiro conteúdo suficientemente longo para ser uma seção possível.",
                "6. ADVERTÊNCIAS",
                "7. INTERAÇÕES MEDICAMENTOSAS",
                "Segundo conteúdo oficial suficientemente longo para ser escolhido.",
                "Outra frase pertencente ao segundo conteúdo oficial da bula.",
                "8. CUIDADOS DE ARMAZENAMENTO",
            ]
        )
        config = configuracao(tokens_janela=10, sobreposicao_tokens=0)
        contador = ProvedorFalso([])
        janelas = criar_janelas(documento, contador.contar_tokens, 10, 0)
        classificacoes = []
        indice_escolhido = 0
        for janela in janelas:
            ids = {linha.identificador for linha in janela.linhas}
            if "L000001" in ids:
                classificacoes.append(resposta_secao("L000001", "5. INTERAÇÕES MEDICAMENTOSAS"))
            elif "L000004" in ids:
                indice_escolhido = janela.indice
                classificacoes.append(resposta_secao("L000004", "7. INTERAÇÕES MEDICAMENTOSAS"))
            else:
                classificacoes.append(resposta_nao_encontrado())
        desempate = {
            "linha_titulo_escolhida": "L000004",
            "justificativa_curta": "seção completa no corpo",
        }
        limites = []
        for janela in janelas[indice_escolhido:]:
            if "L000007" in {linha.identificador for linha in janela.linhas}:
                limites.append(
                    resposta_fim(
                        "L000007",
                        "8. CUIDADOS DE ARMAZENAMENTO",
                        inicio="L000004",
                        titulo_secao="7. INTERAÇÕES MEDICAMENTOSAS",
                    )
                )
                break
            limites.append(
                {
                    "encontrou_fim": False,
                    "linha_inicio": "L000004",
                    "linha_fim_exclusiva": None,
                    "titulo_encontrado": "7. INTERAÇÕES MEDICAMENTOSAS",
                    "proximo_titulo": None,
                    "fim_documento": False,
                }
            )
        servico, _ = self.criar_servico(
            documento, classificacoes + [desempate] + limites, config
        )

        resultado = self.extrair(servico)

        self.assertEqual(resultado.status, StatusExtracao.CONCLUIDO)
        self.assertEqual(resultado.linha_inicio, "L000004")
        self.assertNotIn("Primeiro conteúdo", resultado.trecho_interacoes)

    def test_duas_respostas_invalidas_sem_fallback_nao_produzem_trecho(self) -> None:
        documento = criar_documento(
            [
                "Medicamento e suas interações",
                "Texto oficial suficiente para um corpo, mas sem título estrutural estrito.",
            ]
        )
        servico, _ = self.criar_servico(documento, ["x", "y"])
        resultado = self.extrair(servico)
        self.assertEqual(resultado.status, StatusExtracao.RESPOSTA_INVALIDA)
        self.assertIsNone(resultado.trecho_interacoes)

    def test_secao_somente_titulo_recebe_status_especifico(self) -> None:
        documento = criar_documento(
            ["5. INTERAÇÕES MEDICAMENTOSAS", "6. ADVERTÊNCIAS E PRECAUÇÕES"]
        )
        servico, _ = self.criar_servico(
            documento,
            [resposta_secao(), resposta_fim("L000002", "6. ADVERTÊNCIAS E PRECAUÇÕES")],
        )
        resultado = self.extrair(servico)
        self.assertEqual(resultado.status, StatusExtracao.SECAO_SOMENTE_TITULO)

    def test_limite_de_memoria_interrompe_antes_da_chamada(self) -> None:
        documento = criar_documento(["Texto comum suficientemente longo para leitura."])
        servico, provedor = self.criar_servico(
            documento,
            [resposta_nao_encontrado()],
            medidor=MedidorBloqueado,
        )
        resultado = self.extrair(servico)
        self.assertEqual(resultado.status, StatusExtracao.LIMITE_MEMORIA)
        self.assertEqual(provedor.chamadas, [])

    def test_erro_inferencia_nao_executa_fallback(self) -> None:
        documento = criar_documento(["5. INTERAÇÕES MEDICAMENTOSAS", "Corpo oficial bastante longo para a validação."])
        servico, _ = self.criar_servico(documento, [ErroInferenciaError("falha")])
        resultado = self.extrair(servico)
        self.assertEqual(resultado.status, StatusExtracao.ERRO_INFERENCIA)
        self.assertIsNone(resultado.trecho_interacoes)

    def test_pdf_sem_texto_nao_chama_llm(self) -> None:
        provedor = ProvedorFalso([])
        servico = ServicoExtracaoInteracoes(
            provedor,
            configuracao(),
            leitor=lambda _p: (_ for _ in ()).throw(PdfSemTextoError("sem texto")),
            fabrica_medidor=MedidorFalso,
        )
        resultado = self.extrair(servico)
        self.assertEqual(resultado.status, StatusExtracao.PDF_SEM_TEXTO)
        self.assertEqual(provedor.chamadas, [])

    def test_pdf_invalido_nao_chama_llm(self) -> None:
        provedor = ProvedorFalso([])
        servico = ServicoExtracaoInteracoes(
            provedor,
            configuracao(),
            leitor=lambda _p: (_ for _ in ()).throw(PdfInvalidoError("inválido")),
            fabrica_medidor=MedidorFalso,
        )
        resultado = self.extrair(servico)
        self.assertEqual(resultado.status, StatusExtracao.PDF_INVALIDO)
        self.assertEqual(provedor.chamadas, [])

    def test_resposta_registra_tokens_chamadas_e_memoria(self) -> None:
        documento = criar_documento([f"Linha sem seção número {i}." for i in range(4)])
        servico, _ = self.criar_servico(documento, [resposta_nao_encontrado()])
        resultado = self.extrair(servico)
        self.assertEqual(resultado.quantidade_chamadas_llm, 1)
        self.assertEqual(resultado.tokens_entrada_total, 10)
        self.assertEqual(resultado.tokens_saida_total, 5)
        self.assertEqual(resultado.memoria_antes_mb, 100)
        self.assertEqual(resultado.pico_memoria_mb, 120)


if __name__ == "__main__":
    unittest.main()
