"""Pré-condições do modelo anterior, executadas ANTES de retirar suas colunas.

Não copia hashes de fonte para outra estrutura. As publicações antigas são
conferidas uma vez com a entrada original; triggers invalidam alterações futuras.
"""
from psycopg import sql


def conferir_selecao(c, campos):
    if ('processamentos_interacoes','hash_trecho') not in campos:
        return
    old={r['medicamento_origem']:r['id'] for r in c.execute('SELECT medicamento_origem,id FROM public.processamentos_interacoes_vigentes').fetchall()}
    new={r['medicamento_origem']:r['id'] for r in c.execute('''SELECT DISTINCT ON (p.medicamento_origem) p.medicamento_origem,p.id
        FROM public.processamentos_interacoes p JOIN public.bulas_interacoes bi ON bi.nome_normalizado=p.medicamento_origem
        WHERE p.invalidado_em IS NULL AND p.status IN ('CONCLUIDO_COM_RELACOES','CONCLUIDO_SEM_RELACOES')
        AND bi.status_extracao='CONCLUIDO' AND NULLIF(BTRIM(bi.trecho_interacoes),'') IS NOT NULL
        ORDER BY p.medicamento_origem,p.id DESC''').fetchall()}
    differences={k:{'antes':old.get(k),'depois':new.get(k)} for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
    if differences:
        raise ValueError('Seleção mudaria após retirar metadados da fonte; requer decisão sobre os IDs, sem invalidá-los automaticamente: '+str(differences))


def conferir_publicacoes_legadas(c, campos):
    if ('processamentos_interacoes','hash_trecho') not in campos:
        return
    from pipelines.resumos_interacoes.rastreabilidade import entrada_card,entrada_geral,simples_atual,VERSAO_MODELO_FIXO
    from pipelines.identificacao_interacoes.validacao import relacao_nao_positiva
    ps=c.execute('''SELECT p.*,p.id AS processamento_id,b.principios_ativos FROM public.processamentos_interacoes p
        JOIN public.bulas b ON b.nome_normalizado=p.medicamento_origem
        WHERE NULLIF(BTRIM(p.resumo_geral_simples),'') IS NOT NULL OR EXISTS (
            SELECT 1 FROM public.interacoes i WHERE i.processamento_id=p.id
              AND NULLIF(BTRIM(i.resumo_card_simples),'') IS NOT NULL)''').fetchall()
    invalid=[]
    for p in ps:
        interactions=c.execute('SELECT * FROM public.interacoes WHERE processamento_id=%s ORDER BY id',(p['id'],)).fetchall()
        valid=[i for i in interactions if not relacao_nao_positiva(i)]
        extras={k:p.get(k) for k in ('hash_trecho','sha256_pdf')}
        for i in interactions:
            if not (i.get('resumo_card_simples') or '').strip(): continue
            group=[j for j in valid if j['alvo']==i['alvo'] and j['tipo_alvo']==i['tipo_alvo'] and (j.get('resumo_card') or '').strip()]
            future=entrada_card({**i,'principios_ativos':p['principios_ativos'],'resumos_mesmo_alvo':group})
            control=i['reescrita_controle'] or {}
            entry=future if (control.get('publicado') or {}).get('prompt')==VERSAO_MODELO_FIXO else future | extras
            if not simples_atual(i['resumo_card_simples'],control,entry) or not simples_atual(i['resumo_card_simples'],control,future,legado_validado=True):
                invalid.append(('interacoes',i['id']))
        if (p.get('resumo_geral_simples') or '').strip():
            future=entrada_geral(p,valid)
            control=p['reescrita_controle'] or {}
            entry=future if (control.get('publicado') or {}).get('prompt')==VERSAO_MODELO_FIXO else future | extras
            if not simples_atual(p['resumo_geral_simples'],control,entry) or not simples_atual(p['resumo_geral_simples'],control,future,legado_validado=True):
                invalid.append(('processamentos_interacoes',p['id']))
    if invalid:
        raise ValueError(f'{len(invalid)} reescritas antigas não validam com a entrada original. Migração bloqueada para não republicar texto inválido: {invalid}')
