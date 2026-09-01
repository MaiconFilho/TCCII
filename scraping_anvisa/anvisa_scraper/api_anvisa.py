import base64
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from dateutil import parser as date_parser

if TYPE_CHECKING:
    from selenium.webdriver.chrome.webdriver import WebDriver
else:
    WebDriver = Any

from .erros import (
    BloqueioAnvisaError,
    DataAtualizacaoInvalidaError,
    MedicamentoNaoEncontradoError,
    RespostaAnvisaError,
    SemBulaProfissionalError,
)
from .modelos import BulaLocalizada, MedicamentoParaColeta
from .planilha import normalizar_nome


BASE_URL = "https://consultas.anvisa.gov.br"
ENDPOINT_CONSULTA = "/api/consulta/bulario"
ENDPOINT_PDF = "/api/consulta/medicamentos/arquivo/bula/parecer/{id}/?Authorization="
QUANTIDADE_POR_PAGINA = 10
LIMITE_PAGINAS = 200


SCRIPT_FETCH_TEXTO = r"""
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {
  method: "GET",
  credentials: "include",
  headers: {
    "Accept": "application/json, text/plain, */*",
    "Authorization": "Guest"
  }
})
  .then(async (resposta) => done({
    status: resposta.status,
    contentType: resposta.headers.get("content-type") || "",
    body: await resposta.text()
  }))
  .catch((erro) => done({status: 0, error: String(erro)}));
"""


SCRIPT_FETCH_BINARIO = r"""
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {
  method: "GET",
  credentials: "include",
  headers: {
    "Accept": "application/pdf, application/force-download, */*",
    "Authorization": "Guest"
  }
})
  .then(async (resposta) => {
    const buffer = await resposta.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    const tamanhoBloco = 0x8000;
    let binario = "";
    for (let i = 0; i < bytes.length; i += tamanhoBloco) {
      const bloco = bytes.subarray(i, Math.min(i + tamanhoBloco, bytes.length));
      binario += String.fromCharCode.apply(null, bloco);
    }
    done({
      status: resposta.status,
      contentType: resposta.headers.get("content-type") || "",
      base64: btoa(binario)
    });
  })
  .catch((erro) => done({status: 0, error: String(erro)}));
"""


def _validar_status(resposta: dict[str, Any], operacao: str) -> None:
    status = int(resposta.get("status") or 0)
    if status in {403, 429}:
        raise BloqueioAnvisaError(
            f"A Anvisa respondeu HTTP {status} durante {operacao}. "
            "A execução foi interrompida para não insistir no bloqueio."
        )
    if status != 200:
        detalhe = resposta.get("error") or resposta.get("body", "")[:300]
        raise RespostaAnvisaError(
            f"Falha durante {operacao}: HTTP {status}. Detalhe: {detalhe}"
        )


def montar_url_consulta(nome_produto: str, pagina: int = 1) -> str:
    parametros = urlencode(
        {
            "columns": "",
            "count": str(QUANTIDADE_POR_PAGINA),
            "filter[nomeProduto]": nome_produto,
            "order": "asc",
            "page": str(pagina),
        }
    )
    return f"{BASE_URL}{ENDPOINT_CONSULTA}?{parametros}"


def interpretar_data_atualizacao(valor: object) -> datetime | None:
    texto = str(valor or "").strip()
    if not texto:
        return None

    if texto.isdigit() and len(texto) in {10, 13}:
        timestamp: float = int(texto)
        if len(texto) == 13:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    try:
        data = date_parser.isoparse(texto)
    except (TypeError, ValueError, OverflowError):
        try:
            data = date_parser.parse(texto, dayfirst=True)
        except (TypeError, ValueError, OverflowError):
            return None

    if data.tzinfo is None:
        return data.replace(tzinfo=timezone.utc)
    return data.astimezone(timezone.utc)


def _consultar_pagina(
    navegador: WebDriver,
    medicamento: MedicamentoParaColeta,
    pagina: int,
) -> dict[str, Any]:
    resposta = navegador.execute_async_script(
        SCRIPT_FETCH_TEXTO,
        montar_url_consulta(medicamento.nome_produto, pagina),
    )
    _validar_status(
        resposta,
        f"consulta de '{medicamento.nome_produto}' na página {pagina}",
    )

    try:
        dados = json.loads(resposta["body"])
    except (KeyError, TypeError, json.JSONDecodeError) as erro:
        raise RespostaAnvisaError("A consulta não retornou um JSON válido.") from erro
    if not isinstance(dados, dict):
        raise RespostaAnvisaError("A consulta retornou uma estrutura JSON inesperada.")
    return dados


