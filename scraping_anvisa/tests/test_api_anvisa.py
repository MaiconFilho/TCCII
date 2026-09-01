import base64
import json
import unittest
from datetime import datetime, timezone

from anvisa_scraper.api_anvisa import (
    baixar_pdf,
    consultar_mais_recente_por_nome,
    interpretar_data_atualizacao,
    montar_url_consulta,
)
from anvisa_scraper.modelos import BulaLocalizada, MedicamentoParaColeta


class NavegadorFalso:
    def __init__(self, respostas: list[dict]) -> None:
        self.respostas = iter(respostas)

    def execute_async_script(self, _script: str, _url: str) -> dict:
        return next(self.respostas)


def resposta_json(corpo: dict) -> dict:
    return {
        "status": 200,
        "contentType": "application/json",
        "body": json.dumps(corpo),
    }


class TestConsultaPorNome(unittest.TestCase):
    def test_url_contem_filtro_por_nome_e_pagina(self) -> None:
        url = montar_url_consulta("finasterida", 2)
        self.assertIn("filter%5BnomeProduto%5D=finasterida", url)
        self.assertIn("count=10", url)
        self.assertIn("page=2", url)

    def test_seleciona_bula_com_data_atualizacao_mais_recente(self) -> None:
        pagina_1 = {
            "totalPages": 2,
            "content": [
                {
                    "idProduto": 1,
                    "numeroRegistro": "111",
                    "nomeProduto": "FINASTERIDA",
                    "expediente": "100",
                    "dataAtualizacao": "01/03/2025",
                    "idBulaProfissionalProtegido": "antiga",
                },
                {
                    "idProduto": 9,
                    "numeroRegistro": "999",
                    "nomeProduto": "FINASTERIDA + OUTRO",
                    "expediente": "900",
                    "dataAtualizacao": "01/01/2026",
                    "idBulaProfissionalProtegido": "nao-exata",
                },
            ],
        }
        pagina_2 = {
            "totalPages": 2,
            "content": [
                {
                    "idProduto": 2,
                    "numeroRegistro": "222",
                    "nomeProduto": "Finasterida",
                    "expediente": "200",
                    "dataAtualizacao": "15/08/2026",
                    "idBulaProfissionalProtegido": "recente",
                }
            ],
        }
        navegador = NavegadorFalso(
            [resposta_json(pagina_1), resposta_json(pagina_2)]
        )
        medicamento = MedicamentoParaColeta(
            nome_produto="finasterida",
            nome_normalizado="finasterida",
            quantidade_registros_planilha=3,
        )

        bula = consultar_mais_recente_por_nome(navegador, medicamento)

        self.assertEqual(bula.id_bula_profissional, "recente")
        self.assertEqual(bula.numero_registro, "222")
        self.assertEqual(bula.data_atualizacao_original, "15/08/2026")

    def test_data_iso_e_convertida_para_utc(self) -> None:
        data = interpretar_data_atualizacao("2026-08-15T10:30:00-03:00")
        self.assertEqual(data, datetime(2026, 8, 15, 13, 30, tzinfo=timezone.utc))

    def test_valida_assinatura_pdf(self) -> None:
        conteudo = b"%PDF-1.7\nconteudo de teste"
        navegador = NavegadorFalso(
            [
                {
                    "status": 200,
                    "contentType": "application/force-download",
                    "base64": base64.b64encode(conteudo).decode("ascii"),
                }
            ]
        )
        bula = BulaLocalizada(
            nome_normalizado="finasterida",
            nome_produto="finasterida",
            numero_registro="123",
            expediente="456",
            id_bula_profissional="id-protegido",
            data_atualizacao=datetime(2026, 8, 15, tzinfo=timezone.utc),
            data_atualizacao_original="15/08/2026",
        )

        self.assertEqual(baixar_pdf(navegador, bula), conteudo)


if __name__ == "__main__":
    unittest.main()
