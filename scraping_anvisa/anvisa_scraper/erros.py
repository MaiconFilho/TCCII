class ErroColetaAnvisa(RuntimeError):
    """Erro esperado durante uma coleta individual."""


class BloqueioAnvisaError(ErroColetaAnvisa):
    """A Anvisa solicitou interrupção imediata por 403 ou limitação persistente."""


class LimiteRequisicoesAnvisaError(ErroColetaAnvisa):
    """A Anvisa respondeu 429 e solicitou redução temporária das requisições."""

    def __init__(self, mensagem: str, retry_after_segundos: float | None = None) -> None:
        super().__init__(mensagem)
        self.retry_after_segundos = retry_after_segundos


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
