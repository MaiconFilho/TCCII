class ErroIdentificacaoInteracoes(Exception):
    """Erro conhecido e tratável durante a identificação das interações."""


class ErroSegmentacaoError(ErroIdentificacaoInteracoes):
    """O texto não pôde ser coberto integralmente pelas janelas."""


class ContextoExcedidoError(ErroIdentificacaoInteracoes):
    """Uma janela indivisível não cabe no contexto do modelo."""


class RespostaTruncadaError(ErroIdentificacaoInteracoes):
    """A geração atingiu o limite de tokens sem fechar o JSON."""


class RespostaInvalidaError(ErroIdentificacaoInteracoes):
    """JSON inválido ou fora do esquema, mesmo após nova tentativa."""


class ErroInferenciaError(ErroIdentificacaoInteracoes):
    pass


class LimiteMemoriaError(ErroIdentificacaoInteracoes):
    pass


class ErroBancoError(ErroIdentificacaoInteracoes):
    pass


class EsquemaBancoIncompativelError(ErroBancoError):
    pass


class FonteAlteradaError(ErroBancoError):
    """Texto ou bula mudaram entre a seleção e a gravação."""


class ProcessamentoJaExisteError(ErroBancoError):
    """Outro processo já gravou a mesma versão para a mesma fonte."""


class MedicamentoEmAndamentoError(ErroBancoError):
    """Outro processo mantém o bloqueio do medicamento no PostgreSQL."""
