"""Estados públicos e verificações de fonte para a execução/retomada do trabalhador."""
import re
import hashlib
import unicodedata

ATIVOS = ('AGUARDANDO', 'EM_PROCESSAMENTO', 'AGUARDANDO_SELECAO')
FINAIS = ('CONCLUIDO', 'NAO_ENCONTRADO', 'FALHA', 'REVISAO_NECESSARIA')
MENSAGENS = {
    'AGUARDANDO': 'Aguardando processamento.',
    'ANVISA': 'Consultando a Anvisa.',
    'DOWNLOAD': 'Baixando a bula profissional.',
    'EXTRACAO': 'Extraindo as informações.',
    'IDENTIFICACAO': 'Identificando as interações.',
    'VINCULOS': 'Associando os medicamentos citados.',
    'VETORES': 'Gerando os vetores.',
    'RESUMOS': 'Gerando os resumos.',
    'CONCLUIDO': 'Medicamento disponível para consulta.',
    'NAO_ENCONTRADO': 'Medicamento não encontrado no Bulário Eletrônico da Anvisa.',
    'AGUARDANDO_SELECAO': 'Selecione o medicamento encontrado na Anvisa para continuar.',
    'FALHA': 'Não foi possível concluir a preparação do medicamento. Tente novamente mais tarde.',
    'BLOQUEIO_ANVISA': 'Não foi possível buscar a bula no Bulário Eletrônico da Anvisa. O serviço recusou o acesso à consulta. Tente novamente mais tarde.',
    'REVISAO_NECESSARIA': 'A preparação exige revisão. Não é possível concluir sobre as interações neste momento.',
}

FALHAS_POR_ETAPA = {
    'ANVISA': 'Não foi possível buscar a bula no Bulário Eletrônico da Anvisa. Tente novamente mais tarde.',
    'DOWNLOAD': 'A bula foi localizada, mas não foi possível baixar o documento no Bulário Eletrônico da Anvisa.',
    'EXTRACAO': 'Não foi possível extrair as informações da bula deste medicamento.',
    'IDENTIFICACAO': 'Não foi possível concluir a identificação das interações na bula.',
    'VINCULOS': 'Não foi possível concluir a associação dos medicamentos citados na bula.',
    'VETORES': 'Não foi possível concluir a preparação dos trechos da bula para consulta.',
    'RESUMOS': 'Não foi possível concluir a geração dos resumos das interações.',
}


def preenchido(texto):
    return bool(texto and texto.strip())


def bula_local(fonte):
    data = fonte.get('data_publicacao_anvisa')
    return dict(nome_normalizado=fonte['nome_normalizado'], nome_produto=fonte.get('nome_anvisa') or fonte['nome_normalizado'],
                numero_registro=fonte.get('numero_registro') or '', expediente=fonte.get('expediente') or '',
                id_bula_profissional=fonte.get('id_bula_profissional') or '', id_produto=fonte.get('id_produto') or '',
                data_publicacao=data.isoformat() if data else None)


def mesma_bula(fonte, bula):
    if not fonte:
        return False
    local = bula_local(fonte)
    return all(local.get(campo) == bula.get(campo) for campo in
               ('nome_normalizado', 'numero_registro', 'expediente', 'id_bula_profissional'))


def extracao_valida(fonte):
    return bool(fonte and fonte.get('status_extracao') in ('CONCLUIDO', 'SEM_SECAO_INTERACOES')
                and fonte.get('registro_extracao') == fonte.get('numero_registro')
                and fonte.get('expediente_extracao') == fonte.get('expediente')
                and (not fonte.get('sha256_pdf_fonte') or fonte['sha256_pdf_fonte'] == fonte.get('sha256_pdf')))


def cadastro_valido(fonte):
    return bool(fonte and preenchido(fonte.get('principios_ativos')) and preenchido(fonte.get('composicao'))
                and (not fonte.get('sha256_cadastro') or fonte['sha256_cadastro'] == fonte.get('sha256_pdf')))


def vetores_compativeis(fonte, modelo):
    """Confere modelo/hash e cobertura do texto sem importar tokenizer nem pesos."""
    original = fonte.get('trecho_interacoes') or ''
    texto = re.sub(r'\s+', '', unicodedata.normalize('NFKC', original))
    linhas = fonte.get('vetores', [])
    if not texto or not linhas:
        return False
    hash_fonte = hashlib.sha256(original.encode('utf-8')).hexdigest()
    if any(l.get('fonte_sha256') and l['fonte_sha256'] != hash_fonte for l in linhas):
        return False
    fonte_confirmada = all(l.get('fonte_sha256') == hash_fonte and l.get('total_chunks_fonte') == len(linhas) for l in linhas)
    coberto = 0
    for indice, linha in enumerate(linhas):
        # Formato persistido por conversao_interacoes_vetores.hashing. Não importar
        # o pacote de vetorização na API (seu __init__ também importa o serviço).
        hash_esperado = hashlib.sha256('\x1f'.join((fonte['nome_normalizado'], str(indice),
                                                  linha['texto_chunk'], modelo)).encode('utf-8')).hexdigest()
        if linha['chunk_index'] != indice or linha['texto_hash'] != hash_esperado:
            return False
        if fonte_confirmada:
            continue
        trecho = re.sub(r'\s+', '', unicodedata.normalize('NFKC', linha['texto_chunk']))
        inicio = texto.find(trecho, max(0, coberto - len(trecho)))
        if not trecho or inicio < 0 or inicio > coberto:
            return False
        coberto = max(coberto, inicio + len(trecho))
    return fonte_confirmada or coberto == len(texto)


def pronto(fonte, modelo):
    if not fonte or fonte.get('status') != 'CONCLUIDO' or not extracao_valida(fonte) or not cadastro_valido(fonte):
        return False
    if fonte['status_extracao'] == 'SEM_SECAO_INTERACOES':
        return True  # ausência confirmada pela extração, distinta de falha/revisão
    if fonte.get('status_identificacao') == 'CONCLUIDO_SEM_RELACOES':
        return vetores_compativeis(fonte, modelo)
    return (fonte.get('status_identificacao') == 'CONCLUIDO_COM_RELACOES'
            and fonte.get('resumos_completos', False) and vetores_compativeis(fonte, modelo))


def resposta(linha):
    """Somente dados públicos; erros técnicos e caminhos nunca saem para o navegador."""
    dados = {chave: linha.get(chave) for chave in (
        'id', 'nome_solicitado', 'nome_confirmado', 'estado', 'etapa', 'criado_em',
        'atualizado_em', 'concluido_em', 'opcoes', 'mensagem')}
    if dados['estado'] == 'FALHA':
        dados['mensagem'] = FALHAS_POR_ETAPA.get(dados['etapa'], MENSAGENS['FALHA'])
    return dados
