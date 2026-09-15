"""Segunda tentativa seletiva; so grava novas extracoes concluidas."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from extracao_interacoes.erros import ErroBancoError
from extracao_interacoes.main import configuracao_do_ambiente, configurar_log
from extracao_interacoes.modelo_llm import LlamaCppProvider
from extracao_interacoes.modelos import BulaParaExtracao, StatusExtracao
from extracao_interacoes.pipeline import processar_lote
from extracao_interacoes.relatorio import RelatorioCsv
from extracao_interacoes.servico import ServicoExtracaoInteracoes

PENDENTES = {'SEM_SECAO_INTERACOES', 'REVISAO_MANUAL', 'PDF_SEM_TEXTO'}


def nomes_sem_texto(caminho):
    if caminho is None:
        return set()
    with caminho.open(encoding='utf-8-sig', newline='') as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=';')
        if not {'nome_normalizado', 'status'} <= set(leitor.fieldnames or []):
            raise ValueError('CSV sem as colunas nome_normalizado e status.')
        return {r['nome_normalizado'] for r in leitor if r['status'] == 'PDF_SEM_TEXTO'}


def elegivel(status, nome, nomes_csv):
    # O banco prevalece sobre um CSV antigo. Nunca selecionar concluido.
    return status in PENDENTES or (status is None and nome in nomes_csv)


def selecionar(conexao, nomes_csv):
    registros = conexao.execute('''
        SELECT b.nome_normalizado, b.numero_registro, b.expediente, b.caminho_pdf,
               bi.status_extracao, to_jsonb(bi) AS anterior
        FROM public.bulas b
        LEFT JOIN public.bulas_interacoes bi USING(nome_normalizado)
        WHERE b.status = 'CONCLUIDO' AND b.caminho_pdf IS NOT NULL
          AND b.numero_registro IS NOT NULL
          AND (bi.status_extracao = ANY(%s::text[])
               OR (bi.nome_normalizado IS NULL AND b.nome_normalizado = ANY(%s::text[])))
        ORDER BY b.nome_normalizado
    ''', (sorted(PENDENTES), sorted(nomes_csv))).fetchall()
    return [r for r in registros if elegivel(r['status_extracao'], r['nome_normalizado'], nomes_csv)]


def assinatura_fora_do_lote(conexao, nomes):
    return conexao.execute('''
        SELECT count(*) AS quantidade,
               md5(coalesce(string_agg(md5(to_jsonb(bi)::text), '' ORDER BY nome_normalizado), '')) AS assinatura
        FROM public.bulas_interacoes bi
        WHERE NOT (nome_normalizado = ANY(%s::text[]))
    ''', (nomes,)).fetchone()


class RepositorioRetentativa:
    def __init__(self, conexao):
        self.conexao = conexao

    def gravar_resultado(self, bula, trecho_interacoes, reprocessar=False, **dados):
        # Novas falhas ficam no CSV; nunca apagam texto ou mudam pendencia anterior.
        if dados.get('status_extracao') != StatusExtracao.CONCLUIDO.value:
            return
        if not trecho_interacoes or not trecho_interacoes.strip():
            raise ErroBancoError('Resultado concluido sem texto; gravacao recusada.')
        with self.conexao.transaction():
            cursor = self.conexao.execute('''
                INSERT INTO public.bulas_interacoes AS destino
                    (nome_normalizado, numero_registro, expediente, trecho_interacoes,
                     status_extracao, detalhe_revisao, tempo_leitura_segundos,
                     tempo_inferencia_segundos, tempo_total_segundos)
                VALUES (%s,%s,%s,%s,'CONCLUIDO',NULL,%s,%s,%s)
                ON CONFLICT (nome_normalizado) DO UPDATE SET
                    numero_registro=EXCLUDED.numero_registro,
                    expediente=EXCLUDED.expediente,
                    trecho_interacoes=EXCLUDED.trecho_interacoes,
                    status_extracao=EXCLUDED.status_extracao,
                    detalhe_revisao=NULL,
                    tempo_leitura_segundos=EXCLUDED.tempo_leitura_segundos,
                    tempo_inferencia_segundos=EXCLUDED.tempo_inferencia_segundos,
                    tempo_total_segundos=EXCLUDED.tempo_total_segundos
                WHERE destino.status_extracao = ANY(%s::text[])
            ''', (bula.nome_normalizado, bula.numero_registro, bula.expediente,
                  trecho_interacoes, dados.get('tempo_leitura_segundos'),
                  dados.get('tempo_inferencia_segundos'), dados.get('tempo_total_segundos'),
                  sorted(PENDENTES)))
            if cursor.rowcount != 1:
                raise ErroBancoError('Registro mudou desde a selecao; resultado existente preservado.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--relatorio-anterior', type=Path,
        help='CSV opcional para incluir falhas PDF_SEM_TEXTO sem registro no banco.',
    )
    parser.add_argument('--nome-normalizado')
    parser.add_argument('--executar', action='store_true', help='Sem esta opcao, apenas lista; nao grava nem carrega LLM.')
    args = parser.parse_args()
    load_dotenv(BASE/'.env')
    nomes_csv = nomes_sem_texto(args.relatorio_anterior)
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True,
                         row_factory=dict_row, connect_timeout=10) as conexao:
        registros = selecionar(conexao, nomes_csv)
        if args.nome_normalizado:
            registros = [r for r in registros if r['nome_normalizado'] == args.nome_normalizado]
        print('Selecionados:', len(registros))
        print('Status anteriores:', dict(Counter(r['status_extracao'] or 'PDF_SEM_TEXTO' for r in registros)))
        for r in registros:
            print('-', r['nome_normalizado'])
        if not args.executar or not registros:
            return 0
        # Falhar antes de qualquer gravacao, sem ocultar PDFs ausentes.
        ausentes = [r['nome_normalizado'] for r in registros if not Path(r['caminho_pdf']).is_file()]
        if ausentes:
            raise SystemExit('PDFs nao encontrados: ' + ', '.join(ausentes))
        nomes = [r['nome_normalizado'] for r in registros]
        antes = assinatura_fora_do_lote(conexao, nomes)
        pasta = BASE/'relatorios'/('segunda_tentativa_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        pasta.mkdir(parents=True, exist_ok=False)
        with (pasta/'antes.json').open('x', encoding='utf-8') as arquivo:
            json.dump({'registros': registros, 'fora_do_lote': antes}, arquivo, ensure_ascii=False, indent=2, default=str)
        print('Copia dos registros anteriores:', pasta/'antes.json', flush=True)
        configurar_log()
        cfg = configuracao_do_ambiente()
        cfg.validar()
        print('Carregando uma instancia da LLM para todo o lote...', flush=True)
        provedor = LlamaCppProvider.carregar(cfg, diretorio_cache=BASE/'modelos_hf')
        provedor.testar()
        servico = ServicoExtracaoInteracoes(provedor, cfg)
        bulas = [BulaParaExtracao(r['nome_normalizado'], r['numero_registro'], r['expediente'],
                                 Path(r['caminho_pdf']).resolve()) for r in registros]
        with RelatorioCsv(pasta/'resultado.csv') as relatorio:
            resumo = processar_lote(bulas, servico, RepositorioRetentativa(conexao), relatorio, reprocessar=True)
        depois = assinatura_fora_do_lote(conexao, nomes)
        atuais = conexao.execute('''
            SELECT nome_normalizado, status_extracao, length(trecho_interacoes) AS caracteres
            FROM public.bulas_interacoes WHERE nome_normalizado = ANY(%s::text[])
            ORDER BY nome_normalizado
        ''', (nomes,)).fetchall()
        with (pasta/'depois.json').open('x', encoding='utf-8') as arquivo:
            json.dump({'resumo': resumo, 'fora_do_lote_preservado': antes == depois,
                       'registros': atuais}, arquivo, ensure_ascii=False, indent=2, default=str)
        print('Resumo:', resumo)
        print('Registros fora do lote preservados:', antes == depois)
        print('Relatorios:', pasta)
        if antes != depois:
            raise SystemExit('Banco fora do lote mudou durante a execucao; conferir atividade concorrente.')
        return 1 if resumo.get('ERRO_BANCO') else 0


if __name__ == '__main__':
    raise SystemExit(main())
