from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from .erros import BloqueioAnvisaError


URL_BULARIO = "https://consultas.anvisa.gov.br/#/bulario/q/"


def criar_navegador(
    perfil: Path,
    headless: bool = False,
) -> webdriver.Chrome:
    # O Chrome não deve reutilizar simultaneamente o mesmo perfil em modo
    # visível e headless. Um perfil separado evita bloqueio/corrupção do
    # user-data-dir e o erro DevToolsActivePort.
    perfil_efetivo = (
        perfil.with_name(f"{perfil.name}-headless")
        if headless
        else perfil
    )
    perfil_efetivo.mkdir(parents=True, exist_ok=True)

    opcoes = Options()
    opcoes.add_argument(f"--user-data-dir={perfil_efetivo.resolve()}")
    opcoes.add_argument("--disable-notifications")
    opcoes.add_argument("--lang=pt-BR")
    if headless:
        opcoes.add_argument("--headless")
        opcoes.add_argument("--disable-gpu")
        opcoes.add_argument("--remote-debugging-port=0")
        opcoes.add_argument("--no-first-run")
        opcoes.add_argument("--no-default-browser-check")
        opcoes.add_argument("--window-size=1440,1000")
    else:
        opcoes.add_argument("--start-maximized")

    navegador = webdriver.Chrome(options=opcoes)
    navegador.set_page_load_timeout(90)
    navegador.set_script_timeout(120)
    return navegador


def abrir_sessao_publica(
    navegador: webdriver.Chrome,
    aguardar_confirmacao: bool = True,
) -> None:
    navegador.get(URL_BULARIO)
    titulo_ou_html = f"{navegador.title}\n{navegador.page_source[:5000]}".lower()
    bloqueado = "sorry, you have been blocked" in titulo_ou_html

    if bloqueado and not aguardar_confirmacao:
        raise BloqueioAnvisaError(
            "O Chrome abriu uma página de bloqueio antes da coleta."
        )

    if aguardar_confirmacao:
        print("\nO Chrome foi aberto no Bulário Eletrônico da Anvisa.")
        if bloqueado:
            print("A página exibiu um bloqueio. Não tente contorná-lo.")
        input(
            "Confirme que o Bulário abriu normalmente e pressione ENTER "
            "para iniciar o piloto..."
        )
