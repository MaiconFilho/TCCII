"""Auditoria somente leitura no banco; escreve apenas um relatorio JSON local."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE.parent) not in sys.path:
    sys.path.insert(0, str(BASE.parent))

import psycopg
import pymupdf
from dotenv import dotenv_values, load_dotenv

from extracao_interacoes.leitor_pdf import ler_pdf
from extracao_interacoes.ocr import motivo_ocr
from extracao_interacoes.validacao import localizar_fallback_estrutural, localizar_titulos_interacoes


def sem_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto.casefold())
                   if not unicodedata.combining(c))


def auditar(item, executar_ocr=True):
    resultado = dict(item)
    with pymupdf.open(item["arquivo_pdf"]) as doc:
        resultado["paginas"] = len(doc)
        resultado["anexos"] = doc.embfile_names()
        textos, motivos = [], []
        for pagina in doc:
            texto = pagina.get_text("text", sort=True)
            textos.append(texto)
            motivo = motivo_ocr(pagina, texto)
            if motivo:
                motivos.append({"pagina": pagina.number + 1, "motivo": motivo})
        resultado["paginas_sugeridas_ocr"] = motivos
        resultado["caracteres_nativos"] = sum(len(t.strip()) for t in textos)
        resultado["indicativos"] = [
            {"pagina": i, "linha": linha.strip()[:350]}
            for i, texto in enumerate(textos, 1)
            for linha in texto.splitlines()
            if "intera" in sem_acentos(linha) and "medic" in sem_acentos(linha)
        ][:12]
        resultado["amostra_nativa"] = " ".join(" ".join(textos).split())[:300]
    if resultado["anexos"]:
        resultado["classificacao"] = "PDF_COM_ANEXOS"
        return resultado
    if motivos and not executar_ocr:
        resultado["classificacao"] = "PRECISA_OCR"
        return resultado
    try:
        documento = ler_pdf(Path(item["arquivo_pdf"]))
    except Exception as erro:
        resultado["classificacao"] = type(erro).__name__
        resultado["detalhe"] = str(erro)
        return resultado
    resultado["paginas_ocr"] = list(documento.paginas_ocr)
    resultado["tempo_ocr_segundos"] = round(documento.tempo_ocr_segundos, 3)
    resultado["caracteres_lidos"] = documento.quantidade_caracteres
    titulos = localizar_titulos_interacoes(documento)
    resultado["titulos"] = [
        {"pagina": documento.linhas[i].pagina, "titulo": t}
        for i, _, t in titulos
    ]
    resultado["secoes_estruturais"] = localizar_fallback_estrutural(documento)
    resultado["indicativos_apos_leitura"] = [
        {"pagina": linha.pagina, "linha": linha.texto_original.strip()[:350]}
        for linha in documento.linhas
        if "intera" in sem_acentos(linha.texto_original)
        and "medic" in sem_acentos(linha.texto_original)
    ][:12]
    origem = "OCR" if documento.paginas_ocr else "TEXTO"
    resultado["classificacao"] = origem + (
        "_COM_TITULO" if titulos else "_SEM_TITULO_RECONHECIDO"
    )
    # Ausencia de titulo por heuristica NAO e confirmacao clinica/documental.
    return resultado


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relatorio-lote", type=Path, default=BASE / "relatorios/lote_completo_01.csv")
    parser.add_argument("--saida", type=Path, default=BASE / "relatorios/auditoria_ocr.json")
    parser.add_argument("--sem-ocr", action="store_true")
    args = parser.parse_args()
    if args.saida.exists():
        raise SystemExit("O relatorio de destino ja existe; escolha outro nome.")
    load_dotenv(BASE / ".env")
    config = dotenv_values(BASE / ".env")
    with psycopg.connect(
        config["DATABASE_URL"], connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conexao:
        with conexao.cursor() as cursor:
            cursor.execute(
                "SELECT bi.nome_normalizado, b.caminho_pdf "
                "FROM public.bulas_interacoes bi JOIN public.bulas b USING(nome_normalizado) "
                "WHERE bi.status_extracao = 'SEM_SECAO_INTERACOES' ORDER BY bi.nome_normalizado"
            )
            itens = {n: {"nome_normalizado": n, "arquivo_pdf": p,
                         "status_anterior": "SEM_SECAO_INTERACOES"}
                     for n, p in cursor.fetchall()}
    with args.relatorio_lote.open(encoding="utf-8-sig", newline="") as arquivo:
        for linha in csv.DictReader(arquivo, delimiter=";"):
            if linha["status"] == "PDF_SEM_TEXTO":
                itens[linha["nome_normalizado"]] = {
                    "nome_normalizado": linha["nome_normalizado"],
                    "arquivo_pdf": linha["arquivo_pdf"],
                    "status_anterior": linha["status"],
                }
    resultados = []
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    # Checkpoint JSONL exclusivo permite recuperar a auditoria interrompida.
    checkpoint = args.saida.with_suffix(".jsonl")
    with checkpoint.open("x", encoding="utf-8") as arquivo:
        for indice, item in enumerate(itens.values(), 1):
            try:
                resultado = auditar(item, not args.sem_ocr)
            except Exception as erro:
                resultado = dict(item, classificacao="ERRO_AUDITORIA", detalhe=str(erro))
            resultados.append(resultado)
            arquivo.write(json.dumps(resultado, ensure_ascii=False) + "\n")
            arquivo.flush()
            print(f"[{indice}/{len(itens)}] {item['nome_normalizado']}: "
                  f"{resultado['classificacao']}", flush=True)
    saida = {
        "aviso": "SEM_TITULO_RECONHECIDO nao comprova ausencia; conferir indicativos e imagem.",
        "contagens": dict(Counter(r["classificacao"] for r in resultados)),
        "registros": resultados,
    }
    with args.saida.open("x", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=2)
    print(json.dumps(saida["contagens"], ensure_ascii=False))
    print(f"Auditoria: {args.saida.resolve()}")


if __name__ == "__main__":
    main()
