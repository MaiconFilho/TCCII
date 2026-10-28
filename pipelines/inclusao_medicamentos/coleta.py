"""Sessão pública normal do scraper; nenhum contorno de bloqueio."""
import hashlib
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pipelines.scraping_anvisa.anvisa_scraper.api_anvisa import consultar_opcoes_por_nome, baixar_pdf
from pipelines.scraping_anvisa.anvisa_scraper.controle import ControleColeta
from pipelines.scraping_anvisa.anvisa_scraper.modelos import BulaLocalizada, MedicamentoParaColeta
from pipelines.scraping_anvisa.anvisa_scraper.navegador import criar_navegador, abrir_sessao_publica
from pipelines.scraping_anvisa.anvisa_scraper.pipeline import salvar_pdf_atomico, montar_nome_arquivo
from pipelines.caminhos import resolver_caminho
from .estado import MENSAGENS, mesma_bula


def serializar(bula):
    dados = asdict(bula)
    dados['data_publicacao'] = bula.data_publicacao.isoformat()
    return dados


def pdf_valido(fonte):
    if not fonte or fonte.get('status') != 'CONCLUIDO' or not fonte.get('caminho_pdf'):
        return False
    caminho = resolver_caminho(fonte['caminho_pdf'])
    if not caminho.is_file():
        return False
    conteudo = caminho.read_bytes()
    return conteudo.startswith(b'%PDF') and hashlib.sha256(conteudo).hexdigest() == fonte.get('sha256_pdf')


class Coletor:
    def __init__(self, repo, perfil, pasta_pdfs, headless=False):
        self.repo, self.perfil, self.pasta_pdfs, self.headless = repo, perfil, pasta_pdfs, headless
        self.navegador = None
        self.confirmar_posse = lambda: None

    def sessao(self):
        if self.navegador is None:
            self.navegador = criar_navegador(self.perfil, headless=self.headless)
            abrir_sessao_publica(self.navegador, aguardar_confirmacao=False)
        return self.navegador

    def fechar(self):
        if self.navegador is not None:
            try:
                self.navegador.quit()
            except Exception:
                logging.warning('Não foi possível confirmar o fechamento da sessão do scraper.', exc_info=True)
            finally:
                self.navegador = None

    def confirmar(self, pedido):
        if pedido.get('bula_confirmada'):
            return pedido['bula_confirmada']
        opcoes = consultar_opcoes_por_nome(self.sessao(),
            MedicamentoParaColeta(pedido['nome_solicitado'], pedido['nome_normalizado']))
        if len(opcoes) == 1 and opcoes[0].nome_normalizado == pedido['nome_normalizado']:
            return serializar(opcoes[0])
        self.confirmar_posse()
        self.repo.atualizar(pedido['id'], estado='AGUARDANDO_SELECAO', etapa='AGUARDANDO_SELECAO',
            opcoes=[serializar(o) for o in opcoes], mensagem=MENSAGENS['AGUARDANDO_SELECAO'])
        return None

    def baixar(self, dados, fonte):
        if pdf_valido(fonte) and mesma_bula(fonte, dados):
            return
        dados = dict(dados)
        dados['data_publicacao'] = datetime.fromisoformat(dados['data_publicacao'])
        bula = BulaLocalizada(**dados)
        conteudo = baixar_pdf(self.sessao(), bula)
        sha = hashlib.sha256(conteudo).hexdigest()
        # Versões distintas ficam em arquivos distintos; PDFs anteriores não são sobrescritos.
        arquivo = Path(montar_nome_arquivo(bula))
        destino = self.pasta_pdfs / f'{arquivo.stem}_{sha[:12]}.pdf'
        _, tamanho = salvar_pdf_atomico(conteudo, destino)
        self.confirmar_posse()
        controle = ControleColeta(self.repo.dsn)
        try:
            medicamento = MedicamentoParaColeta(bula.nome_produto, bula.nome_normalizado,
                fonte.get('quantidade_registros_planilha', 0) if fonte else 0)
            with controle.conexao.transaction():
                if fonte and fonte.get('sha256_pdf') != sha:
                    controle.conexao.execute('''UPDATE bulas SET sha256_cadastro = coalesce(sha256_cadastro, sha256_pdf, 'fonte_anterior')
                        WHERE nome_normalizado = %s''', (bula.nome_normalizado,))
                    controle.conexao.execute('''UPDATE bulas_interacoes SET sha256_pdf_fonte = coalesce(sha256_pdf_fonte, %s, 'fonte_anterior')
                        WHERE nome_normalizado = %s''', (fonte.get('sha256_pdf'), bula.nome_normalizado))
                controle.iniciar(medicamento)
                controle.finalizar(medicamento, 'CONCLUIDO', 'Bula profissional obtida sob demanda.',
                    bula=bula, caminho_pdf=str(destino.resolve()), sha256_pdf=sha, tamanho_pdf=tamanho)
        finally:
            controle.fechar()
