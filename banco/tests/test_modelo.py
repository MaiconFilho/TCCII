"""Regressões em PostgreSQL real. Não usa banco de trabalho nem Supabase."""
import json
import os
import unittest
from datetime import datetime,timezone

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient

from banco.operacao import MODELO,validar,snapshot,migrar
from apps.api_interacoes.app import criar_app
from apps.api_interacoes.configuracao import ConfiguracaoApi
from pipelines.resumos_interacoes.rastreabilidade import entrada_card,entrada_geral,hash_dados,VERSAO_REESCRITA

DSN=os.getenv('MODELO60_TEST_DATABASE_URL','')


def exigir_isolado(dsn):
    cfg=conninfo_to_dict(dsn)
    if 'test' not in cfg.get('dbname','').lower() or cfg.get('host') not in ('localhost','127.0.0.1'):
        raise RuntimeError('Somente banco local test pode executar esta suíte.')


@unittest.skipUnless(DSN,'Configure MODELO60_TEST_DATABASE_URL com banco local isolado já inicializado.')
class TestModelo60(unittest.TestCase):
    def setUp(self):
        exigir_isolado(DSN)
        self.c=psycopg.connect(DSN,autocommit=True,row_factory=dict_row)
        self.c.execute('BEGIN')
        for name in ('meda','medb','medc'):
            self.c.execute("INSERT INTO bulas(nome_normalizado,nome_anvisa,status,principios_ativos,numero_registro,expediente) VALUES (%s,%s,'CONCLUIDO',%s,'123','456')",(name,name.upper(),name))

    def tearDown(self):
        self.c.execute('ROLLBACK'); self.c.close()

    def extracao(self,name='meda'):
        self.c.execute("INSERT INTO bulas_interacoes(nome_normalizado,trecho_interacoes,status_extracao) VALUES (%s,'Meda aumenta o efeito de medb.','CONCLUIDO')",(name,))

    def processamento(self,name='meda',status='CONCLUIDO_COM_RELACOES'):
        return self.c.execute("INSERT INTO processamentos_interacoes(medicamento_origem,status,versao_processamento,modelo,quantidade_relacoes) VALUES (%s,%s,'teste','modelo de teste',%s) RETURNING id",(name,status,1 if status=='CONCLUIDO_COM_RELACOES' else 0)).fetchone()['id']

    def interacao(self,pid,name='meda',alvo='medb'):
        return self.c.execute("INSERT INTO interacoes(medicamento_origem,processamento_id,alvo,tipo_alvo,resumo,evidencia,resumo_card,resumo_modelo) VALUES (%s,%s,%s,'MEDICAMENTO','Resumo original de teste.','Meda aumenta o efeito de medb.','Card Smol sintético de teste.','Smol/modelo-teste') RETURNING id",(name,pid,alvo)).fetchone()['id']

    def erro(self,query,params=None):
        with self.assertRaises(psycopg.IntegrityError),self.c.transaction(): self.c.execute(query,params)

    def test_contrato_e_pais_sem_dependentes(self):
        report=validar(self.c)
        self.assertEqual(sum(map(len,MODELO.values())),60)
        self.assertEqual(report['contagens']['bulas_interacoes'],0)
        self.assertEqual(report['contagens']['processamentos_interacoes'],0)

    def test_seis_fks_nulas_orfas_pk_composta_e_extracao_unica(self):
        from banco.operacao import FKS
        self.extracao(); pid=self.processamento(); iid=self.interacao(pid)
        self.c.execute("INSERT INTO interacoes_medicamentos VALUES (%s,'medb','NOME_MEDICAMENTO_EXATO',NULL)",(iid,))
        self.erro("INSERT INTO interacoes_medicamentos VALUES (%s,'medb','NOME_MEDICAMENTO_EXATO',NULL)",(iid,))
        self.erro("INSERT INTO bulas_interacoes(nome_normalizado) VALUES ('meda')")
        for table,col,parent,parentcol in FKS:
            if table=='bulas_interacoes':
                self.erro("INSERT INTO bulas_interacoes(nome_normalizado,status_extracao) VALUES (NULL,'CONCLUIDO')")
                self.erro("INSERT INTO bulas_interacoes(nome_normalizado,status_extracao) VALUES ('ausente','CONCLUIDO')")
            else:
                key=('id',iid) if table=='interacoes' else ('id',pid) if table=='processamentos_interacoes' else ('interacao_id',iid)
                self.erro(f'UPDATE {table} SET {col}=NULL WHERE {key[0]}=%s',(key[1],))
                self.erro(f'UPDATE {table} SET {col}=%s WHERE {key[0]}=%s',(99999999 if col in ('processamento_id','interacao_id') else 'ausente',key[1]))

    def test_origem_validada_em_ambas_direcoes_sem_fk_extra(self):
        pid=self.processamento(); iid=self.interacao(pid)
        self.erro("UPDATE interacoes SET medicamento_origem='medb' WHERE id=%s",(iid,))
        self.erro("UPDATE processamentos_interacoes SET medicamento_origem='medb' WHERE id=%s",(pid,))
        self.erro("DELETE FROM processamentos_interacoes WHERE id=%s",(pid,))

    def test_tentativa_posterior_falha_revisao_e_invalida(self):
        self.extracao(); original=self.processamento()
        self.processamento(status='FALHA'); review=self.processamento(status='REVISAO_NECESSARIA')
        self.assertEqual(self.c.execute('SELECT id FROM processamentos_interacoes_vigentes').fetchone()['id'],original)
        self.assertEqual(self.c.execute('SELECT id FROM processamentos_interacoes_registrados').fetchone()['id'],review)
        self.c.execute('UPDATE processamentos_interacoes SET invalidado_em=now() WHERE id=%s',(original,))
        self.assertEqual(self.c.execute('SELECT count(*) AS n FROM processamentos_interacoes_vigentes').fetchone()['n'],0)

    def test_migracao_idempotente_preserva_smols_jsons_campos_por_pk(self):
        self.extracao(); pid=self.processamento(); iid=self.interacao(pid)
        controle={'publicado':{'status':'CONCLUIDO','prompt':VERSAO_REESCRITA},'historico':{'blocos':['ação','ç',1]},'ultima_tentativa':{'erro':None}}
        self.c.execute('UPDATE interacoes SET reescrita_controle=%s,resumo_card_simples=%s WHERE id=%s',(Jsonb(controle),'Texto simples sintético.',iid))
        self.c.execute('UPDATE processamentos_interacoes SET resumo_geral=%s,resumo_geral_simples=%s,reescrita_controle=%s WHERE id=%s',('Geral original.','Geral simples.',Jsonb(controle),pid))
        before=snapshot(self.c)
        migrar(self.c); migrar(self.c)
        self.assertEqual(snapshot(self.c),before)

    def test_trigger_invalida_reescrita_apos_mudar_entrada(self):
        self.extracao(); pid=self.processamento(); iid=self.interacao(pid)
        self.c.execute("UPDATE interacoes SET resumo_card_simples='Simples',reescrita_controle='{\"publicado\":{\"status\":\"CONCLUIDO\"}}' WHERE id=%s",(iid,))
        self.c.execute("UPDATE interacoes SET evidencia='Outra evidência.' WHERE id=%s",(iid,))
        value=self.c.execute('SELECT reescrita_controle FROM interacoes WHERE id=%s',(iid,)).fetchone()['reescrita_controle']
        self.assertTrue(value['entrada_invalidada']); self.assertEqual(value['publicado']['status'],'CONCLUIDO')

    def test_outro_alvo_nao_invalida_card_ja_publicado(self):
        self.extracao();pid=self.processamento();iid=self.interacao(pid)
        self.c.execute("UPDATE interacoes SET resumo_card_simples='Texto',reescrita_controle='{\"publicado\":{\"status\":\"CONCLUIDO\"}}' WHERE id=%s",(iid,))
        self.interacao(pid,alvo='medc')
        value=self.c.execute('SELECT reescrita_controle FROM interacoes WHERE id=%s',(iid,)).fetchone()['reescrita_controle']
        self.assertNotIn('entrada_invalidada',value)

    def test_snapshot_independe_de_fuso_e_preserva_precisao_jsonb(self):
        pid=self.processamento();iid=self.interacao(pid)
        a=snapshot(self.c)
        self.c.execute("SET LOCAL TIME ZONE 'UTC'")
        self.assertEqual(a,snapshot(self.c))
        self.c.execute("SET LOCAL TIME ZONE 'America/Sao_Paulo'")
        self.assertEqual(a,snapshot(self.c))
        self.c.execute('UPDATE interacoes SET reescrita_controle=%s::jsonb WHERE id=%s',('{"preciso":0.123456789012345678901}',iid))
        a=snapshot(self.c)
        self.c.execute('UPDATE interacoes SET reescrita_controle=%s::jsonb WHERE id=%s',('{"preciso":0.123456789012345678902}',iid))
        self.assertNotEqual(a,snapshot(self.c))
        self.c.execute("UPDATE interacoes SET reescrita_controle='null'::jsonb WHERE id=%s",(iid,))
        a=snapshot(self.c)
        self.c.execute('UPDATE interacoes SET reescrita_controle=NULL WHERE id=%s',(iid,))
        self.assertNotEqual(a,snapshot(self.c))

    def test_snapshot_float8_exato_independente_de_extra_float_digits(self):
        pid=self.processamento();iid=self.interacao(pid)
        self.c.execute('UPDATE interacoes SET resumo_tempo_segundos=%s WHERE id=%s',(9.058465800015256,iid))
        a=snapshot(self.c)
        for precision in (0,1,3):
            self.c.execute('SET LOCAL extra_float_digits='+str(precision))
            self.assertEqual(a,snapshot(self.c))


