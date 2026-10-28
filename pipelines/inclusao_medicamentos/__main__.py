"""Trabalhador único, reservado no PostgreSQL; filhos liberam pesos ao terminar."""
import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from pipelines.identificacao_interacoes.modelos import chave_bloqueio
from .estado import MENSAGENS
from .repositorio import RepositorioSolicitacoes, ErroSolicitacao
from .orquestrador import Orquestrador, FalhaTemporaria, ExigeRevisao, PossePerdida

BASE = Path(__file__).resolve().parent
RAIZ = BASE.parents[1]
LOCK_WORKER = chave_bloqueio('interamed:trabalhador:inclusao:unico')


def versao_codigo():
    """Uma alteração encerra o trabalhador entre pedidos; a API inicia a versão nova."""
    arquivos = [*BASE.glob('*.py'), *BASE.glob('.env'),
        *(RAIZ / 'pipelines/scraping_anvisa/anvisa_scraper').glob('*.py')]
    return tuple((str(p), p.stat().st_mtime_ns) for p in sorted(arquivos))


def executar_filho(etapa, nome, *, dsn, log, confirmar_lock, timeout=3600):
    import psutil
    executavel = os.getenv('WORKER_PYTHON_' + etapa.upper(), sys.executable)
    # Só o caminho do resultado é temporário; logs e checkpoints ficam preservados.
    with tempfile.TemporaryDirectory(prefix='interamed_etapa_') as pasta, log.open('a', encoding='utf-8') as saida:
        resultado = Path(pasta) / 'resultado.json'
        ambiente = {**os.environ, 'DATABASE_URL': dsn, 'PYTHONIOENCODING': 'utf-8'}
        filho = subprocess.Popen([executavel, '-u', '-m', 'pipelines.inclusao_medicamentos.etapas',
            etapa, nome, '--resultado', str(resultado)], cwd=RAIZ, env=ambiente, stdout=saida,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            processo = psutil.Process(filho.pid)
        except psutil.NoSuchProcess:
            processo = None
        inicio = time.monotonic()
        try:
            while filho.poll() is None:
                confirmar_lock()
                if time.monotonic() - inicio > timeout:
                    raise FalhaTemporaria('Tempo limite da etapa excedido.')
                time.sleep(1)
            if not resultado.exists():
                raise RuntimeError('A etapa encerrou sem resultado. Consulte o log do trabalhador.')
            dados = json.loads(resultado.read_text(encoding='utf-8'))
            if dados['status'] == 'REVISAO_NECESSARIA':
                raise ExigeRevisao(dados.get('erro', 'Revisão necessária.'))
            if filho.returncode or dados['status'] != 'CONCLUIDO':
                if dados.get('temporario'):
                    raise FalhaTemporaria(dados.get('erro', 'Falha temporária na etapa.'))
                raise RuntimeError(dados.get('erro', 'Etapa não concluída.'))
        finally:
            if filho.poll() is None:
                # Resumos usa seu próprio worker de inferência. Encerrar também
                # os descendentes desta chamada evita deixar pesos órfãos na RAM.
                try:
                    descendentes = processo.children(recursive=True) if processo else []
                except psutil.Error:
                    descendentes = []
                for descendente in descendentes:
                    try:
                        descendente.terminate()
                    except psutil.Error:
                        pass
                filho.terminate()
                try:
                    filho.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    filho.kill()
                    filho.wait()
                _, vivos = psutil.wait_procs(descendentes, timeout=3)
                for descendente in vivos:
                    try:
                        descendente.kill()
                    except psutil.Error:
                        pass


def tratar_pedido(repo, pedido, processar, max_tentativas=3, confirmar_posse=lambda: None):
    from pipelines.scraping_anvisa.anvisa_scraper.erros import (
        BloqueioAnvisaError, MedicamentoNaoEncontradoError, SemBulaProfissionalError, DataPublicacaoInvalidaError,
        LimiteRequisicoesAnvisaError, RespostaAnvisaError)
    try:
        processar(pedido)
    except PossePerdida:
        raise  # Não sobrescrever o estado de uma reserva retomada por outro processo.
    except MedicamentoNaoEncontradoError:
        confirmar_posse()
        repo.atualizar(pedido['id'], estado='NAO_ENCONTRADO', etapa='NAO_ENCONTRADO', mensagem=MENSAGENS['NAO_ENCONTRADO'])
    except (ExigeRevisao, SemBulaProfissionalError, DataPublicacaoInvalidaError) as erro:
        confirmar_posse()
        logging.warning('Solicitação %s requer revisão: %s', pedido['id'], erro)
        repo.atualizar(pedido['id'], estado='REVISAO_NECESSARIA', mensagem=MENSAGENS['REVISAO_NECESSARIA'], erro=str(erro))
    except BloqueioAnvisaError as erro:
        confirmar_posse()
        logging.exception('Acesso à Anvisa bloqueado na solicitação %s', pedido['id'])
        repo.atualizar(pedido['id'], estado='FALHA', mensagem=MENSAGENS['BLOQUEIO_ANVISA'], erro=str(erro))
    except Exception as erro:
        confirmar_posse()
        logging.exception('Falha na solicitação %s', pedido['id'])
        temporario = isinstance(erro, (FalhaTemporaria, TimeoutError, LimiteRequisicoesAnvisaError))
        temporario |= isinstance(erro, RespostaAnvisaError) and getattr(erro, 'status_http', None) in (0, 408, 500, 502, 503, 504)
        # Selenium expõe TimeoutException sem herdar TimeoutError.
        temporario |= type(erro).__name__ == 'TimeoutException'
        temporario |= isinstance(erro, ErroSolicitacao) and erro.status_http == 503
        if temporario and pedido['tentativas'] < max_tentativas:
            espera = max(30 * pedido['tentativas'], getattr(erro, 'espera', 0), getattr(erro, 'retry_after_segundos', 0) or 0)
            repo.reagendar(pedido['id'], espera, str(erro))
        else:
            repo.atualizar(pedido['id'], estado='FALHA', mensagem=MENSAGENS['FALHA'], erro=str(erro))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepara medicamentos solicitados pela interface, um por vez.')
    parser.add_argument('--uma-vez', action='store_true', help='Atende no máximo uma solicitação, útil para o piloto.')
    parser.add_argument('--solicitacao', type=UUID, help='Atende somente este identificador, útil para um piloto isolado.')
    args = parser.parse_args(argv)
    load_dotenv(BASE / '.env')
    load_dotenv(RAIZ / 'pipelines/identificacao_interacoes/.env')
    dsn = os.getenv('WORKER_DATABASE_URL') or os.getenv('DATABASE_URL', '')
    if not dsn:
        print('Configure WORKER_DATABASE_URL ou DATABASE_URL no ambiente do trabalhador.')
        return 2
    try:
        max_tentativas = int(os.getenv('WORKER_MAX_TENTATIVAS', '3'))
        intervalo = float(os.getenv('WORKER_INTERVALO_SEGUNDOS', '5'))
        timeout = int(os.getenv('WORKER_ETAPA_TIMEOUT_SEGUNDOS', '3600'))
    except ValueError:
        parser.error('Tentativas, intervalo e timeout devem conter números válidos.')
    modo_headless = os.getenv('WORKER_HEADLESS', 'false').strip().lower()
    if modo_headless not in ('true', '1', 'false', '0'):
        parser.error('WORKER_HEADLESS deve conter true ou false.')
    if not 1 <= max_tentativas <= 5 or intervalo < 1 or timeout < 1:
        parser.error('Configure tentativas entre 1 e 5 e intervalos positivos.')
    logs = BASE / 'logs'
    logs.mkdir(exist_ok=True)
    logging.basicConfig(filename=logs / 'trabalhador.log', encoding='utf-8', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    repo = RepositorioSolicitacoes(dsn, os.getenv('EMBEDDING_MODEL_ID', 'intfloat/multilingual-e5-small'))
    from .coleta import Coletor
    versao_inicial = versao_codigo()
    with repo.conectar() as reserva:
        if not reserva.execute('SELECT pg_try_advisory_lock(%s) AS ok', (LOCK_WORKER,)).fetchone()['ok']:
            print('Já existe um trabalhador ativo neste banco. Nenhum trabalho foi duplicado.')
            return 0
        repo.recuperar(reserva, max_tentativas)
        while True:
            if versao_codigo() != versao_inicial:
                logging.info('Código/configuração alterados; encerrando entre pedidos para carregar a nova versão.')
                return 0
            pedido = repo.reservar(reserva, max_tentativas, args.solicitacao)
            if pedido:
                print(f"Solicitação {pedido['id']}: {pedido['nome_solicitado']}", flush=True)
                coletor = Coletor(repo,
                    Path(os.getenv('WORKER_CHROME_PROFILE', str(RAIZ / 'pipelines/scraping_anvisa/.chrome-profile-anvisa'))),
                    Path(os.getenv('WORKER_PDFS_DIR', str(RAIZ / 'pipelines/scraping_anvisa/pdfs'))),
                    headless=modo_headless in ('true', '1'))
                def confirmar_posse():
                    try:
                        atual = reserva.execute('SELECT estado, tentativas FROM solicitacoes_medicamentos WHERE id = %s', (pedido['id'],)).fetchone()
                        if not atual or atual['estado'] != 'EM_PROCESSAMENTO' or atual['tentativas'] != pedido['tentativas']:
                            raise PossePerdida('A reserva deixou de pertencer a este trabalhador.')
                    except PossePerdida:
                        raise
                    except Exception as erro:
                        raise PossePerdida('A conexão de reserva foi perdida; reinicie o trabalhador para retomar.') from erro
                coletor.confirmar_posse = confirmar_posse
                def executar(etapa, nome):
                    coletor.fechar()  # navegador e modelos não ficam carregados juntos
                    executar_filho(etapa, nome, dsn=dsn, log=logs / f'{pedido["id"]}.log',
                        confirmar_lock=confirmar_posse, timeout=timeout)
                try:
                    tratar_pedido(repo, pedido, Orquestrador(repo, coletor, executar, confirmar_posse).processar,
                                  max_tentativas, confirmar_posse)
                except PossePerdida:
                    logging.exception('Trabalhador encerrado após perder a reserva.')
                    print('Reserva perdida; reinicie o trabalhador para retomar com segurança.', flush=True)
                    return 2
                finally:
                    coletor.fechar()
                print('Situação: ' + repo.obter(pedido['id'])['estado'], flush=True)
            if args.uma_vez:
                return 0
            time.sleep(intervalo)


if __name__ == '__main__':
    raise SystemExit(main())
