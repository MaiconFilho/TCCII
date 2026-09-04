class ErroExtracaoInteracoes(Exception):
    """Erro conhecido e tratável durante a extração."""


class PdfInvalidoError(ErroExtracaoInteracoes):
    pass


class PdfSemTextoError(ErroExtracaoInteracoes):
    pass


class ContextoExcedidoError(ErroExtracaoInteracoes):
    def __init__(self, tokens_entrada: int, limite: int, reserva_saida: int) -> None:
        self.tokens_entrada = tokens_entrada
        self.limite = limite
        self.reserva_saida = reserva_saida
        super().__init__(
            f"O prompt possui {tokens_entrada} tokens e reserva "
            f"{reserva_saida} para a resposta, excedendo o limite {limite}."
        )


class RespostaInvalidaError(ErroExtracaoInteracoes):
    pass


class RespostaTruncadaError(RespostaInvalidaError):
    pass


class ErroModeloError(ErroExtracaoInteracoes):
    pass


class ErroBancoError(ErroExtracaoInteracoes):
    pass


class EsquemaBancoIncompativelError(ErroBancoError):
    pass


class RegistroJaExisteError(ErroBancoError):
    pass
