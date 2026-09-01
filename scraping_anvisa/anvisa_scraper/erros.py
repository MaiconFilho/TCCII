class ErroColetaAnvisa(RuntimeError):
    """Erro esperado durante uma coleta individual."""


class BloqueioAnvisaError(ErroColetaAnvisa):
    """A Anvisa solicitou interrupção imediata por 403 ou 429."""


class RespostaAnvisaError(ErroColetaAnvisa):
    """A resposta da Anvisa não pôde ser validada."""


class MedicamentoNaoEncontradoError(ErroColetaAnvisa):
    """Nenhum resultado com nome exato foi encontrado no Bulário."""


class DataPublicacaoInvalidaError(ErroColetaAnvisa):
    """As bulas encontradas não possuem uma data de publicação comparável."""


# Compatibilidade com importações da versão 2.2.
DataAtualizacaoInvalidaError = DataPublicacaoInvalidaError


class SemBulaProfissionalError(ErroColetaAnvisa):
    """O resultado não contém uma bula profissional disponível."""
