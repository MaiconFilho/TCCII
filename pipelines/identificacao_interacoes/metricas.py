"""Medições observadas; ausências são null, não estimativas de geração."""
from __future__ import annotations
import json
import time
import psutil


def memoria():
    p = psutil.Process().memory_info()
    return {'rss_mb': p.rss / 1024**2,
            'disponivel_mb': psutil.virtual_memory().available / 1024**2,
            'falhas_pagina_processo': getattr(p, 'num_page_faults', None),
            'paginacao_disco_bytes': None,
            'nota_paginacao': 'Falhas de página incluem faltas leves; não comprovam acesso ao disco.'}


def preparar_tempos(provedor):
    try:
        from llama_cpp import llama_cpp
        ctx = provedor.modelo._ctx.ctx
        llama_cpp.llama_perf_context_reset(ctx)
        return ctx
    except (ImportError, AttributeError):
        return None


def tempos_backend(ctx):
    vazio = {'tempo_entrada_segundos': None, 'tempo_geracao_segundos': None,
             'tokens_entrada_avaliados': None, 'tokens_saida_avaliados': None,
             'tokens_reaproveitados': None}
    if ctx is None:
        return vazio
    from llama_cpp import llama_cpp
    p = llama_cpp.llama_perf_context(ctx)
    # n_p_eval conta só o que foi processado: o prefixo igual ao da chamada
    # anterior fica no cache do llama.cpp e não entra no tempo de entrada.
    return {'tempo_entrada_segundos': p.t_p_eval_ms / 1000 if p.n_p_eval else None,
            'tempo_geracao_segundos': p.t_eval_ms / 1000 if p.n_eval else None,
            'tokens_entrada_avaliados': int(p.n_p_eval),
            'tokens_saida_avaliados': int(p.n_eval),
            'tokens_reaproveitados': int(p.n_reused)}


def propostas(conteudo):
    try:
        d = json.loads(conteudo)
        if 'frases' in d:
            return sum(len(f.get('alvos', f.get('relacoes', []))) for f in d['frases'])
        if 'correcoes' in d:
            return sum(len(c.get('relacoes', [])) for c in d['correcoes'])
        return len(d.get('relacoes', d.get('exemplos', d.get('resumos', []))))
    except (ValueError, TypeError, AttributeError):
        return None


def medir(provedor, medidor, mensagens, esquema, resultado, *, janela, etapa, tentativa, finalidade, motivo):
    registro = {'medicamento': resultado.nome_normalizado, 'janela': janela, 'etapa': etapa,
                'tentativa': tentativa, 'finalidade': finalidade, 'motivo_adicional': motivo,
                'memoria_antes': memoria(), 'tokens_entrada': None, 'tokens_saida': None,
                'propostas': None, 'encerramento': None}
    ctx = preparar_tempos(provedor)
    inicio = time.perf_counter()
    try:
        resposta, _ = medidor.executar(lambda: provedor.analisar(mensagens, esquema=esquema))
        registro.update(tokens_entrada=int(resposta.tokens_entrada), tokens_saida=int(resposta.tokens_saida),
                        propostas=propostas(resposta.conteudo),
                        encerramento=getattr(resposta, 'motivo_encerramento', None)
                        or ('length' if resposta.truncada else 'indisponivel'))
        return resposta
    except Exception as erro:
        registro['encerramento'] = 'erro'
        registro['erro'] = type(erro).__name__
        raise
    finally:
        registro['tempo_total_segundos'] = time.perf_counter() - inicio
        registro.update(tempos_backend(ctx))
        if registro['tempo_entrada_segundos'] is not None and registro['tempo_geracao_segundos'] is not None:
            # Amostragem com gramática JSON, detokenização e Python: o que o
            # backend não conta como avaliação de entrada nem de geração.
            registro['tempo_fora_da_avaliacao_segundos'] = max(
                0.0, registro['tempo_total_segundos'] - registro['tempo_entrada_segundos']
                - registro['tempo_geracao_segundos'])
        else:
            registro['tempo_fora_da_avaliacao_segundos'] = None
        registro['memoria_depois'] = memoria()
        resultado.metricas_chamadas.append(registro)
        resultado.chamadas_llm += 1
        resultado.tokens_entrada_total += registro['tokens_entrada'] or 0
        resultado.tokens_saida_total += registro['tokens_saida'] or 0
        resultado.tempo_inferencia_segundos += registro['tempo_total_segundos']
        # Propostas brutas: só a leitura principal de cada janela. Correções,
        # releituras e exemplos aparecem separados na agregação por finalidade.
        if etapa == 'identificacao' and finalidade == 'extracao' and not motivo:
            resultado.propostas_brutas += registro['propostas'] or 0


def _somar(valores):
    presentes = [v for v in valores if v is not None]
    return round(sum(presentes), 3) if presentes else None


def agregar(chamadas):
    """Totais por etapa/finalidade/motivo; tempos do backend só quando medidos."""
    grupos = {}
    for c in chamadas:
        chave = '/'.join(str(x) for x in (c['etapa'], c['finalidade'], c.get('motivo_adicional')) if x)
        grupos.setdefault(chave, []).append(c)
    saida = {}
    for chave, itens in sorted(grupos.items()):
        total = sum(c['tempo_total_segundos'] for c in itens)
        geracao = _somar(c.get('tempo_geracao_segundos') for c in itens)
        tokens_saida = sum(c['tokens_saida'] or 0 for c in itens)
        saida[chave] = {
            'chamadas': len(itens),
            'tentativas_extras': sum(1 for c in itens if c.get('tentativa')),
            'tokens_entrada': sum(c['tokens_entrada'] or 0 for c in itens),
            'tokens_entrada_avaliados': _somar(c.get('tokens_entrada_avaliados') for c in itens),
            'tokens_saida': tokens_saida,
            'propostas': _somar(c.get('propostas') for c in itens),
            'truncadas': sum(1 for c in itens if c.get('encerramento') == 'length'),
            'erros': sum(1 for c in itens if c.get('encerramento') == 'erro'),
            'tempo_total_segundos': round(total, 3),
            'tempo_entrada_segundos': _somar(c.get('tempo_entrada_segundos') for c in itens),
            'tempo_geracao_segundos': geracao,
            'tempo_fora_da_avaliacao_segundos': _somar(c.get('tempo_fora_da_avaliacao_segundos') for c in itens),
            # Taxa efetiva inclui leitura do prompt e gramática; não é velocidade de geração.
            'taxa_efetiva_saida_tokens_s': round(tokens_saida / total, 2) if total else None,
            'taxa_geracao_tokens_s': round(tokens_saida / geracao, 2) if geracao else None,
            'rss_max_mb': round(max(c['memoria_depois']['rss_mb'] for c in itens), 1),
            'disponivel_min_mb': round(min(c['memoria_depois']['disponivel_mb'] for c in itens), 1),
        }
    return saida
