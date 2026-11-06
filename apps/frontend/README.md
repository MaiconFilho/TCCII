# InteraMed — interface

A interface usa a API 2.0.0 e a base fixa de cinco tabelas/60 campos.
Inclusão sob demanda não integra este modelo: nenhuma variável pública a reativa.
Nome ausente exibe orientação para escolher um cadastro disponível.

Frontend em Next.js (App Router), React, TypeScript, Tailwind CSS e shadcn/ui,
ligado à API de consulta [`api_interacoes`](../api_interacoes/README.md). Os
resultados vêm do banco: o tópico de interações das bulas profissionais da
Anvisa, processado por [`identificacao_interacoes`](../../pipelines/identificacao_interacoes/README.md).

O hero usa uma superfície pontilhada animada em Three.js. A animação respeita
`prefers-reduced-motion`, e o layout mantém a largura original em zoom de 100%,
expandindo conteúdo e tipografia quando o zoom do navegador é reduzido.

O guia inicial abre automaticamente uma vez por aba/sessão, até a conclusão
registrada em `sessionStorage` na chave `interamed:guia-concluido`. Na abertura
automática, só o botão final fecha o guia, após 5 segundos de leitura da tela de
limitações com a aba visível; voltar preserva a contagem. O botão **Como usar**
no cabeçalho reabre o guia desde o início, sem espera e com fechamento livre.

- **Busca de um medicamento**: resumo geral de 2 a 3 parágrafos sobre as interações
  da própria bula. “Ver detalhes” abre os cards e a paginação. Não inclui outras
  bulas. Sem resumo gerado, informa a indisponibilidade e mantém os detalhes.
- **Resumos dos cards**: preferem `resumo_card`, gerado pela etapa offline
  `pipelines/resumos_interacoes`; usam o recorte anterior enquanto ele não existir.
- **Verificar interações entre medicamentos**: adicione vários à lista e clique
  em Consultar interações. A interface consulta todos os pares únicos (inclusive entre os
  medicamentos adicionados), até quatro consultas simultâneas, e mostra o
  resultado de cada par nos dois sentidos. Citações diferentes do mesmo par,
  mesmo quando vêm das duas bulas, ficam em um cartão com todos os trechos na
  janela de evidências. Menções e alertas continuam identificados separadamente.
  Os medicamentos podem ser removidos da lista antes de uma nova busca.
- **Ver evidência**: trecho literal da bula, com o nome citado destacado, a bula
  de origem, o registro, o expediente e o critério do
  vínculo e os medicamentos do banco com aquela substância.
- **Sem gravidade**: as bulas não trazem escala padronizada e a interface não
  classifica gravidade.

## Executar localmente

São dois processos, em dois Prompts de Comando, a partir da raiz do projeto.

1. API (precisa de `apps\api_interacoes\.env` com a `DATABASE_URL`):

   ```cmd
   apps\api_interacoes\.venv\Scripts\python -m apps.api_interacoes
   ```

2. Frontend (requer Node.js 20.9 ou superior):

   ```cmd
   cd apps\frontend
   npm install
   npm run dev
   ```

Abra `http://localhost:3000` (ou `http://127.0.0.1:3000`; as duas origens são
permitidas no CORS padrão da API). Se a API estiver parada, a tela mostra
"API indisponível" com o comando para iniciá-la.

O endereço da API vem de `NEXT_PUBLIC_API_URL` (padrão `http://127.0.0.1:8000`).
Para usar outro, copie `.env.example` para `.env.local` e ajuste. O valor é
fixado no `npm run build`; refaça o build se mudar.

Para verificar e gerar a versão publicável:

```powershell
npm run typecheck
npm run lint
npm run build
```

O projeto requer Node.js 20.9 ou superior. Os comandos `dev`, `build` e `start`
também aceitam o Node 22 portátil em `.tools/` quando o Node global é antigo;
essa pasta local não é versionada.

O build exporta arquivos estáticos para `out/`; um servidor de arquivos estáticos
pode publicá-los, e as consultas partem do navegador para a API. O Node portátil
em `.tools/` foi usado apenas para desenvolver nesta máquina e não faz parte do
projeto versionado.

## Organização

- `app/`: rota inicial, layout, metadados e estilos globais.
- `components/interamed.tsx`: interface, estados da consulta, cartões e janela de evidência.
- `components/ui/`: componentes shadcn/ui gerados pelo CLI.
- `lib/api.ts`: cliente da API e tipos das respostas (espelham `apps/api_interacoes/contratos.py`).
- `lib/interacoes-data.ts`: textos de exibição (estados, critérios de vínculo,
  pesquisas frequentes), nomes legíveis e link de busca no Bulário.

O botão "Consultar no Bulário" abre a **busca** do medicamento no Bulário da
Anvisa: a API não fornece o link do PDF específico. A interface não infere
gravidade nem trata ausência de resultado como ausência comprovada de interação.

## Resumos em linguagem simples

O geral usa `texto_simples` quando fornecido pela API; os cards individuais e
de pares usam `resumo_card_simples`. Na ausência de uma versão válida, continuam
usando o resumo original e, no card, o recorte anterior como último recurso.
O frontend não carrega modelos nem confere hashes: a API verifica a vigência.
A janela **Ver evidência** continua mostrando o trecho literal da bula.

## Modelo aprovado e implantação

Fontes não exigem publicação da bula. A interface mostra somente registro,
expediente e os alvos/critérios comprovados. Componentes são atribuídos apenas
quando o nome corresponde exatamente; equivalência de sais ou composição não
atribui a evidência a todos os componentes.

Para produção, gere o build com `NEXT_PUBLIC_API_URL=https://interamed-api.vercel.app`
e configure CORS da API para `https://interamed-web.vercel.app`. Essa URL participa
do build estático; precisa de novo build para mudar. A sequência coordenada de
banco/API/frontend exige validar a API e o [modelo aprovado](../../README.md#banco).

```powershell
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
```

A verificação visual requer um navegador autorizado. Testes de contrato/build não
substituem a conferência da aplicação conectada ao Supabase readequado.
