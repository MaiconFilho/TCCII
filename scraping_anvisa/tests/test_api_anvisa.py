import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from anvisa_scraper.api_anvisa import (
    INTERVALO_ENTRE_PAGINAS_SEGUNDOS,
    _validar_status,
    baixar_pdf,
    consultar_mais_recente_por_nome,
    interpretar_data_publicacao,
    montar_url_consulta,
)
from anvisa_scraper.erros import (
    BloqueioAnvisaError,
    LimiteRequisicoesAnvisaError,
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

    def test_seleciona_bula_com_data_de_publicacao_mais_recente(self) -> None:
        pagina_1 = {
            "totalPages": 2,
            "content": [
                {
                    "idProduto": 1,
                    "numeroRegistro": "111",
                    "nomeProduto": "FINASTERIDA",
                    "expediente": "100",
                    "data": "2024-03-01T09:00:00.000-0300",
                    "dataAtualizacao": "2026-09-01T00:00:00.000-0300",
                    "idBulaProfissionalProtegido": "antiga",
                },
                {
                    "idProduto": 9,
                    "numeroRegistro": "999",
                    "nomeProduto": "FINASTERIDA + OUTRO",
                    "expediente": "900",
                    "data": "2026-01-01T09:00:00.000-0300",
                    "dataAtualizacao": "2026-09-01T00:00:00.000-0300",
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
                    "data": "2025-08-15T10:30:00.000-0300",
                    "dataAtualizacao": "2026-09-01T00:00:00.000-0300",
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

        with patch("anvisa_scraper.api_anvisa.time.sleep") as esperar:
            bula = consultar_mais_recente_por_nome(navegador, medicamento)

        self.assertEqual(bula.id_bula_profissional, "recente")
        self.assertEqual(bula.numero_registro, "222")
        self.assertEqual(bula.data_publicacao.strftime("%d/%m/%Y"), "15/08/2025")
        esperar.assert_called_once_with(INTERVALO_ENTRE_PAGINAS_SEGUNDOS)

    def test_429_informa_tempo_de_espera_sem_virar_bloqueio_403(self) -> None:
        with self.assertRaises(LimiteRequisicoesAnvisaError) as contexto:
            _validar_status(
                {"status": 429, "retryAfter": "17"},
                "consulta de 'CLO' na página 16",
            )

        self.assertEqual(contexto.exception.retry_after_segundos, 17.0)
        self.assertIn("HTTP 429", str(contexto.exception))

    def test_403_continua_interrompendo_imediatamente(self) -> None:
        with self.assertRaises(BloqueioAnvisaError):
            _validar_status({"status": 403}, "consulta de 'CLO'")

    def test_data_iso_preserva_data_e_fuso_da_publicacao(self) -> None:
        data = interpretar_data_publicacao("2025-04-25T10:18:58.000-0300")
        self.assertEqual(
            data,
            datetime(
                2025,
                4,
                25,
                10,
                18,
                58,
                tzinfo=timezone(-timedelta(hours=3)),
            ),
        )

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
            data_publicacao=datetime(2025, 8, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(baixar_pdf(navegador, bula), conteudo)


if __name__ == "__main__":
    unittest.main()
