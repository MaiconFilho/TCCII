"""Varredura estrutural em modo somente leitura; nao carrega LLM nem grava no banco."""
import argparse
import json
import os
import sys
from pathlib import Path
from collections import Counter
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE.parent))
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from extracao_interacoes.leitor_pdf import ler_pdf
from extracao_interacoes.validacao import localizar_titulos_interacoes, localizar_fallback_estrutural, copiar_e_validar_trecho, limite_no_documento, sem_acentos
from extracao_interacoes.modo_rapido import preparar_confirmacoes

def diagnosticar(item):
    saida=dict(item)
    try:
        doc=ler_pdf(Path(item['caminho_pdf']))
        saida['paginas']=doc.quantidade_paginas
        saida['paginas_ocr']=doc.paginas_ocr
        titulos=localizar_titulos_interacoes(doc)
        saida['titulos']=[]
        for i,n,t in titulos:
            fim=next((l.numero for l in doc.linhas[i+1:] if limite_no_documento(doc,l.numero-1,t)),len(doc.linhas)+1)
            detalhe={'pagina':doc.linhas[i].pagina,'linha':doc.linhas[i].identificador,'titulo':t,'fim':fim}
            try:
                trecho=copiar_e_validar_trecho(doc,doc.linhas[i].identificador,f'L{fim:06d}',t)
                detalhe.update(caracteres=len(trecho),amostra=trecho[:250],final=trecho[-200:])
            except Exception as erro:
                detalhe['erro']=str(erro)
            saida['titulos'].append(detalhe)
        candidatos=localizar_fallback_estrutural(doc)
        saida['candidatos']=candidatos
        saida['confirmavel']=preparar_confirmacoes(doc) is not None
        saida['indicativos']=[]
        for i,l in enumerate(doc.linhas):
            if 'intera' in sem_acentos(l.texto_original):
                saida['indicativos'].append({'pagina':l.pagina,'linha':l.identificador,'texto':l.texto_original,
                    'contexto':[x.texto_original for x in doc.linhas[max(0,i-1):i+4]]})
        saida['classe']='PRONTO_LLM' if saida['confirmavel'] else 'TITULO_REJEITADO' if titulos else 'SEM_TITULO'
    except Exception as erro:
        saida.update(classe=type(erro).__name__,erro=str(erro))
    return saida

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--saida',type=Path,required=True)
    args=p.parse_args()
    if args.saida.exists(): raise SystemExit('Destino existente; escolha outro nome.')
    load_dotenv(BASE/'.env')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,options='-c default_transaction_read_only=on') as c:
        itens=c.execute('''SELECT b.nome_normalizado,b.caminho_pdf,bi.status_extracao,bi.detalhe_revisao
            FROM public.bulas b LEFT JOIN public.bulas_interacoes bi USING(nome_normalizado)
            WHERE b.status='CONCLUIDO' AND b.caminho_pdf IS NOT NULL
              AND (bi.nome_normalizado IS NULL OR bi.status_extracao <> 'CONCLUIDO')
            ORDER BY b.nome_normalizado''').fetchall()
    resultados=[]
    for i,item in enumerate(itens,1):
        resultado=diagnosticar(item);resultados.append(resultado)
        print(f"[{i}/{len(itens)}] {item['nome_normalizado']}: {resultado['classe']}",flush=True)
    args.saida.parent.mkdir(parents=True,exist_ok=True)
    with args.saida.open('x',encoding='utf-8') as f: json.dump(resultados,f,ensure_ascii=False,indent=2)
    print(dict(Counter(r['classe'] for r in resultados)))

if __name__=='__main__': main()
