"""Adaptadores de um medicamento. Cada execução termina e libera seus modelos."""
import argparse
import json
import hashlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .estado import cadastro_valido, extracao_valida
from .repositorio import RepositorioSolicitacoes

RAIZ = Path(__file__).resolve().parents[2]


class RevisaoNecessaria(Exception):
    pass


def extracao(dsn, nome):
    from pipelines.extracao_interacoes.main import configuracao_do_ambiente
    from pipelines.extracao_interacoes.modelo_llm import LlamaCppProvider
    from pipelines.extracao_interacoes.modelos import BulaParaExtracao
    from pipelines.extracao_interacoes.repositorio import RepositorioInteracoes
    from pipelines.extracao_interacoes.repositorio_cadastro import RepositorioCadastro
    from pipelines.extracao_interacoes.servico import ServicoExtracaoInteracoes
    from pipelines.extracao_interacoes.cadastro import ServicoExtracaoCadastro
    from pipelines.extracao_interacoes.pipeline_cadastro import processar_cadastro
    from pipelines.caminhos import resolver_caminho

    fonte = RepositorioSolicitacoes(dsn).fonte(nome)
    precisa_texto, precisa_cadastro = not extracao_valida(fonte), not cadastro_valido(fonte)
    if not precisa_texto and not precisa_cadastro:
        return
    if (fonte.get('status_extracao') == 'REVISAO_MANUAL'
            and fonte.get('registro_extracao') == fonte['numero_registro']
            and fonte.get('expediente_extracao') == fonte.get('expediente')
            and fonte.get('sha256_pdf_fonte') in (None, fonte.get('sha256_pdf'))):
        raise RevisaoNecessaria('Extração já sinalizada para revisão.')
    config = configuracao_do_ambiente()
    config.validar()
    provedor = LlamaCppProvider.carregar(config, diretorio_cache=RAIZ / 'pipelines/extracao_interacoes/modelos_hf')
    servico = ServicoExtracaoInteracoes(provedor, config)
    repo = RepositorioInteracoes(dsn)
    try:
        bula = BulaParaExtracao(nome, fonte['numero_registro'], fonte['expediente'],
                               resolver_caminho(fonte['caminho_pdf']), fonte['caminho_pdf'])
        documentos = []
        if precisa_texto:
            resultado = servico.extrair(nome_normalizado=nome, numero_registro=bula.numero_registro,
                                       expediente=bula.expediente, caminho_pdf=bula.caminho_pdf,
                                       ao_ler_documento=documentos.append)
            status = resultado.status.value
            if status in ('CONCLUIDO', 'SEM_SECAO_INTERACOES', 'REVISAO_MANUAL'):
                repo.gravar_resultado(bula, resultado.trecho_interacoes,
                    reprocessar=fonte.get('status_extracao') is not None, status_extracao=status,
                    detalhe_revisao=resultado.detalhe_erro or None,
                    tempo_leitura_segundos=resultado.tempo_leitura_segundos,
                    tempo_inferencia_segundos=resultado.tempo_inferencia_segundos)
                repo.conexao.execute('UPDATE bulas_interacoes SET sha256_pdf_fonte = %s WHERE nome_normalizado = %s',
                                     (fonte['sha256_pdf'], nome))
            if status == 'REVISAO_MANUAL' or status == 'SECAO_SOMENTE_TITULO':
                raise RevisaoNecessaria(status)
            if status not in ('CONCLUIDO', 'SEM_SECAO_INTERACOES'):
                raise RuntimeError('Extração não concluída: ' + status)
        if precisa_cadastro:
            documento = documentos[0] if documentos else servico.leitor(bula.caminho_pdf)
            resultado = processar_cadastro(bula, documento, ServicoExtracaoCadastro(provedor, config),
                                          RepositorioCadastro(repo.conexao),
                                          reprocessar=fonte.get('sha256_cadastro') not in (None, fonte.get('sha256_pdf')))
            if resultado.status != 'CONCLUIDO':
                if resultado.status == 'REVISAO_MANUAL':
                    raise RevisaoNecessaria(resultado.detalhe)
                raise RuntimeError('Cadastro não concluído: ' + resultado.status)
            repo.conexao.execute('UPDATE bulas SET sha256_cadastro = %s WHERE nome_normalizado = %s AND sha256_pdf = %s',
                                 (fonte['sha256_pdf'], nome, fonte['sha256_pdf']))
    finally:
        repo.fechar()


def identificacao(dsn, nome):
    from pipelines.identificacao_interacoes.main_identificacao import main
    fonte = RepositorioSolicitacoes(dsn).fonte(nome)
    if fonte.get('status_identificacao'):
        return
    if fonte.get('ultimo_status') == 'REVISAO_NECESSARIA':
        raise RevisaoNecessaria('Identificação exige revisão.')
    codigo = main(['--nome-normalizado', nome])
    fonte = RepositorioSolicitacoes(dsn).fonte(nome)
    if fonte.get('ultimo_status') == 'REVISAO_NECESSARIA' and not fonte.get('status_identificacao'):
        raise RevisaoNecessaria('Identificação exige revisão.')
    if codigo or not fonte.get('status_identificacao'):
        if codigo == 4:
            raise TimeoutError('A etapa de identificação não conseguiu acessar os dados.')
        raise RuntimeError('Identificação não concluída.')


