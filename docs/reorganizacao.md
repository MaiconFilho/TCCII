# Organização do InteraMed

As aplicações ficam em `apps/` e os processos de dados em `pipelines/`.
Cada componente conserva seu `requirements.txt` ou `package-lock.json`, exemplos
de configuração e testes. `docs/` reúne guias; `backups_banco/` contém somente
orientações versionadas, sem os backups locais.

| Caminho anterior | Caminho atual |
| --- | --- |
| `frontend/` | `apps/frontend/` |
| `api_interacoes/` | `apps/api_interacoes/` |
| `scraping_anvisa/` | `pipelines/scraping_anvisa/` |
| `extracao_interacoes/` | `pipelines/extracao_interacoes/` |
| `identificacao_interacoes/` | `pipelines/identificacao_interacoes/` |
| `conversao_interacoes_vetores/` | `pipelines/conversao_interacoes_vetores/` |

## Executar pela raiz

```powershell
python -m pipelines.scraping_anvisa --help
python -m pipelines.extracao_interacoes --help
python -m pipelines.identificacao_interacoes --help
python -m pipelines.conversao_interacoes_vetores --help
python -m apps.api_interacoes
```

No frontend, execute `npm ci` e `npm run dev` dentro de `apps/frontend/`.
No PowerShell, `npm.cmd` pode ser usado quando a política local impedir `npm.ps1`.
Os guias de cada componente detalham os ambientes, variáveis e opções.

## Compatibilidade de arquivos

Os imports Python usam `pipelines.<modulo>` e `apps.api_interacoes`. Os scripts
que permitiam execução direta conservam o ponto de entrada correspondente.
`pipelines/caminhos.py` resolve caminhos de PDFs anteriores à reorganização sem
reescrever os valores persistidos. Nenhum dado precisa ser removido para mover
as pastas. Ambientes virtuais e ferramentas locais devem ser recriados caso seus
lançadores ainda apontem para os caminhos antigos.

## Verificar

O workflow usa os caminhos atuais. Testes com PostgreSQL exigem uma base isolada;
nunca devem ser apontados para o banco de trabalho. As consultas da API são de
leitura e o processamento continua sendo executado pelos módulos próprios.
