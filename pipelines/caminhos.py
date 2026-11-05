"""Localiza arquivos preservados durante a reorganização do monorepo."""

from pathlib import Path
import os


RAIZ = Path(__file__).resolve().parent.parent
MODULOS = (
    "scraping_anvisa", "extracao_interacoes", "identificacao_interacoes",
    "conversao_interacoes_vetores",
)


def localizar_pdf(nome_normalizado: str, numero_registro: str | None, pasta: Path | None = None) -> Path:
    """Mesmo nome produzido pela coleta; sem caminho persistido nem busca ambígua.

    Arquivos conferidos: aas_178170936_profissional.pdf,
    ablok_plus_109740092_profissional.pdf etc.
    """
    from pipelines.scraping_anvisa.anvisa_scraper.modelos import montar_slug_nome
    diretorio = (pasta or Path(os.getenv('INTERAMED_PDF_DIR', str(RAIZ / 'pipelines/scraping_anvisa/pdfs')))).resolve()
    registro = str(numero_registro or '')
    if not registro.isdigit():
        raise ValueError('Registro numérico necessário para localizar o PDF sem ambiguidade.')
    arquivo = (diretorio / f'{montar_slug_nome(nome_normalizado)}_{registro}_profissional.pdf').resolve()
    if arquivo.parent != diretorio:
        raise ValueError('PDF fora do diretório configurado.')
    return arquivo


def resolver_caminho(caminho: str | Path) -> Path:
    """Aceita caminhos atuais e os antigos, sem alterar o valor persistido."""
    original = Path(caminho)
    if original.exists():
        return original.resolve()
    for modulo in MODULOS:
        for prefixo in (RAIZ / modulo, Path(modulo)):
            try:
                relativo = original.relative_to(prefixo)
            except ValueError:
                continue
            candidato = RAIZ / "pipelines" / modulo / relativo
            if candidato.is_file():
                return candidato.resolve()
    return original.resolve()
