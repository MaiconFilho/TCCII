class ErroColetaAnvisa(RuntimeError):
    """Erro esperado durante uma coleta individual."""


class BloqueioAnvisaError(ErroColetaAnvisa):
    """A Anvisa solicitou interrupção imediata por 403 ou 429."""


class RespostaAnvisaError(ErroColetaAnvisa):
    """A resposta da Anvisa não pôde ser validada."""


class MedicamentoNaoEncontradoError(ErroColetaAnvisa):
    """Nenhum resultado com nome exato foi encontrado no Bulário."""


class DataAtualizacaoInvalidaError(ErroColetaAnvisa):
    """As bulas encontradas não possuem uma data de atualização comparável."""


class SemBulaProfissionalError(ErroColetaAnvisa):
    """O resultado não contém uma bula profissional disponível."""
