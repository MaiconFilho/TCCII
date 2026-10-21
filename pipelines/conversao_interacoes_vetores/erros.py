class ErroConversaoVetores(Exception):
    """Erro conhecido e tratável durante a vetorização."""


class SemTextoError(ErroConversaoVetores):
    pass


class ErroChunkingError(ErroConversaoVetores):
    pass


class ModeloInvalidoError(ErroConversaoVetores):
    pass


class ErroCarregamentoModeloError(ModeloInvalidoError):
    pass


class DimensaoInvalidaError(ErroConversaoVetores):
    pass


class VetorInvalidoError(DimensaoInvalidaError):
    """Vetor com NaN, infinito ou norma nula."""


class ErroInferenciaError(ErroConversaoVetores):
    pass


class LimiteMemoriaError(ErroConversaoVetores):
    pass


class ErroBancoError(ErroConversaoVetores):
    pass


class EsquemaBancoIncompativelError(ErroBancoError):
    pass


class ExtensaoVectorIndisponivelError(ErroBancoError):
    """A extensão pgvector não está instalada no servidor PostgreSQL."""
