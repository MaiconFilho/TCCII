"""Operações explícitas; credenciais somente por ambiente ou arquivo .env.

Sem argumentos, apenas diagnostica. Nunca restaura no Supabase nem executa
as migrações históricas 001..007 sobre o modelo consolidado.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from dotenv import dotenv_values

BASE = Path(__file__).resolve().parent
MODELO = json.loads((BASE / 'modelo.json').read_text(encoding='utf-8'))
TABELAS = ('bulas', 'bulas_interacoes', 'processamentos_interacoes', 'interacoes', 'interacoes_medicamentos')
CHAVES = {t: ('id',) for t in TABELAS} | {'bulas': ('nome_normalizado',), 'bulas_interacoes': ('nome_normalizado',),
    'interacoes_medicamentos': ('interacao_id', 'medicamento_relacionado')}
FKS = (('bulas_interacoes','nome_normalizado','bulas','nome_normalizado'),
    ('processamentos_interacoes','medicamento_origem','bulas','nome_normalizado'),
    ('interacoes','medicamento_origem','bulas','nome_normalizado'),
    ('interacoes','processamento_id','processamentos_interacoes','id'),
    ('interacoes_medicamentos','interacao_id','interacoes','id'),
    ('interacoes_medicamentos','medicamento_relacionado','bulas','nome_normalizado'))
VIEWS = ('processamentos_interacoes_vigentes','processamentos_interacoes_registrados')
MIGRACAO = 'modelo60/v1'


def json_texto(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def destino(dsn):
    config = conninfo_to_dict(dsn)
    result={k: config.get(k, '') for k in ('host','port','dbname')}
    host=result['host']
    if host.endswith('.pooler.supabase.com'):
        user=config.get('user','')
        result['projeto']=user.rsplit('.',1)[1] if '.' in user else None
    elif host.startswith('db.') and host.endswith('.supabase.co'):
        result['projeto']=host.split('.')[1]
    return result


def exigir_banco_teste_local(dsn):
    config=conninfo_to_dict(dsn)
    if 'test' not in config.get('dbname','').lower() or config.get('host') not in ('localhost','127.0.0.1'):
        raise ValueError('Somente banco local com test no nome pode executar esta operação de ensaio.')


def colunas(c):
    return c.execute("""SELECT table_name,column_name,udt_name,is_nullable FROM information_schema.columns
        WHERE table_schema='public' AND table_name=ANY(%s) ORDER BY table_name,ordinal_position""", (list(TABELAS),)).fetchall()


def diagnosticar(c):
    campos = colunas(c)
    presentes = {r['table_name'] for r in campos}
    report = {'migracao': MIGRACAO, 'data_utc': datetime.now(timezone.utc).isoformat(),
        'colunas': campos, 'faltantes': sorted(set(TABELAS)-presentes), 'problemas': {}, 'contagens': {}}
    report['servidor']=c.execute("SELECT current_setting('server_version') AS versao,current_database() AS banco,current_setting('TimeZone') AS fuso").fetchone()
    for t in TABELAS:
        if t not in presentes: continue
        report['contagens'][t] = c.execute(sql.SQL('SELECT count(*) AS n FROM public.{}').format(sql.Identifier(t))).fetchone()['n']
        keys = sql.SQL(',').join(map(sql.Identifier,CHAVES[t]))
        report['problemas'][t+'.duplicadas'] = c.execute(sql.SQL('SELECT count(*) AS n FROM (SELECT {} FROM public.{} GROUP BY {} HAVING count(*)>1) d').format(keys,sql.Identifier(t),keys)).fetchone()['n']
    for t,f,p,pf in FKS:
        if not {t,p} <= presentes: continue
        counts = c.execute(sql.SQL('''SELECT count(*) FILTER (WHERE d.{f} IS NULL) AS nulos,
            count(*) FILTER (WHERE d.{f} IS NOT NULL AND p.{pf} IS NULL) AS orfaos
            FROM public.{t} d LEFT JOIN public.{p} p ON p.{pf}=d.{f}''').format(
                t=sql.Identifier(t),f=sql.Identifier(f),p=sql.Identifier(p),pf=sql.Identifier(pf))).fetchone()
        report['problemas'][t+'.'+f] = counts
    if {'interacoes','processamentos_interacoes'} <= presentes:
        report['problemas']['origens_divergentes'] = c.execute('''SELECT count(*) AS n FROM public.interacoes i
            JOIN public.processamentos_interacoes p ON p.id=i.processamento_id
            WHERE i.medicamento_origem IS DISTINCT FROM p.medicamento_origem''').fetchone()['n']
        origem = sql.Identifier('resumo_origem') if any(r['table_name']=='interacoes' and r['column_name']=='resumo_origem' for r in campos) else sql.SQL('NULL::text')
        report['modelos_cards'] = c.execute(sql.SQL('SELECT resumo_modelo,{} AS resumo_origem,count(*) AS quantidade FROM public.interacoes GROUP BY 1,2 ORDER BY 1,2').format(origem)).fetchall()
        report['modelos_gerais'] = c.execute('SELECT resumo_modelo,count(*) AS quantidade FROM public.processamentos_interacoes GROUP BY 1 ORDER BY 1').fetchall()
    report['seguranca'] = c.execute('''SELECT relname,relrowsecurity,relforcerowsecurity,relacl::text,
        pg_get_userbyid(relowner) AS proprietario FROM pg_class WHERE oid IN
        (SELECT to_regclass('public.'||x) FROM unnest(%s::text[]) x)''',(list(TABELAS+VIEWS),)).fetchall()
    report['politicas'] = c.execute("SELECT * FROM pg_policies WHERE schemaname='public' AND tablename=ANY(%s)",(list(TABELAS),)).fetchall()
    report['constraints'] = c.execute('''SELECT conrelid::regclass::text AS tabela,conname,contype,pg_get_constraintdef(oid) AS definicao
        FROM pg_constraint WHERE conrelid IN (SELECT to_regclass('public.'||x) FROM unnest(%s::text[]) x) ORDER BY 1,2''',(list(TABELAS),)).fetchall()
    report['views_dependentes'] = c.execute('''SELECT DISTINCT dependent.relname AS view, source.relname AS tabela
        FROM pg_depend d JOIN pg_rewrite r ON r.oid=d.objid JOIN pg_class dependent ON dependent.oid=r.ev_class
        JOIN pg_class source ON source.oid=d.refobjid JOIN pg_namespace ns ON ns.oid=source.relnamespace
        WHERE source.relname=ANY(%s) AND ns.nspname='public' AND dependent.oid<>source.oid''',(list(TABELAS),)).fetchall()
    removed={r['column_name'] for r in campos if r['column_name'] not in MODELO[r['table_name']]}
    report['triggers']=c.execute('''SELECT t.tgname,cl.relname AS tabela,t.tgfoid::regproc::text AS funcao,
        pg_get_triggerdef(t.oid) AS definicao,pg_get_functiondef(t.tgfoid) AS corpo
        FROM pg_trigger t JOIN pg_class cl ON cl.oid=t.tgrelid WHERE NOT t.tgisinternal
        AND t.tgrelid IN (SELECT to_regclass('public.'||x) FROM unnest(%s::text[]) x)''',(list(TABELAS),)).fetchall()
    for trigger in report['triggers']:
        body=trigger.pop('corpo')
        trigger['referencias_legadas']=sorted(f for f in removed if re.search(r'\b'+re.escape(f)+r'\b',body))
    report['tabelas_fora_modelo'] = c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND NOT (tablename=ANY(%s))",(list(TABELAS),)).fetchall()
    return report


def problemas(report):
    def total(v): return sum(total(x) for x in v.values()) if isinstance(v,dict) else int(v)
    return report['faltantes'] or {k:v for k,v in report['problemas'].items() if total(v)}


def snapshot(c):
    """Digest por PK de TODOS os 60 campos, incluindo JSONBs/textos sem normalizar."""
    result = {}
    existentes={(r['table_name'],r['column_name']) for r in colunas(c)}
    for t in TABELAS:
        def projection(field,kind):
            if (t,field) not in existentes: return sql.SQL('NULL AS {}').format(sql.Identifier(field))
            if kind=='JSONB': return sql.SQL('{}::text AS {}').format(sql.Identifier(field),sql.Identifier(field))
            if kind=='TIMESTAMPTZ': return sql.SQL('EXTRACT(EPOCH FROM {})::text AS {}').format(sql.Identifier(field),sql.Identifier(field))
            if kind=='FLOAT8': return sql.SQL("encode(float8send({}),'hex') AS {}").format(sql.Identifier(field),sql.Identifier(field))
            return sql.Identifier(field)
        fields = sql.SQL(',').join(projection(f,kind) for f,kind in MODELO[t].items())
        rows = c.execute(sql.SQL('SELECT {} FROM public.{}').format(fields,sql.Identifier(t))).fetchall()
        result[t] = {json_texto([row[k] for k in CHAVES[t]]): hashlib.sha256(json_texto(row).encode()).hexdigest() for row in rows}
    return result


def validar(c):
    campos = colunas(c)
    equivalencias={'TEXT':'text','BIGINT':'int8','INTEGER':'int4','FLOAT8':'float8','NUMERIC':'numeric','TIMESTAMPTZ':'timestamptz','JSONB':'jsonb'}
    expected={(t,k):equivalencias[v] for t,cols in MODELO.items() for k,v in cols.items()}
    found={(r['table_name'],r['column_name']):r['udt_name'] for r in campos}
    if found != expected: raise ValueError('Schema diverge das cinco tabelas/60 campos/tipos aprovados.')
    for t,f,_,_ in FKS:
        if not any(r['table_name']==t and r['column_name']==f and r['is_nullable']=='NO' for r in campos):
            raise ValueError(f'FK aceita NULL: {t}.{f}')
    for t,keys in CHAVES.items():
        pk=c.execute('''SELECT array_agg(a.attname ORDER BY u.n) AS campos FROM pg_constraint c
            CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY u(attnum,n)
            JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=u.attnum
            WHERE c.contype='p' AND c.conrelid=to_regclass(%s) GROUP BY c.oid''',('public.'+t,)).fetchall()
        if len(pk)!=1 or pk[0]['campos']!=list(keys): raise ValueError('PK incorreta: '+t)
    foreign=c.execute('''SELECT r.relname AS t, a.attname AS f, p.relname AS p, b.attname AS pf, c.confdeltype
        FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_class p ON p.oid=c.confrelid
        JOIN pg_attribute a ON a.attrelid=r.oid AND a.attnum=c.conkey[1]
        JOIN pg_attribute b ON b.attrelid=p.oid AND b.attnum=c.confkey[1]
        WHERE c.contype='f' AND c.conrelid IN (SELECT to_regclass('public.'||x) FROM unnest(%s::text[]) x)''',(list(TABELAS),)).fetchall()
    if len(foreign)!=6 or {(r['t'],r['f'],r['p'],r['pf']) for r in foreign}!=set(FKS) or any(r['confdeltype'] not in ('r','a') for r in foreign):
        raise ValueError('Seis FKs protetoras não conferem.')
    for view in VIEWS:
        metadata=c.execute("SELECT obj_description(to_regclass(%s),'pg_class') AS comentario,reloptions FROM pg_class WHERE oid=to_regclass(%s)",('public.'+view,'public.'+view)).fetchone()
        if not metadata or not (metadata['comentario'] or '').lower().startswith(MIGRACAO+':') or 'security_invoker=true' not in (metadata['reloptions'] or []):
            raise ValueError('View/versão/segurança incompatível: '+view)
    expected_triggers={'interamed_origem_interacao','interamed_origem_processamento','interamed_entrada_card','interamed_entrada_geral','interamed_entrada_cadastro','interamed_fonte_extraida','interamed_fonte_bula'}
    triggers=c.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled <> 'D' AND tgrelid IN (SELECT to_regclass('public.'||x) FROM unnest(%s::text[]) x)",(list(TABELAS),)).fetchall()
    if not expected_triggers<={t['tgname'] for t in triggers}: raise ValueError('Triggers de integridade/validação ausentes ou desabilitados.')
    for table,field,_,_ in FKS:
        indexed=c.execute('''SELECT EXISTS(SELECT 1 FROM pg_index ix JOIN pg_attribute a
            ON a.attrelid=ix.indrelid AND a.attnum=ix.indkey[0]
            WHERE ix.indrelid=to_regclass(%s) AND a.attname=%s AND ix.indisvalid AND ix.indpred IS NULL) AS ok''',('public.'+table,field)).fetchone()['ok']
        if not indexed: raise ValueError('Índice completo da FK ausente: '+table+'.'+field)
    for table in ('interacoes','processamentos_interacoes'):
        seq=c.execute("SELECT pg_get_serial_sequence(%s,'id') AS s",('public.'+table,)).fetchone()['s']
        if not seq: raise ValueError('Geração de IDs ausente: '+table)
        maximum=c.execute(sql.SQL('SELECT COALESCE(max(id),0) AS n FROM public.{}').format(sql.Identifier(table))).fetchone()['n']
        state=c.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier(*seq.split('.')))).fetchone()
        if maximum and (state['last_value']<maximum or (state['last_value']==maximum and not state['is_called'])):
            raise ValueError('Sequência pode reutilizar ID: '+table)
    report=diagnosticar(c)
    if problemas(report): raise ValueError('Integridade inválida: '+json_texto(report['problemas']))
    return report


def inicializar(c):
    if colunas(c): raise ValueError('Instalação nova exige ausência das tabelas de domínio; use migrar para atualizar.')
    with c.transaction():
        c.execute((BASE/'modelo.sql').read_text(encoding='utf-8'))
        c.execute((BASE/'objetos.sql').read_text(encoding='utf-8'))
        validar(c)


def migrar(c, *, esperado=None):
    """Transação atômica. Nunca apaga linhas, não usa CASCADE, não altera JSONBs."""
    with c.transaction():
        c.execute("SET LOCAL lock_timeout='10s'")
        c.execute('SELECT pg_advisory_xact_lock(670060001)')
        c.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(sql.SQL(',').join(sql.Identifier('public',t) for t in TABELAS)))
        before=diagnosticar(c)
        if problemas(before): raise ValueError('Migração bloqueada por integridade: '+json_texto(before['problemas']))
        incompatible=[t['tgname'] for t in before['triggers'] if t['referencias_legadas']]
        if incompatible: raise ValueError('Triggers ainda referenciam colunas retiradas; migração bloqueada: '+str(incompatible))
        current={(r['table_name'],r['column_name']) for r in before['colunas']}
        missing={(t,f) for t,fields in MODELO.items() for f in fields}-current
        opcionais = {'resumo_card_simples','resumo_geral_simples','reescrita_controle','resumo_origem'}
        if any(f not in opcionais for t,f in missing):
            raise ValueError('Baseline anterior incompleta: '+str(sorted(missing)))
        for t,f in sorted(missing):
            c.execute(sql.SQL('ALTER TABLE public.{} ADD COLUMN {} {}').format(sql.Identifier(t),sql.Identifier(f),sql.SQL(MODELO[t][f])))
        if missing:
            before=diagnosticar(c)
            current={(r['table_name'],r['column_name']) for r in before['colunas']}
        old=snapshot(c)
        if esperado is not None and old!=esperado:
            raise ValueError('Estado mudou após o backup; migração cancelada sob lock. Gere e teste nova cópia.')
        from .compatibilidade import conferir_publicacoes_legadas, conferir_selecao
        conferir_publicacoes_legadas(c, current)
        conferir_selecao(c, current)
        acls=c.execute('''SELECT cl.relname,pg_get_userbyid(cl.relowner) AS owner,
                CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
                a.privilege_type,a.is_grantable
            FROM pg_class cl LEFT JOIN LATERAL aclexplode(COALESCE(cl.relacl,acldefault('r',cl.relowner))) a ON true
            WHERE cl.oid IN (SELECT to_regclass('public.'||x) FROM unnest(%s::text[]) x)''',(list(VIEWS),)).fetchall()
        for v in VIEWS: c.execute(sql.SQL('DROP VIEW IF EXISTS public.{}').format(sql.Identifier(v)))
        # Só as colunas enumeradas pelo contrato são retidas; objetos externos
        # dependentes interrompem a transação em vez de serem removidos em cascata.
        for t in TABELAS:
            for row in before['colunas']:
                if row['table_name']==t and row['column_name'] not in MODELO[t]:
                    c.execute(sql.SQL('ALTER TABLE public.{} DROP COLUMN {} RESTRICT').format(sql.Identifier(t),sql.Identifier(row['column_name'])))
        for t,f,_,_ in FKS: c.execute(sql.SQL('ALTER TABLE public.{} ALTER COLUMN {} SET NOT NULL').format(sql.Identifier(t),sql.Identifier(f)))
        # Substitui somente FKs do domínio, preservando CHECKs/PKs compatíveis.
        for row in c.execute('''SELECT conrelid::regclass::text AS t,conname FROM pg_constraint WHERE contype='f'
            AND conrelid IN (SELECT to_regclass('public.'||x) FROM unnest(%s::text[]) x)''',(list(TABELAS),)).fetchall():
            c.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT {}').format(sql.Identifier('public',row['t'].split('.')[-1]),sql.Identifier(row['conname'])))
        for t,f,p,pf in FKS:
            c.execute(sql.SQL('ALTER TABLE public.{} ADD CONSTRAINT {} FOREIGN KEY ({}) REFERENCES public.{} ({}) ON UPDATE RESTRICT ON DELETE RESTRICT').format(
                sql.Identifier(t),sql.Identifier('fk_'+t+'_'+f),sql.Identifier(f),sql.Identifier(p),sql.Identifier(pf)))
        c.execute((BASE/'objetos.sql').read_text(encoding='utf-8'))
        for v in VIEWS:
            # Default ACLs da instalação não podem ampliar os privilégios antigos.
            grants=c.execute('''SELECT DISTINCT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS role
                FROM pg_class cl CROSS JOIN LATERAL aclexplode(COALESCE(cl.relacl,acldefault('r',cl.relowner))) a
                WHERE cl.oid=to_regclass(%s)''',('public.'+v,)).fetchall()
            for g in grants:
                role=sql.SQL('PUBLIC') if g['role']=='PUBLIC' else sql.Identifier(g['role'])
                c.execute(sql.SQL('REVOKE ALL ON public.{} FROM {}').format(sql.Identifier(v),role))
            for a in [a for a in acls if a['relname']==v]:
                role=sql.SQL('PUBLIC') if a['grantee']=='PUBLIC' else sql.Identifier(a['grantee'])
                c.execute(sql.SQL('GRANT {} ON public.{} TO {}{}').format(sql.SQL(a['privilege_type']),sql.Identifier(v),role,sql.SQL(' WITH GRANT OPTION' if a['is_grantable'] else '')))
            owners={a['owner'] for a in acls if a['relname']==v}
            for owner in owners: c.execute(sql.SQL('ALTER VIEW public.{} OWNER TO {}').format(sql.Identifier(v),sql.Identifier(owner)))
        for t in ('interacoes','processamentos_interacoes'):
            seq=c.execute('SELECT pg_get_serial_sequence(%s,\'id\') AS seq',('public.'+t,)).fetchone()['seq']
            if seq is None: raise ValueError('Geração de IDs ausente: '+t)
            # setval nunca retrocede a sequência já avançada.
            maximum=c.execute(sql.SQL('SELECT COALESCE(max(id),0) AS n FROM public.{}').format(sql.Identifier(t))).fetchone()['n']
            last=c.execute(sql.SQL('SELECT last_value FROM {}').format(sql.Identifier(*seq.split('.')))).fetchone()['last_value']
            c.execute('SELECT setval(%s,%s,true)',(seq,max(last,maximum,1)))
        after=validar(c)
        if old!=snapshot(c): raise ValueError('Campos mantidos divergiram por PK; rollback integral.')
        if before['politicas']!=after['politicas']: raise ValueError('Políticas RLS divergiram; rollback.')
        for b in before['seguranca']:
            if b['relname'] in TABELAS and b not in after['seguranca']: raise ValueError('ACL/RLS/proprietário de tabela alterado; rollback.')
        return {'migracao':MIGRACAO,'contagens':after['contagens'],'comparacao_por_pk':'iguais: todos os 60 campos, inclusive textos/JSONBs','diagnostico':after}


def binario(name):
    base=os.getenv('PG_BIN','')
    return str(Path(base)/(name+'.exe' if os.name=='nt' else name)) if base else name


def pg_env(dsn):
    config=conninfo_to_dict(dsn)
    env=os.environ.copy()
    for k,v in config.items():
        mapping={'host':'PGHOST','port':'PGPORT','dbname':'PGDATABASE','user':'PGUSER','password':'PGPASSWORD','sslmode':'PGSSLMODE','sslrootcert':'PGSSLROOTCERT'}
        if k in mapping: env[mapping[k]]=v
    return env


def backup(dsn, directory, *, fase):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dump=directory/f'interamed_{fase}_{stamp}.dump'
    # Exporta o schema public inteiro para incluir funções/views/sequências e
    # recuperação de módulos legados; auth/storage não fazem parte deste escopo.
    with psycopg.connect(dsn,autocommit=True,row_factory=dict_row) as c, c.transaction():
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if fase=='final': validar(c)
        snap=c.execute('SELECT pg_export_snapshot() AS s').fetchone()['s']
        metadata={'origem':destino(dsn),'fase':fase,'data_utc':stamp,'snapshot_formato':3,'escopo':'PostgreSQL: schema public; sem auth/storage/serviços Supabase',
            'migracao':MIGRACAO if fase=='final' else None,'diagnostico':diagnosticar(c),'registros_por_pk':snapshot(c)}
        args=[binario('pg_dump'),'--format=custom','--schema=public','--no-password','--snapshot='+snap,'--file='+str(dump)]
        result=subprocess.run(args,env=pg_env(dsn),capture_output=True)
        if result.returncode: raise RuntimeError('pg_dump falhou; confira versão, permissões e acesso. Nenhum backup final validado.')
    metadata['sha256']=hashlib.file_digest(dump.open('rb'),'sha256').hexdigest()
    manifest=dump.with_suffix('.json'); manifest.write_text(json.dumps(metadata,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return manifest


def restaurar(dsn,manifest):
    manifest=Path(manifest); info=json.loads(manifest.read_text(encoding='utf-8')); dump=manifest.with_suffix('.dump')
    if info.get('snapshot_formato')!=3: raise ValueError('Formato de manifesto incompatível com a conferência exata atual. Use um manifesto gerado por esta versão.')
    config=conninfo_to_dict(dsn)
    if 'test' not in config.get('dbname','').lower() or config.get('host') not in ('127.0.0.1','localhost'):
        raise ValueError('Restauração só aceita banco local com test no nome.')
    if hashlib.file_digest(dump.open('rb'),'sha256').hexdigest()!=info['sha256']: raise ValueError('Dump diverge do manifesto.')
    with psycopg.connect(dsn,autocommit=True,row_factory=dict_row) as c:
        if c.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname='public'").fetchone()['n']:
            raise ValueError('Destino de restauração deve estar vazio.')
        # CREATE DATABASE já fornece public. O dump de schema o recria.
        # RESTRICT recusa inclusive funções/sequências preexistentes; sem CASCADE.
        c.execute('DROP SCHEMA IF EXISTS public RESTRICT')
    result=subprocess.run([binario('pg_restore'),'--no-owner','--no-privileges','--exit-on-error','--dbname='+config['dbname'],str(dump)],env=pg_env(dsn),capture_output=True)
    if result.returncode: raise RuntimeError('pg_restore falhou; confira extensões/versões/roles exigidos. Destino parcial somente no banco isolado.')
    with psycopg.connect(dsn,autocommit=True,row_factory=dict_row) as c:
        if snapshot(c)!=info['registros_por_pk']: raise ValueError('Restauração divergente por PK.')
        if info['fase']=='final': validar(c)
    return {'restauracao':'validada por PK','origem':info['origem'],'backup_sha256':info['sha256'],'destino_teste':destino(dsn)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('acao',choices=['diagnosticar','inicializar','migrar','validar','backup-seguranca','backup-final','restaurar'])
    p.add_argument('--env',type=Path); p.add_argument('--saida',type=Path,default=Path('tmp/readequacao/operacao'))
    p.add_argument('--manifesto',type=Path); p.add_argument('--confirmar-destino'); p.add_argument('--janela-manutencao',action='store_true')
    p.add_argument('--copia-validada',type=Path)
    args=p.parse_args(); dsn=(dotenv_values(args.env).get('DATABASE_URL') if args.env else os.getenv('DATABASE_URL'))
    if not dsn: p.error('Configure DATABASE_URL no ambiente ou arquivo --env; não informe segredos nos argumentos.')
    target=destino(dsn); print(json_texto({'destino':target}))
    args.saida.mkdir(parents=True,exist_ok=True)
    try:
        if args.acao.startswith('backup-'):
            if args.acao=='backup-final':
                with psycopg.connect(dsn,autocommit=True,row_factory=dict_row) as c: validar(c)
            result={'manifesto':str(backup(dsn,args.saida,fase='final' if args.acao=='backup-final' else 'seguranca'))}
        elif args.acao=='restaurar':
            if not args.manifesto: p.error('--manifesto obrigatório')
            result=restaurar(dsn,args.manifesto)
        else:
            with psycopg.connect(dsn,autocommit=True,row_factory=dict_row,connect_timeout=10) as c:
                if args.acao=='diagnosticar': result=diagnosticar(c)
                elif args.acao=='validar': result=validar(c)
                elif args.acao=='inicializar': inicializar(c); result=validar(c)
                else:
                    local=target['host'] in ('localhost','127.0.0.1') and 'test' in target['dbname'].lower()
                    if not local:
                        if target['host'].endswith('.pooler.supabase.com') and not target.get('projeto'):
                            raise ValueError('Pooler Supabase sem projeto identificável no usuário; confirme a configuração do destino.')
                        expected=(target.get('projeto') or target['host'])+'/'+target['dbname']
                        if args.confirmar_destino!=expected or not args.janela_manutencao or not args.manifesto or not args.copia_validada:
                            p.error('Migração remota exige destino projeto/dbname (ou host/dbname) confirmado, manutenção, manifesto de segurança e relatório de cópia validada.')
                        manifest=json.loads(args.manifesto.read_text(encoding='utf-8'))
                        replica=json.loads(args.copia_validada.read_text(encoding='utf-8'))
                        if manifest['origem']!=target or manifest['registros_por_pk']!=snapshot(c): raise ValueError('Backup não corresponde ao estado atual; gere e valide uma nova cópia.')
                        if replica.get('backup_sha256')!=manifest['sha256'] or replica.get('migracao')!=MIGRACAO or not replica.get('testes_api_aprovados'):
                            raise ValueError('Relatório da cópia não comprova migração e testes API sobre este backup.')
                    result=migrar(c,esperado=None if local else manifest['registros_por_pk'])
        out=args.saida/(args.acao+'.json'); out.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print('Concluído. Relatório: '+str(out))
        return 0
    except (ValueError,RuntimeError,psycopg.Error) as exc:
        # Erros de conexão podem conter informações do DSN: não os imprime.
        print('Falha: '+(str(exc) if not isinstance(exc,psycopg.Error) else type(exc).__name__+'; consulte configuração/permissões do destino.'))
        return 2


if __name__=='__main__': raise SystemExit(main())
