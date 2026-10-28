"""Fila PostgreSQL e leituras de prontidão. Nenhuma dependência de scraping/LLM."""
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pipelines.identificacao_interacoes.modelos import chave_bloqueio
from pipelines.identificacao_interacoes.validacao import relacao_nao_positiva
from pipelines.scraping_anvisa.anvisa_scraper.normalizacao import normalizar_nome
from .estado import ATIVOS, MENSAGENS, resposta


class ErroSolicitacao(Exception):
    def __init__(self, mensagem, status=503):
        super().__init__(mensagem)
        self.mensagem, self.status_http = mensagem, status


class RepositorioSolicitacoes:
    def __init__(self, dsn, modelo_vetores='intfloat/multilingual-e5-small'):
        self.dsn, self.modelo_vetores = dsn, modelo_vetores

    @contextmanager
    def conectar(self):
        try:
            with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row,
                                 connect_timeout=5, options='-c statement_timeout=15000') as c:
                yield c
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as erro:
            raise ErroSolicitacao('A preparação de medicamentos ainda não está configurada. Aplique as migrações.') from erro
        except psycopg.Error as erro:
            raise ErroSolicitacao('O serviço de preparação de medicamentos está indisponível no momento.') from erro

    def fonte(self, nome, conexao=None):
        if conexao is None:
            with self.conectar() as c:
                return self.fonte(nome, c)
        c = conexao
        fonte = c.execute('''
            SELECT b.*, bi.status_extracao, bi.trecho_interacoes, bi.sha256_pdf_fonte,
                   bi.numero_registro AS registro_extracao, bi.expediente AS expediente_extracao,
                   v.id AS processamento_id, v.status AS status_identificacao,
                   r.status AS ultimo_status, p.resumo_geral,
                   (nullif(btrim(p.resumo_geral), '') IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM interacoes i WHERE i.processamento_id = v.id
                         AND coalesce(i.resumo_card, '') !~ '[^[:space:]]')) AS resumos_completos
            FROM bulas b LEFT JOIN bulas_interacoes bi USING(nome_normalizado)
            LEFT JOIN processamentos_interacoes_vigentes v ON v.medicamento_origem = b.nome_normalizado
            LEFT JOIN processamentos_interacoes_registrados r ON r.medicamento_origem = b.nome_normalizado
            LEFT JOIN processamentos_interacoes p ON p.id = v.id
            WHERE b.nome_normalizado = %s
        ''', (nome,)).fetchone()
        if fonte:
            if fonte.get('status_identificacao') == 'CONCLUIDO_COM_RELACOES':
                cards = c.execute('SELECT alvo, tipo_alvo, evidencia, resumo_card FROM interacoes WHERE processamento_id = %s',
                                  (fonte['processamento_id'],)).fetchall()
                fonte['resumos_completos'] = bool(fonte.get('resumo_geral') and fonte['resumo_geral'].strip()) and all(
                    card.get('resumo_card') and card['resumo_card'].strip() for card in cards if not relacao_nao_positiva(card))
            fonte['vetores'] = []
            if c.execute("SELECT to_regclass('public.bulas_interacoes_embeddings') AS tabela").fetchone()['tabela']:
                fonte['vetores'] = c.execute('''SELECT chunk_index, texto_chunk, texto_hash, fonte_sha256, total_chunks_fonte
                    FROM bulas_interacoes_embeddings WHERE nome_normalizado = %s AND ativo ORDER BY chunk_index''', (nome,)).fetchall()
        return fonte

    def solicitar(self, nome):
        nome = nome.strip()
        normal = normalizar_nome(nome)
        if not normal or len(nome) > 200 or not any(x.isalnum() for x in normal):
            raise ErroSolicitacao('Informe o nome do medicamento, com até 200 caracteres.', 422)
        with self.conectar() as c, c.transaction():
            c.execute('SELECT pg_advisory_xact_lock(%s)', (chave_bloqueio('solicitacao:' + normal),))
            existente = c.execute('''SELECT * FROM solicitacoes_medicamentos
                WHERE principal_id IS NULL AND (nome_normalizado = %s OR nome_confirmado = %s)
                  AND estado = ANY(%s) ORDER BY criado_em LIMIT 1''', (normal, normal, list(ATIVOS))).fetchone()
            if existente:
                return resposta(existente)
            # A API decide se precisa incluir. Não audita etapas de cadastros existentes.
            if c.execute('SELECT 1 FROM bulas WHERE nome_normalizado = %s', (normal,)).fetchone():
                return resposta(dict(id=None, nome_solicitado=nome, nome_confirmado=normal,
                    estado='CONCLUIDO', etapa='CONCLUIDO',
                    mensagem='Medicamento já cadastrado. Consulte as informações disponíveis.', opcoes=[]))
            return resposta(c.execute('''INSERT INTO solicitacoes_medicamentos
                (id, nome_solicitado, nome_normalizado, mensagem) VALUES (%s, %s, %s, %s) RETURNING *''',
                (uuid4(), nome, normal, MENSAGENS['AGUARDANDO'])).fetchone())

    def obter(self, identificador):
        with self.conectar() as c:
            for _ in range(16):
                linha = c.execute('SELECT * FROM solicitacoes_medicamentos WHERE id = %s', (identificador,)).fetchone()
                if not linha:
                    raise ErroSolicitacao('Solicitação não encontrada.', 404)
                if not linha['principal_id']:
                    return linha
                identificador = linha['principal_id']
        raise ErroSolicitacao('Não foi possível acompanhar esta solicitação.')

    def selecionar(self, identificador, nome):
        with self.conectar() as c, c.transaction():
            linha = c.execute('SELECT * FROM solicitacoes_medicamentos WHERE id = %s FOR UPDATE', (identificador,)).fetchone()
            if not linha:
                raise ErroSolicitacao('Solicitação não encontrada.', 404)
            if linha['estado'] != 'AGUARDANDO_SELECAO':
                raise ErroSolicitacao('Esta solicitação não está aguardando seleção.', 409)
            opcao = next((o for o in linha['opcoes'] if o['nome_normalizado'] == nome), None)
            if not opcao:
                raise ErroSolicitacao('Selecione uma das opções retornadas pela Anvisa.', 422)
            c.execute('''UPDATE solicitacoes_medicamentos SET bula_confirmada = %s, estado = 'AGUARDANDO',
                etapa = 'AGUARDANDO', tentativas = 0, disponivel_em = now(), atualizado_em = now(), mensagem = %s
                WHERE id = %s''', (Jsonb(opcao), MENSAGENS['AGUARDANDO'], identificador))
        return resposta(self.obter(identificador))

    def confirmar(self, identificador, bula):
        nome = bula['nome_normalizado']
        with self.conectar() as c, c.transaction():
            c.execute('SELECT pg_advisory_xact_lock(%s)', (chave_bloqueio('solicitacao:' + nome),))
            outro = c.execute('''SELECT id FROM solicitacoes_medicamentos
                WHERE id <> %s AND principal_id IS NULL AND (nome_normalizado = %s OR nome_confirmado = %s)
                  AND estado = ANY(%s) ORDER BY criado_em LIMIT 1''', (identificador, nome, nome, list(ATIVOS))).fetchone()
            if outro:
                c.execute('''UPDATE solicitacoes_medicamentos SET principal_id = %s, nome_confirmado = %s,
                    atualizado_em = now() WHERE id = %s''', (outro['id'], nome, identificador))
                return outro['id']
            c.execute('''UPDATE solicitacoes_medicamentos SET nome_confirmado = %s, bula_confirmada = %s,
                atualizado_em = now() WHERE id = %s''', (nome, Jsonb(bula), identificador))
        return identificador

    def atualizar(self, identificador, **campos):
        permitidos = {'estado', 'etapa', 'opcoes', 'bula_confirmada', 'progresso', 'mensagem', 'erro'}
        if not campos or not set(campos) <= permitidos:
            raise ValueError('Campos inválidos da solicitação.')
        valores = [Jsonb(v) if k in {'opcoes', 'bula_confirmada', 'progresso'} else v for k, v in campos.items()]
        with self.conectar() as c:
            c.execute('UPDATE solicitacoes_medicamentos SET ' + ', '.join(f'{k} = %s' for k in campos)
                      + ", atualizado_em = now(), concluido_em = CASE WHEN %s THEN now() ELSE concluido_em END WHERE id = %s",
                      (*valores, campos.get('estado') in ('CONCLUIDO', 'FALHA', 'NAO_ENCONTRADO', 'REVISAO_NECESSARIA'), identificador))

    def recuperar(self, c, max_tentativas):
        # Só é chamado após obter o lock global do trabalhador. Sessões antigas já terminaram.
        c.execute('''UPDATE solicitacoes_medicamentos SET
            estado = CASE WHEN tentativas >= %s THEN 'FALHA' ELSE 'AGUARDANDO' END,
            mensagem = CASE WHEN tentativas >= %s THEN %s ELSE %s END,
            concluido_em = CASE WHEN tentativas >= %s THEN now() ELSE concluido_em END,
            erro = 'Trabalhador interrompido; retomada limitada por tentativas.', atualizado_em = now()
            WHERE estado = 'EM_PROCESSAMENTO' AND principal_id IS NULL''',
            (max_tentativas, max_tentativas, MENSAGENS['FALHA'], MENSAGENS['AGUARDANDO'], max_tentativas))

    def reservar(self, c, max_tentativas, identificador=None):
        with c.transaction():
            return c.execute('''UPDATE solicitacoes_medicamentos SET estado = 'EM_PROCESSAMENTO',
                tentativas = tentativas + 1, iniciado_em = coalesce(iniciado_em, now()), atualizado_em = now()
                WHERE id = (SELECT id FROM solicitacoes_medicamentos WHERE estado = 'AGUARDANDO'
                    AND principal_id IS NULL AND disponivel_em <= now() AND tentativas < %s
                    AND (%s::uuid IS NULL OR id = %s)
                    ORDER BY criado_em FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *''',
                    (max_tentativas, identificador, identificador)).fetchone()

    def reagendar(self, identificador, segundos, erro):
        with self.conectar() as c:
            c.execute('''UPDATE solicitacoes_medicamentos SET estado = 'AGUARDANDO', mensagem = %s,
                erro = %s, atualizado_em = now(), disponivel_em = now() + %s * interval '1 second'
                WHERE id = %s''', (MENSAGENS['AGUARDANDO'], erro, segundos, identificador))
