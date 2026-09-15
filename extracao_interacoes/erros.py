class ErroExtracaoInteracoes(Exception):
    """Erro conhecido e tratável durante a extração."""


class PdfInvalidoError(ErroExtracaoInteracoes):
    pass


class PdfSemTextoError(ErroExtracaoInteracoes):
    pass


class OCRIndisponivelError(ErroExtracaoInteracoes):
    pass


class ErroOCRError(ErroExtracaoInteracoes):
    pass


class PdfComAnexosError(ErroExtracaoInteracoes):
    pass


class RespostaInvalidaError(ErroExtracaoInteracoes):
    pass


class RespostaTruncadaError(RespostaInvalidaError):
    pass


class SecaoSomenteTituloError(RespostaInvalidaError):
    pass


class LimiteMemoriaError(ErroExtracaoInteracoes):
    pass


class ErroCarregamentoModeloError(ErroExtracaoInteracoes):
    pass


class ErroInferenciaError(ErroExtracaoInteracoes):
    pass


class ErroBancoError(ErroExtracaoInteracoes):
    pass


class EsquemaBancoIncompativelError(ErroBancoError):
    pass


class RegistroJaExisteError(ErroBancoError):
    pass


# Compatibilidade para consumidores da versão anterior.
ErroModeloError = ErroInferenciaError


class ContextoExcedidoError(ErroInferenciaError):
    def __init__(self, tokens_entrada: int, limite: int, reserva_saida: int) -> None:
        self.tokens_entrada = tokens_entrada
        self.limite = limite
        self.reserva_saida = reserva_saida
        super().__init__(
            f"Prompt com {tokens_entrada} tokens e reserva de {reserva_saida}; "
            f"limite de contexto: {limite}."
        )
