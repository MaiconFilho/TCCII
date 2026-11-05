"""Migração do legado com publicações Smol SINTÉTICAS, sem inferência."""
import os
from pathlib import Path
import unittest

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from banco.operacao import migrar,snapshot,diagnosticar,validar,BASE
from banco.tests.test_modelo import exigir_isolado
from pipelines.resumos_interacoes.rastreabilidade import entrada_card,entrada_geral,hash_dados,simples_atual

DSN=os.getenv('MIGRACAO60_TEST_DATABASE_URL','')


@unittest.skipUnless(DSN,'Defina MIGRACAO60_TEST_DATABASE_URL em banco LOCAL test exclusivo para a fixture legada.')
class TestMigracaoLegada(unittest.TestCase):
    def setUp(self):
        exigir_isolado(DSN)
        self.c=psycopg.connect(DSN,autocommit=True,row_factory=dict_row)
        self.c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        self.c.execute((BASE/'tests/legado_base.sql').read_text(encoding='utf-8'))
        for f in sorted((BASE.parent/'pipelines/identificacao_interacoes/migracoes').glob('[0-9][0-9][0-9]_*.sql')):
            self.c.execute(f.read_text(encoding='utf-8'))
        self.c.execute("INSERT INTO bulas(nome_normalizado,nome_anvisa,principios_ativos,status,numero_registro,expediente,sha256_pdf) VALUES ('meda','Meda','meda','CONCLUIDO','123','456','pdf-sintetico'),('medb','Medb','medb','CONCLUIDO','789','012','outro-pdf')")
        self.c.execute("INSERT INTO bulas_interacoes(nome_normalizado,numero_registro,expediente,trecho_interacoes,status_extracao) VALUES ('meda','123','456','Meda aumenta o efeito de medb.','CONCLUIDO')")
        self.pid=self.c.execute("""INSERT INTO processamentos_interacoes(medicamento_origem,status,versao_processamento,modelo,
            hash_trecho,caracteres_trecho,numero_registro,expediente,sha256_pdf,quantidade_janelas,janelas_concluidas,quantidade_relacoes,iniciado_em)
            VALUES ('meda','CONCLUIDO_COM_RELACOES','sintetico','sintetico',encode(sha256(convert_to('Meda aumenta o efeito de medb.','UTF8')),'hex'),29,'123','456','pdf-sintetico',1,1,1,now()) RETURNING id""").fetchone()['id']
        self.iid=self.c.execute("""INSERT INTO interacoes(medicamento_origem,processamento_id,alvo,tipo_alvo,resumo,evidencia,resumo_card,resumo_modelo,resumo_origem,resumo_tempo_segundos,resumo_atualizado_em)
            VALUES ('meda',%s,'medb','MEDICAMENTO','Original sintético.','Meda aumenta o efeito de medb.','Card Smol sintético.','Smol-modelo-sintetico','llm',1.25,now()) RETURNING id""",(self.pid,)).fetchone()['id']
        self.c.execute("INSERT INTO interacoes_medicamentos(interacao_id,medicamento_relacionado,criterio_vinculo,fundamento_vinculo) VALUES (%s,'medb','NOME_MEDICAMENTO_EXATO','Medb')",(self.iid,))
        self.c.execute("UPDATE processamentos_interacoes SET resumo_geral='Geral sintético.',resumo_modelo='Smol-modelo-sintetico',resumo_gerado_em=now(),resumo_tempo_total_segundos=2.25")
        p=self.c.execute('SELECT p.*,p.id AS processamento_id,b.principios_ativos FROM processamentos_interacoes p JOIN bulas b ON b.nome_normalizado=p.medicamento_origem').fetchone()
        i=self.c.execute('SELECT * FROM interacoes').fetchone()
        extras={k:p[k] for k in ('hash_trecho','sha256_pdf')}
        for table,field,text,entry,key in [('interacoes','resumo_card_simples','Card simples sintético.',entrada_card(i|{'principios_ativos':p['principios_ativos']})|extras,self.iid),
                ('processamentos_interacoes','resumo_geral_simples','Geral simples sintético.',entrada_geral(p,[i])|extras,self.pid)]:
            control={'publicado':{'id':key,'status':'CONCLUIDO','prompt':'linguagem-simples/36','entrada_hash':hash_dados(entry),'texto_hash':hash_dados(text),'modelo':{'nome':'Smol sintético','revisao':'abc'}},'ultima_tentativa':{'erro':None},'extensao_preservada':['ç',1,True]}
            self.c.execute(f'UPDATE {table} SET {field}=%s,reescrita_controle=%s WHERE id=%s',(text,Jsonb(control),key))

    def tearDown(self): self.c.close()

    def test_preserva_todos_campos_jsons_ids_metadados_e_leitura_smols(self):
        before=snapshot(self.c);migrar(self.c);self.assertEqual(before,snapshot(self.c));validar(self.c)
        # A fila histórica é independente e fica intacta, fora do fluxo entregue.
        self.assertIsNotNone(self.c.execute("SELECT to_regclass('public.solicitacoes_medicamentos') AS t").fetchone()['t'])
        from apps.api_interacoes.app import criar_app
        from apps.api_interacoes.configuracao import ConfiguracaoApi
        from fastapi.testclient import TestClient
        with TestClient(criar_app(ConfiguracaoApi(dsn=DSN))) as client:
            result=client.get('/api/interacoes',params={'medicamento':'meda'}).json()
            self.assertEqual(result['relacoes'][0]['resumo_card'],'Card Smol sintético.')
            self.assertEqual(result['relacoes'][0]['resumo_card_simples'],'Card simples sintético.')
            self.assertEqual(result['resumo_geral']['texto_simples'],'Geral simples sintético.')
        self.c.execute("UPDATE interacoes SET resumo_card='Original corrigido.' WHERE id=%s",(self.iid,))
        row=self.c.execute('SELECT * FROM interacoes').fetchone()
        self.assertFalse(simples_atual(row['resumo_card_simples'],row['reescrita_controle'],entrada_card(row),legado_validado=True))

    def test_nulo_bloqueia_sem_perder_dados_ou_colunas(self):
        self.c.execute('ALTER TABLE interacoes ALTER COLUMN processamento_id DROP NOT NULL')
        self.c.execute('UPDATE interacoes SET processamento_id=NULL')
        before=snapshot(self.c)
        self.assertEqual(diagnosticar(self.c)['problemas']['interacoes.processamento_id']['nulos'],1)
        with self.assertRaisesRegex(ValueError,'integridade'):migrar(self.c)
        self.assertEqual(before,snapshot(self.c))
        self.assertEqual(self.c.execute('SELECT sha256_pdf FROM processamentos_interacoes').fetchone()['sha256_pdf'],'pdf-sintetico')

    def test_fonte_divergente_bloqueia_republicacao_e_reescrita_invalida(self):
        self.c.execute("UPDATE interacoes SET resumo_card='Outro original.'")
        with self.assertRaisesRegex(ValueError,'não validam'):migrar(self.c)
        self.c.execute("UPDATE interacoes SET resumo_card='Card Smol sintético.'")
        self.c.execute("UPDATE bulas SET sha256_pdf='mudou' WHERE nome_normalizado='meda'")
        with self.assertRaisesRegex(ValueError,'Seleção mudaria'):migrar(self.c)

    def test_preserva_rls_politica_acl_e_view_respeita_papel_consultante(self):
        self.c.execute('ALTER TABLE processamentos_interacoes ENABLE ROW LEVEL SECURITY')
        self.c.execute('CREATE POLICY restrita_teste ON processamentos_interacoes FOR SELECT TO PUBLIC USING(false)')
        before=diagnosticar(self.c)
        migrar(self.c)
        after=diagnosticar(self.c)
        self.assertEqual(before['politicas'],after['politicas'])
        old=[x for x in before['seguranca'] if x['relname']=='processamentos_interacoes']
        new=[x for x in after['seguranca'] if x['relname']=='processamentos_interacoes']
        self.assertEqual(old,new)
        with self.c.transaction():
            self.c.execute('SET LOCAL ROLE pg_read_all_data')
            self.assertEqual(self.c.execute('SELECT count(*) AS n FROM processamentos_interacoes_vigentes').fetchone()['n'],0)