@unittest.skipUnless(DSN,'Configure MODELO60_TEST_DATABASE_URL com banco local isolado já inicializado.')
class TestApiReal(unittest.TestCase):
    def setUp(self):
        exigir_isolado(DSN)
        self.c=psycopg.connect(DSN,autocommit=True,row_factory=dict_row)
        # Esta classe precisa de commits para que o pool leia as fixtures.
        self.c.execute('TRUNCATE interacoes_medicamentos,interacoes,processamentos_interacoes,bulas_interacoes,bulas RESTART IDENTITY')
        for name in ('meda','medb','sem extracao','sem processamento','sem mencoes','revisao','falha','retirado','negativo'):
            self.c.execute("INSERT INTO bulas(nome_normalizado,nome_anvisa,status,numero_registro,expediente) VALUES (%s,%s,'CONCLUIDO','123','456')",(name,name))
            if name!='sem extracao': self.c.execute("INSERT INTO bulas_interacoes(nome_normalizado,trecho_interacoes,status_extracao) VALUES (%s,'Meda aumenta o efeito de medb.','CONCLUIDO')",(name,))
        self.pids={}
        for name,status in [('meda','CONCLUIDO_COM_RELACOES'),('medb','CONCLUIDO_COM_RELACOES'),('sem mencoes','CONCLUIDO_COM_RELACOES'),('revisao','REVISAO_NECESSARIA'),('falha','FALHA'),('retirado','CONCLUIDO_SEM_RELACOES'),('negativo','CONCLUIDO_SEM_RELACOES')]:
            pid=self.c.execute("INSERT INTO processamentos_interacoes(medicamento_origem,status,versao_processamento,modelo,quantidade_relacoes) VALUES (%s,%s,'teste','teste',%s) RETURNING id",(name,status,1 if status=='CONCLUIDO_COM_RELACOES' else 0)).fetchone()['id']; self.pids[name]=pid
        self.c.execute("UPDATE processamentos_interacoes SET invalidado_em=now() WHERE medicamento_origem='retirado'")
        for name,other in [('meda','medb'),('medb','meda')]:
            iid=self.c.execute("INSERT INTO interacoes(medicamento_origem,processamento_id,alvo,tipo_alvo,resumo,evidencia,resumo_card) VALUES (%s,%s,%s,'MEDICAMENTO','Original','Meda aumenta o efeito de medb.','Card Smol sintético.') RETURNING id",(name,self.pids[name],other)).fetchone()['id']
            self.c.execute("INSERT INTO interacoes_medicamentos VALUES (%s,%s,'NOME_MEDICAMENTO_EXATO',NULL)",(iid,other))
        # Menção sem vínculo deve continuar disponível na consulta individual.
        self.c.execute("INSERT INTO interacoes(medicamento_origem,processamento_id,alvo,tipo_alvo,resumo,evidencia) VALUES ('meda',%s,'alimentos','ALIMENTO','Original','Meda interage com alimentos.')",(self.pids['meda'],))
        self.client=TestClient(criar_app(ConfiguracaoApi(dsn=DSN,cache_cobertura_segundos=0)))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.c.execute('TRUNCATE interacoes_medicamentos,interacoes,processamentos_interacoes,bulas_interacoes,bulas RESTART IDENTITY'); self.c.close()

    def test_individual_pares_dois_sentidos_paginacao_e_sem_vinculo(self):
        r=self.client.get('/api/interacoes',params={'medicamento':'meda','tamanho_pagina':1}).json()
        self.assertEqual(r['paginacao']['total'],2); self.assertEqual(r['relacoes'][0]['resumo_card'],'Card Smol sintético.')
        r=self.client.get('/api/interacoes',params={'medicamento':'meda','pagina':2,'tamanho_pagina':1}).json()
        self.assertEqual(r['relacoes'][0]['medicamentos_relacionados'],[])
        pair=self.client.get('/api/interacoes/par',params={'medicamento_a':'meda','medicamento_b':'medb'}).json()
        self.assertTrue(pair['interacao_identificada']); self.assertEqual({e['sentido'] for e in pair['evidencias']},{'BULA_A_CITA_B','BULA_B_CITA_A'})
        self.assertEqual(pair['evidencias'][0]['fonte']['numero_registro'],'123')

    def test_ausencias_estados_negativo_e_health(self):
        self.assertEqual(self.client.get('/api/saude').json()['esquema'],'ok')
        for name,state in [('sem extracao','EXTRACAO_INDISPONIVEL'),('sem processamento','NAO_PROCESSADO'),('sem mencoes','CONCLUIDO_COM_RELACOES'),('revisao','REVISAO_NECESSARIA'),('falha','FALHA'),('retirado','RETIRADO')]:
            r=self.client.get('/api/interacoes',params={'medicamento':name})
            self.assertEqual(r.status_code,200); self.assertIsNone(r.json()['interacao_identificada']); self.assertEqual(r.json()['analise_bula']['estado'],state)
        r=self.client.get('/api/interacoes',params={'medicamento':'negativo'}).json()
        self.assertFalse(r['interacao_identificada'])
        cors=self.client.options('/api/interacoes',headers={'Origin':'http://localhost:3000','Access-Control-Request-Method':'GET'})
        self.assertEqual(cors.headers['access-control-allow-origin'],'http://localhost:3000')

    def test_falha_na_reescrita_nao_revalida_publicacao_antiga(self):
        from pipelines.resumos_interacoes.repositorio import RepositorioReescrita,unidades_reescrita
        repo=RepositorioReescrita(DSN)
        try:
            unidade=next(u for u in unidades_reescrita(repo.fonte('meda')) if u['tipo']=='card')
            texto='Card simples sintético.'
            controle={'publicado':{'status':'CONCLUIDO','prompt':VERSAO_REESCRITA,'id':unidade['id'],
                'entrada_hash':hash_dados(unidade['entrada']),'texto_hash':hash_dados(texto)}}
            repo.gravar_reescrita('meda',unidade,texto,controle)
            self.c.execute("UPDATE interacoes SET resumo_card='Original alterado.' WHERE id=%s",(unidade['id'],))
            atual=next(u for u in unidades_reescrita(repo.fonte('meda')) if u['tipo']=='card')
            repo.gravar_reescrita('meda',atual,None,{'ultima_tentativa':{'status':'FALHA'}})
            row=self.c.execute('SELECT reescrita_controle,resumo_card_simples FROM interacoes WHERE id=%s',(unidade['id'],)).fetchone()
            self.assertTrue(row['reescrita_controle']['entrada_invalidada'])
            self.assertEqual(row['resumo_card_simples'],texto)
            body=self.client.get('/api/interacoes',params={'medicamento':'meda'}).json()
            self.assertIsNone(next(i for i in body['relacoes'] if i['id']==unidade['id'])['resumo_card_simples'])
        finally:repo.fechar()


if __name__=='__main__': unittest.main()