def _somente_digitos(valor: object) -> str:
    return "".join(caractere for caractere in str(valor or "") if caractere.isdigit())


def _chave_desempate(item: dict[str, Any]) -> tuple[str, str]:
    return (
        _somente_digitos(item.get("expediente")).zfill(30),
        _somente_digitos(item.get("numeroRegistro")).zfill(30),
    )


def consultar_mais_recente_por_nome(
    navegador: WebDriver,
    medicamento: MedicamentoParaColeta,
) -> BulaLocalizada:
    primeira_pagina = _consultar_pagina(navegador, medicamento, 1)
    try:
        total_paginas = max(1, int(primeira_pagina.get("totalPages") or 1))
    except (TypeError, ValueError) as erro:
        raise RespostaAnvisaError("O total de páginas retornado é inválido.") from erro
    if total_paginas > LIMITE_PAGINAS:
        raise RespostaAnvisaError(
            f"A consulta retornou {total_paginas} páginas, acima do limite de segurança."
        )

    resultados: list[dict[str, Any]] = []
    for pagina in range(1, total_paginas + 1):
        dados = primeira_pagina if pagina == 1 else _consultar_pagina(
            navegador,
            medicamento,
            pagina,
        )
        conteudo = dados.get("content") or []
        if not isinstance(conteudo, list):
            raise RespostaAnvisaError("O campo 'content' da consulta não é uma lista.")
        resultados.extend(item for item in conteudo if isinstance(item, dict))

    exatos = [
        item
        for item in resultados
        if normalizar_nome(item.get("nomeProduto")) == medicamento.nome_normalizado
    ]
    if not exatos:
        raise MedicamentoNaoEncontradoError(
            f"Nenhum resultado com nome exato '{medicamento.nome_produto}' foi encontrado."
        )

    com_bula = [
        item
        for item in exatos
        if str(item.get("idBulaProfissionalProtegido") or "").strip()
    ]
    if not com_bula:
        raise SemBulaProfissionalError(
            f"'{medicamento.nome_produto}' não possui bula profissional disponível."
        )

    candidatas: list[tuple[datetime, tuple[str, str], dict[str, Any], str]] = []
    for item in com_bula:
        data_original = str(item.get("dataAtualizacao") or "").strip()
        data = interpretar_data_atualizacao(data_original)
        if data is not None:
            candidatas.append((data, _chave_desempate(item), item, data_original))

    if not candidatas:
        raise DataAtualizacaoInvalidaError(
            f"As bulas de '{medicamento.nome_produto}' não possuem dataAtualizacao válida."
        )

    data_atualizacao, _, resultado, data_original = max(
        candidatas,
        key=lambda candidata: (candidata[0], candidata[1]),
    )
    return BulaLocalizada(
        nome_normalizado=medicamento.nome_normalizado,
        nome_produto=str(resultado.get("nomeProduto") or medicamento.nome_produto).strip(),
        numero_registro=_somente_digitos(resultado.get("numeroRegistro")),
        expediente=_somente_digitos(resultado.get("expediente")),
        id_bula_profissional=str(resultado["idBulaProfissionalProtegido"]).strip(),
        data_atualizacao=data_atualizacao,
        data_atualizacao_original=data_original,
        id_produto=_somente_digitos(resultado.get("idProduto")),
    )


def baixar_pdf(
    navegador: WebDriver,
    bula: BulaLocalizada,
) -> bytes:
    url = f"{BASE_URL}{ENDPOINT_PDF.format(id=bula.id_bula_profissional)}"
    resposta = navegador.execute_async_script(SCRIPT_FETCH_BINARIO, url)
    _validar_status(resposta, f"download de '{bula.nome_produto}'")

    try:
        conteudo = base64.b64decode(resposta["base64"], validate=True)
    except (KeyError, TypeError, ValueError) as erro:
        raise RespostaAnvisaError("O conteúdo baixado não pôde ser decodificado.") from erro

    if not conteudo.startswith(b"%PDF"):
        raise RespostaAnvisaError(
            "A resposta do download não possui a assinatura de um PDF válido."
        )
    return conteudo
