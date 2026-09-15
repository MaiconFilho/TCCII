import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from extracao_interacoes.erros import OCRIndisponivelError, ErroOCRError, PdfComAnexosError
from extracao_interacoes.ocr import ConfiguracaoOCR, LeitorOCR, motivo_ocr, texto_ordenado_ocr
from extracao_interacoes.leitor_pdf import ler_pdf
from extracao_interacoes.modelos import StatusExtracao
from extracao_interacoes.servico import ServicoExtracaoInteracoes
from extracao_interacoes.pipeline import _linha_relatorio
from helpers import criar_documento, configuracao, ProvedorFalso, MedidorFalso
from test_pipeline import bula


def pagina(imagens=(), vetores=0, palavras=()):
    p = Mock()
    p.number = 0
    p.rect = pymupdf.Rect(0, 0, 600, 800)
    p.get_image_info.return_value = [{"bbox": bbox} for bbox in imagens]
    p.get_cdrawings.return_value = [None] * vetores
    p.get_text.return_value = palavras
    return p


class TestOCR(unittest.TestCase):
    def test_logo_pequeno_nao_dispara_ocr(self):
        p = pagina([(0, 0, 50, 30)])
        self.assertIsNone(motivo_ocr(p, "Texto nativo suficiente da bula. " * 10))
        p.get_cdrawings.assert_not_called()

    def test_imagem_de_pagina_com_rodape_nativo_precisa_ocr(self):
        p = pagina([(0, 0, 600, 800)])
        self.assertEqual(motivo_ocr(p, "Bula profissional pagina 1"), "imagem_sem_texto_suficiente")

    def test_imagem_com_camada_ocr_preexistente_nao_repete(self):
        texto = "Texto nativo suficiente da bula. " * 20
        p = pagina([(0, 0, 600, 800)], palavras=[(0, 0, 500, 700, texto)])
        self.assertIsNone(motivo_ocr(p, texto))

    def test_imagem_parcial_sem_texto_na_regiao_precisa_ocr(self):
        p = pagina([(0, 300, 600, 700)], palavras=[(0, 0, 500, 100, "texto fora da imagem")])
        self.assertEqual(motivo_ocr(p, "Texto nativo suficiente. " * 20), "imagem_parcial_sem_texto")

    def test_vetores_sem_texto_precisam_ocr(self):
        self.assertEqual(motivo_ocr(pagina(vetores=100), ""), "texto_vetorial_sem_camada_textual")

    def test_pagina_vazia_ou_moldura_nao_precisa_ocr(self):
        self.assertIsNone(motivo_ocr(pagina(), ""))
        self.assertIsNone(motivo_ocr(pagina(vetores=1), ""))

    def test_caracteres_corrompidos_precisam_ocr(self):
        self.assertEqual(motivo_ocr(pagina(), "\ufffd" * 30), "texto_corrompido")

    def test_cache_reutiliza_texto_e_invalida_quando_pdf_muda(self):
        with tempfile.TemporaryDirectory() as pasta:
            base = Path(pasta)
            pdf = base / "teste.pdf"
            pdf.write_bytes(b"pdf-v1")
            (base / "por.traineddata").write_bytes(b"modelo-falso")
            cfg = ConfiguracaoOCR(tessdata=base, cache=base / "cache")
            p = pagina()
            p.get_text.side_effect = lambda formato, **_: (
                {"blocks": []} if formato == "dict"
                else "Texto extraido por OCR com corpo suficiente."
            )
            primeiro = LeitorOCR(pdf, cfg)
            texto = primeiro.ler_pagina(p, "texto_vetorial_sem_camada_textual")
            segundo = LeitorOCR(pdf, cfg)
            self.assertEqual(segundo.ler_pagina(p, "texto_vetorial_sem_camada_textual"), texto)
            self.assertEqual(segundo.acertos_cache, 1)
            self.assertEqual(p.get_textpage_ocr.call_count, 1)
            pdf.write_bytes(b"pdf-v2")
            LeitorOCR(pdf, cfg).ler_pagina(p, "texto_vetorial_sem_camada_textual")
            self.assertEqual(p.get_textpage_ocr.call_count, 2)
            self.assertTrue(p.get_textpage_ocr.call_args.kwargs["full"])

    def test_modelo_ausente_e_ocr_desativado_nao_viram_sem_secao(self):
        with tempfile.TemporaryDirectory() as pasta:
            for ativo in (True, False):
                with self.subTest(ativo=ativo):
                    cfg = ConfiguracaoOCR(habilitado=ativo, tessdata=Path(pasta), cache=None)
                    with self.assertRaises(OCRIndisponivelError):
                        LeitorOCR(Path("bula.pdf"), cfg).ler_pagina(pagina(), "imagem")

    def test_ocr_vazio_falha_e_nao_cria_cache(self):
        with tempfile.TemporaryDirectory() as pasta:
            base = Path(pasta)
            (base / "por.traineddata").write_bytes(b"modelo")
            p = pagina()
            p.get_text.side_effect = lambda formato, **_: {"blocks": []} if formato == "dict" else ""
            with self.assertRaises(ErroOCRError):
                LeitorOCR(Path("teste.pdf"), ConfiguracaoOCR(tessdata=base, cache=None)).ler_pagina(p, "imagem")

    def test_documento_misto_preserva_texto_nativo_e_ids(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "misto.pdf"
            with pymupdf.open() as d:
                d.new_page().insert_text((72, 72), "Texto nativo da primeira pagina suficiente para leitura.")
                d.new_page()
                d.save(caminho)
            with patch("extracao_interacoes.leitor_pdf.motivo_ocr", side_effect=[None, "imagem"]), patch(
                "extracao_interacoes.leitor_pdf.LeitorOCR.ler_pagina",
                return_value="INTERACOES MEDICAMENTOSAS\nTexto reconhecido na imagem.",
            ):
                doc = ler_pdf(caminho)
            self.assertEqual([l.numero for l in doc.linhas], [1, 2, 3])
            self.assertEqual([l.pagina for l in doc.linhas], [1, 2, 2])
            self.assertTrue(doc.linhas[0].texto_original.startswith("Texto nativo"))

    def test_ocr_duas_colunas_nao_mistura_interacoes_e_dizeres_legais(self):
        p = pagina()
        linhas = []
        for indice in range(6):
            for x, prefixo in ((10, "esquerda"), (320, "direita")):
                linhas.append({"bbox": (x, 50 + indice * 15, x + 250, 60 + indice * 15),
                               "spans": [{"text": f"{prefixo}{indice} texto normal com cinco palavras"}]})
        p.get_text.return_value = {"blocks": [{"type": 0, "lines": linhas}]}
        texto = texto_ordenado_ocr(p, Mock())
        self.assertEqual(texto.splitlines(),
                         [f"esquerda{i} texto normal com cinco palavras" for i in range(6)] +
                         [f"direita{i} texto normal com cinco palavras" for i in range(6)])

    def test_ocr_tres_colunas_nao_intercala_o_texto(self):
        p = pagina()
        linhas = []
        for x, prefixo in ((10, "esquerda"), (210, "centro"), (410, "direita")):
            for indice in range(6):
                linhas.append({
                    "bbox": (x, 50 + indice * 15, x + 175, 60 + indice * 15),
                    "spans": [{"text": f"{prefixo}{indice} texto normal com cinco palavras"}],
                })
        p.get_text.return_value = {"blocks": [{"type": 0, "lines": linhas}]}
        texto = texto_ordenado_ocr(p, Mock())
        self.assertEqual(
            [linha.split()[0].rstrip("0123456789") for linha in texto.splitlines()],
            ["esquerda"] * 6 + ["centro"] * 6 + ["direita"] * 6,
        )

    def test_servico_nao_conclui_ausencia_apos_ocr(self):
        doc = replace(criar_documento(["Texto OCR sem titulo reconhecido."]), paginas_ocr=(1,), tempo_ocr_segundos=1.2)
        provedor = ProvedorFalso([])
        s = ServicoExtracaoInteracoes(provedor, configuracao(modo_extracao="rapido"),
                                     leitor=lambda _: doc, fabrica_medidor=MedidorFalso)
        r = s.extrair("teste", "1", None, Path("teste.pdf"))
        self.assertEqual(r.status, StatusExtracao.REVISAO_MANUAL)
        self.assertIsNone(r.trecho_interacoes)
        self.assertEqual(provedor.chamadas, [])
        linha = _linha_relatorio(bula(), r, 2.0)
        self.assertEqual(linha["paginas_ocr"], "1")
        self.assertEqual(linha["tempo_ocr_segundos"], 1.2)

    def test_erros_de_ocr_e_anexos_possuem_status_explicitos(self):
        for erro, status in (
            (OCRIndisponivelError("modelo ausente"), StatusExtracao.OCR_INDISPONIVEL),
            (ErroOCRError("falha"), StatusExtracao.ERRO_OCR),
            (PdfComAnexosError("portfolio"), StatusExtracao.REVISAO_MANUAL),
        ):
            s = ServicoExtracaoInteracoes(
                ProvedorFalso([]), configuracao(modo_extracao="rapido"),
                leitor=Mock(side_effect=erro), fabrica_medidor=MedidorFalso,
            )
            self.assertEqual(s.extrair("teste", "1", None, Path("b.pdf")).status, status)

    def test_portfolio_nao_e_confundido_com_capa_sem_secao(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "portfolio.pdf"
            with pymupdf.open() as d:
                d.new_page().insert_text((72, 72), "Abra este portfolio para ler os arquivos internos.")
                d.embfile_add("bula.txt", b"conteudo interno")
                d.save(caminho)
            with self.assertRaises(PdfComAnexosError):
                ler_pdf(caminho)
