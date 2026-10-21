"""Cadastro independente da extração de interações, com persistência opcional."""
import json

from .cadastro import ResultadoCadastro


def processar_cadastro(bula, documento, servico, repositorio, *, reprocessar=False,
                      executar=True, somente_composicao=False):
    if documento is None:
        return ResultadoCadastro(status="ERRO_LEITURA", detalhe="PDF não disponível; consulte o erro de leitura.")
    resultado = (
        servico.extrair(documento, somente_composicao=True)
        if somente_composicao else servico.extrair(documento)
    )
    if resultado.composicao and executar:
        try:
            repositorio.gravar(bula, resultado, reprocessar=reprocessar)
            resultado.gravado = True
        except Exception as erro:
            resultado.status = "ERRO_BANCO"
            resultado.detalhe = str(erro)
    return resultado


def colunas_cadastro(resultado):
    return {
        "status_cadastro": resultado.status,
        "detalhe_cadastro": resultado.detalhe,
        "principios_ativos": resultado.principios_ativos or "",
        "caracteres_composicao": len(resultado.composicao or ""),
        "evidencias_cadastro": json.dumps(resultado.evidencias, ensure_ascii=False),
        "chamadas_llm_cadastro": resultado.chamadas_llm,
        "tempo_cadastro_segundos": round(resultado.tempo_segundos, 3),
    }
