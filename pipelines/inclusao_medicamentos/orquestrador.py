"""Coordenação sequencial das etapas existentes, com dependências injetáveis."""
import hashlib
from time import perf_counter

from .estado import MENSAGENS, cadastro_valido, extracao_valida, pronto, vetores_compativeis, mesma_bula


class FalhaTemporaria(Exception):
    def __init__(self, mensagem, espera=30):
        super().__init__(mensagem)
        self.espera = espera


class ExigeRevisao(Exception):
    pass


class PossePerdida(Exception):
    """A conexão de reserva caiu; este processo não pode continuar escrevendo."""


class Orquestrador:
    def __init__(self, repositorio, coletor, executar, confirmar_posse=lambda: None):
        self.repo, self.coletor, self.executar = repositorio, coletor, executar
        self.confirmar_posse = confirmar_posse

    def processar(self, pedido):
        identificador = pedido['id']
        progresso = dict(pedido.get('progresso') or {})

        def etapa(nome):
            self.confirmar_posse()
            self.repo.atualizar(identificador, etapa=nome, mensagem=MENSAGENS[nome])

        self.confirmar_posse()
        etapa('ANVISA')
        bula = self.coletor.confirmar(pedido)
        if bula is None:  # alternativa ambígua: seleção é feita pelo usuário
            return
        self.confirmar_posse()
        principal = self.repo.confirmar(identificador, bula)
        if principal != identificador:
            return
        nome = bula['nome_normalizado']
        fonte = self.repo.fonte(nome)
        if not pronto(fonte, self.repo.modelo_vetores) or not mesma_bula(fonte, bula):
            etapa('DOWNLOAD')
            self.coletor.baixar(bula, fonte)
            self.confirmar_posse()
            fonte = self.repo.fonte(nome)
            if not extracao_valida(fonte) or not cadastro_valido(fonte):
                etapa('EXTRACAO')
                self.executar('extracao', nome)
                fonte = self.repo.fonte(nome)
            if not extracao_valida(fonte) or not cadastro_valido(fonte):
                raise ExigeRevisao('Extração ou composição sem confirmação.')
            if fonte['status_extracao'] != 'SEM_SECAO_INTERACOES':
                if not fonte.get('status_identificacao'):
                    etapa('IDENTIFICACAO')
                    self.executar('identificacao', nome)
                    fonte = self.repo.fonte(nome)
                if fonte.get('status_identificacao') not in ('CONCLUIDO_COM_RELACOES', 'CONCLUIDO_SEM_RELACOES'):
                    raise ExigeRevisao('Identificação não concluída.')
            # Vínculos anteriores são recalculados somente para alvos correspondentes ao novo cadastro.
            assinatura = hashlib.sha256(repr((fonte.get('sha256_pdf'), fonte.get('principios_ativos'),
                fonte.get('composicao'), fonte.get('processamento_id'))).encode()).hexdigest()
            if progresso.get('VINCULOS', {}).get('fonte') != assinatura:
                etapa('VINCULOS')
                inicio = perf_counter()
                self.executar('vinculos', nome)
                progresso['VINCULOS'] = {'fonte': assinatura, 'segundos': round(perf_counter() - inicio, 3)}
                self.repo.atualizar(identificador, progresso=progresso)
            if fonte['status_extracao'] != 'SEM_SECAO_INTERACOES':
                if not vetores_compativeis(fonte, self.repo.modelo_vetores):
                    etapa('VETORES')
                    inicio = perf_counter()
                    self.executar('vetores', nome)
                    progresso['VETORES'] = {'segundos': round(perf_counter() - inicio, 3)}
                    self.repo.atualizar(identificador, progresso=progresso)
                fonte = self.repo.fonte(nome)
                if fonte['status_identificacao'] == 'CONCLUIDO_COM_RELACOES' and not fonte['resumos_completos']:
                    etapa('RESUMOS')
                    self.executar('resumos', nome)
        fonte = self.repo.fonte(nome)
        if not pronto(fonte, self.repo.modelo_vetores):
            raise RuntimeError('As etapas não produziram resultados completos e compatíveis.')
        self.confirmar_posse()
        self.repo.atualizar(identificador, estado='CONCLUIDO', etapa='CONCLUIDO', mensagem=MENSAGENS['CONCLUIDO'])
