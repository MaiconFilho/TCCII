"""Localiza arquivos preservados durante a reorganização do monorepo."""

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
MODULOS = (
    "scraping_anvisa", "extracao_interacoes", "identificacao_interacoes",
    "conversao_interacoes_vetores",
)


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