def vinculos(dsn, nome):
    from pipelines.identificacao_interacoes.repositorio import RepositorioIdentificacao
    from pipelines.identificacao_interacoes.resolucao import CatalogoMedicamentos
    from pipelines.identificacao_interacoes.modelos import TipoAlvo
    repo = RepositorioIdentificacao(dsn)
    try:
        registros = repo.carregar_catalogo()
        novo = CatalogoMedicamentos([m for m in registros if m.nome_normalizado == nome])
        catalogo = CatalogoMedicamentos(registros)
        linhas = repo.conexao.execute('''SELECT i.id, i.alvo, i.tipo_alvo, i.processamento_id, i.medicamento_origem
            FROM interacoes i JOIN processamentos_interacoes_vigentes v ON v.id = i.processamento_id
            WHERE i.tipo_alvo IN ('PRINCIPIO_ATIVO', 'MEDICAMENTO')''').fetchall()
        afetados = {}
        for linha in linhas:
            if novo.resolver(linha['alvo'], TipoAlvo(linha['tipo_alvo']), linha['medicamento_origem']):
                chave = (linha['processamento_id'], linha['medicamento_origem'])
                afetados.setdefault(chave, {})[linha['id']] = catalogo.resolver(
                    linha['alvo'], TipoAlvo(linha['tipo_alvo']), linha['medicamento_origem'])
        for (pid, origem), desejados in afetados.items():
            repo.atualizar_vinculos(pid, origem, desejados)
    finally:
        repo.fechar()


def vetores(dsn, nome):
    from pipelines.conversao_interacoes_vetores.main_embeddings import configuracao_do_ambiente
    from pipelines.conversao_interacoes_vetores.modelo_embeddings import ProvedorEmbeddingsTransformers
    from pipelines.conversao_interacoes_vetores.servico import ServicoEmbeddings
    from pipelines.conversao_interacoes_vetores.repositorio import RepositorioEmbeddings
    repo = RepositorioEmbeddings(dsn)
    try:
        config = configuracao_do_ambiente()
        config.validar()
        provedor = ProvedorEmbeddingsTransformers.carregar(config,
            diretorio_cache=RAIZ / 'pipelines/conversao_interacoes_vetores/modelos_hf')
        repo.garantir_extensao_vector()
        repo.garantir_tabela(provedor.dimensao)
        repo.conexao.execute('ALTER TABLE bulas_interacoes_embeddings ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT true')
        repo.conexao.execute('ALTER TABLE bulas_interacoes_embeddings ADD COLUMN IF NOT EXISTS fonte_sha256 TEXT, ADD COLUMN IF NOT EXISTS total_chunks_fonte INTEGER')
        servico = ServicoEmbeddings(provedor, config)
        fonte = RepositorioSolicitacoes(dsn).fonte(nome)
        existentes = repo.conexao.execute('''SELECT chunk_index, texto_hash FROM bulas_interacoes_embeddings
            WHERE nome_normalizado = %s AND ativo''', (nome,)).fetchall()
        resultado = servico.gerar_para_interacao(nome, fonte['trecho_interacoes'],
            hashes_existentes={r['chunk_index']: r['texto_hash'] for r in existentes})
        if resultado.status.value in ('CONCLUIDO', 'REPROCESSADO', 'IGNORADO_JA_EXISTENTE'):
            with repo.conexao.transaction():
                atual = repo.conexao.execute('SELECT trecho_interacoes FROM bulas_interacoes WHERE nome_normalizado = %s FOR UPDATE', (nome,)).fetchone()
                if atual['trecho_interacoes'] != fonte['trecho_interacoes']:
                    raise RuntimeError('Fonte alterada durante a vetorização.')
                if resultado.status.value != 'IGNORADO_JA_EXISTENTE':
                    repo.gravar_embeddings(nome, resultado.dimensao, resultado.chunks, preservar_sobras=True)
                total = len(resultado.chunks) if resultado.chunks else len(existentes)
                repo.conexao.execute('''UPDATE bulas_interacoes_embeddings SET fonte_sha256 = %s, total_chunks_fonte = %s
                    WHERE nome_normalizado = %s AND ativo''',
                    (hashlib.sha256(fonte['trecho_interacoes'].encode('utf-8')).hexdigest(), total, nome))
        else:
            raise RuntimeError('Vetorização não concluída: ' + resultado.status.value)
    finally:
        repo.fechar()


def resumos(dsn, nome):
    from pipelines.resumos_interacoes.__main__ import main
    if main(['--nome-normalizado', nome]):
        raise RuntimeError('Geração de resumos não concluída.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('etapa', choices=('extracao', 'identificacao', 'vinculos', 'vetores', 'resumos'))
    parser.add_argument('nome')
    parser.add_argument('--resultado', required=True, type=Path)
    args = parser.parse_args()
    pastas = {'extracao': 'extracao_interacoes', 'identificacao': 'identificacao_interacoes',
              'vinculos': 'identificacao_interacoes', 'vetores': 'conversao_interacoes_vetores', 'resumos': 'resumos_interacoes'}
    load_dotenv(RAIZ / 'pipelines' / pastas[args.etapa] / '.env')
    load_dotenv(RAIZ / 'pipelines/identificacao_interacoes/.env')
    resultado = {'status': 'CONCLUIDO'}
    try:
        globals()[args.etapa](os.environ['DATABASE_URL'], args.nome)
    except RevisaoNecessaria as erro:
        resultado = {'status': 'REVISAO_NECESSARIA', 'erro': str(erro)}
    except Exception as erro:
        logging.exception('Falha na etapa %s de %s', args.etapa, args.nome)
        import psycopg
        causa, temporario = erro, False
        for _ in range(6):
            if causa is None:
                break
            temporario |= isinstance(causa, (TimeoutError, psycopg.OperationalError))
            causa = causa.__cause__
        resultado = {'status': 'FALHA', 'erro': type(erro).__name__ + ': ' + str(erro), 'temporario': temporario}
    args.resultado.write_text(json.dumps(resultado, ensure_ascii=False), encoding='utf-8')
    return 0 if resultado['status'] == 'CONCLUIDO' else 1


if __name__ == '__main__':
    raise SystemExit(main())
