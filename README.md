# InteraMed — identificação de interações e consulta web

Projeto acadêmico para consultar interações descritas nas bulas profissionais
da Anvisa. A identificação preserva as evidências e associa os nomes citados aos
medicamentos cadastrados. A API e a interface consultam os resultados persistidos.
A ausência de resultado não comprova ausência de interação.

## Estrutura

```text
TCCII/
├── apps/
│   ├── frontend/
│   └── api_interacoes/
├── pipelines/
│   ├── scraping_anvisa/
│   ├── extracao_interacoes/
│   ├── identificacao_interacoes/
│   ├── conversao_interacoes_vetores/
│   └── caminhos.py
├── docs/
├── backups_banco/
├── .github/
├── .gitignore
└── README.md
```

Os módulos Python são executados a partir da raiz com `python -m pipelines.<modulo>`
ou `python -m apps.api_interacoes`. Cada componente mantém suas dependências e seu
arquivo `.env`, criado a partir de `.env.example`. A API não executa migrações.

## Componentes

- [Scraping](pipelines/scraping_anvisa/README.md)
- [Extração](pipelines/extracao_interacoes/README.md)
- [Identificação](pipelines/identificacao_interacoes/README.md)
- [Vetorização](pipelines/conversao_interacoes_vetores/README.md)
- [API](apps/api_interacoes/README.md)
- [Frontend](apps/frontend/README.md)
- [Organização e execução](docs/reorganizacao.md)

Credenciais, modelos, PDFs coletados, ambientes, relatórios e backups permanecem
locais. O workflow valida os módulos com testes isolados e verifica o frontend.
