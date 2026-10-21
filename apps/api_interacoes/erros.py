class ErroConsulta(Exception):
    """Erro conhecido da API; a mensagem é segura para o cliente."""

    status_http = 400
    codigo = "requisicao_invalida"

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


class ParametroInvalidoError(ErroConsulta):
    status_http = 422
    codigo = "parametro_invalido"


class MedicamentoNaoEncontradoError(ErroConsulta):
    status_http = 404
    codigo = "medicamento_nao_encontrado"


class BancoIndisponivelError(ErroConsulta):
    status_http = 503
    codigo = "banco_indisponivel"


class EsquemaAusenteError(ErroConsulta):
    status_http = 503
    codigo = "esquema_ausente"
