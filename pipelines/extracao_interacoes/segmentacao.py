from collections.abc import Callable

from .modelos import DocumentoPdf, JanelaDocumento


def criar_janelas(
    documento: DocumentoPdf,
    contar_tokens: Callable[[str], int],
    limite_tokens: int = 1800,
    sobreposicao_tokens: int = 250,
) -> list[JanelaDocumento]:
    """Divide o documento inteiro em janelas sequenciais com sobreposição."""
    if limite_tokens <= 0:
        raise ValueError("limite_tokens deve ser positivo.")
    if sobreposicao_tokens < 0 or sobreposicao_tokens >= limite_tokens:
        raise ValueError("A sobreposição deve ser menor que o limite da janela.")
    if not documento.linhas:
        return []

    tokens_por_linha = [
        max(1, contar_tokens(linha.texto_prompt)) for linha in documento.linhas
    ]
    janelas: list[JanelaDocumento] = []
    inicio = 0
    total_linhas = len(documento.linhas)

    while inicio < total_linhas:
        fim = inicio
        tokens = 0
        while fim < total_linhas:
            proximos = tokens_por_linha[fim]
            if fim > inicio and tokens + proximos > limite_tokens:
                break
            tokens += proximos
            fim += 1

        linhas = documento.linhas[inicio:fim]
        janelas.append(
            JanelaDocumento(
                indice=len(janelas),
                linhas=linhas,
                tokens_estimados=tokens,
            )
        )
        if fim >= total_linhas:
            break

        novo_inicio = fim
        acumulado = 0
        while novo_inicio > inicio + 1 and acumulado < sobreposicao_tokens:
            novo_inicio -= 1
            acumulado += tokens_por_linha[novo_inicio]
        inicio = max(inicio + 1, novo_inicio)

    return janelas
