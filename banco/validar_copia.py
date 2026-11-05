"""Valida e migra SOMENTE a cópia local restaurada de um backup identificado."""
import argparse
import json
from pathlib import Path

from dotenv import dotenv_values
import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from .operacao import snapshot,migrar,validar,MIGRACAO,destino,exigir_banco_teste_local
from apps.api_interacoes.app import criar_app
from apps.api_interacoes.configuracao import ConfiguracaoApi


def conferir(condition,message):
    if not condition: raise ValueError(message)


def verificar_api(dsn,c):
    """Lê consultas reais e confere cada resumo retornado contra sua PK."""
    checks=[]
    names=c.execute('''SELECT b.nome_normalizado FROM bulas b
        LEFT JOIN processamentos_interacoes_vigentes v ON v.medicamento_origem=b.nome_normalizado
        ORDER BY (EXISTS(SELECT 1 FROM interacoes i WHERE i.processamento_id=v.id
            AND (i.resumo_modelo ILIKE '%%smol%%' OR i.reescrita_controle::text ILIKE '%%smol%%'))) DESC,
            (v.id IS NOT NULL) DESC,b.nome_normalizado LIMIT 5''').fetchall()
    with TestClient(criar_app(ConfiguracaoApi(dsn=dsn,cache_cobertura_segundos=0))) as client:
        response=client.get('/api/saude')
        conferir(response.status_code==200 and response.json()['esquema']=='ok','Saúde/schema da API incompatível.')
        cors=client.options('/api/interacoes',headers={'Origin':'http://localhost:3000','Access-Control-Request-Method':'GET'})
        conferir(cors.headers.get('access-control-allow-origin')=='http://localhost:3000','CORS local não confere.')
        for item in names:
            name=item['nome_normalizado']
            response=client.get('/api/interacoes',params={'medicamento':name,'tamanho_pagina':100})
            conferir(response.status_code==200,'Falha na consulta individual: '+name)
            body=response.json()
            for row in body['relacoes']:
                stored=c.execute('SELECT * FROM interacoes WHERE id=%s',(row['id'],)).fetchone()
                conferir(row['resumo']==stored['resumo'] and row['evidencia']==stored['evidencia'],'Original/evidência divergente por ID.')
                conferir(row['resumo_card']==((stored['resumo_card'] or '').strip() or None),'Card divergente por ID.')
                if row.get('resumo_card_simples') is not None:
                    conferir(row['resumo_card_simples']==stored['resumo_card_simples'],'Card simples divergente por ID.')
            geral=body['resumo_geral']
            if geral['texto']:
                stored=c.execute('SELECT resumo_geral,resumo_geral_simples FROM processamentos_interacoes WHERE id=%s',(geral['processamento_id'],)).fetchone()
                conferir(geral['texto']==stored['resumo_geral'],'Resumo geral divergente por ID.')
                if geral.get('texto_simples') is not None:
                    conferir(geral['texto_simples']==stored['resumo_geral_simples'],'Resumo geral simples divergente por ID.')
            checks.append({'medicamento':name,'estado':body['analise_bula']['estado'],'cards_na_pagina':len(body['relacoes']),'total':body['paginacao']['total']})
        if len(names)>1:
            for a,b in [(names[0],names[1]),(names[1],names[0])]:
                response=client.get('/api/interacoes/par',params={'medicamento_a':a['nome_normalizado'],'medicamento_b':b['nome_normalizado']})
                conferir(response.status_code==200,'Falha na consulta de par.')
        suggestions=client.get('/api/medicamentos/sugestoes',params={'q':names[0]['nome_normalizado'][:3] if names else 'a'})
        conferir(suggestions.status_code==200,'Falha na consulta de sugestões.')
    return checks


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-copia',type=Path,required=True);p.add_argument('--manifesto',type=Path,required=True)
    p.add_argument('--saida',type=Path,required=True);args=p.parse_args()
    dsn=dotenv_values(args.env_copia)['DATABASE_URL'];exigir_banco_teste_local(dsn)
    manifest=json.loads(args.manifesto.read_text(encoding='utf-8'))
    with psycopg.connect(dsn,autocommit=True,row_factory=dict_row) as c:
        if snapshot(c)!=manifest['registros_por_pk']: raise ValueError('Cópia difere do backup antes da migração.')
        report=migrar(c,esperado=manifest['registros_por_pk'])
        checks=verificar_api(dsn,c)
        if snapshot(c)!=manifest['registros_por_pk']: raise ValueError('Dados diferem após migração/testes.')
    result={'migracao':MIGRACAO,'backup_sha256':manifest['sha256'],'origem':manifest['origem'],
        'destino_teste':destino(dsn),'testes_api_aprovados':True,'comparacao_por_pk':'igual em todos os campos mantidos',
        'consultas':checks,'contagens':report['contagens']}
    args.saida.parent.mkdir(parents=True,exist_ok=True);args.saida.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Cópia migrada, preservação e API verificadas. Relatório: '+str(args.saida))


if __name__=='__main__': main()
