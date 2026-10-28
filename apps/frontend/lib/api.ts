/**
 * Cliente da API de interações (api_interacoes, FastAPI).
 *
 * O site é exportado como arquivos estáticos, então as consultas partem do
 * navegador. O endereço vem de NEXT_PUBLIC_API_URL, fixado no `next build`
 * (no `next dev`, lido de `.env.local`); sem ela, a API local padrão.
 */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "")

export type EstadoAnalise =
  | "CONCLUIDO_COM_RELACOES"
  | "CONCLUIDO_SEM_RELACOES"
  | "REVISAO_NECESSARIA"
  | "EM_PROCESSAMENTO"
  | "NAO_PROCESSADO"
  | "DESATUALIZADO"
  | "RETIRADO"
  | "FALHA"
  | "FONTE_INCOERENTE"
  | "SEM_TOPICO_INTERACOES"
  | "EXTRACAO_INDISPONIVEL"
  | "SEM_BULA"

export type TipoAlvo =
  | "PRINCIPIO_ATIVO"
  | "MEDICAMENTO"
  | "CLASSE_FARMACOLOGICA"
  | "ALIMENTO"
  | "GRUPO_ALIMENTAR"
  | "OUTRA_SUBSTANCIA"
  | "EXAME_LABORATORIAL"
  | "OUTRO"

export type CriterioVinculo =
  | "PRINCIPIO_ATIVO_EXATO"
  | "PRINCIPIO_ATIVO_SEM_SAL"
  | "COMPOSICAO_EQUIVALENCIA_DECLARADA"
  | "NOME_MEDICAMENTO_EXATO"

export interface Medicamento {
  nome_normalizado: string
  nome_exibicao: string
  /** Princípios ativos do cadastro; a API preenche só para os medicamentos consultados. */
  principios_ativos?: string[] | null
}

export interface MedicamentoRelacionado extends Medicamento {
  criterio_vinculo: CriterioVinculo
}

export interface FonteBula {
  medicamento: Medicamento
  numero_registro: string | null
  expediente: string | null
  sha256_pdf: string | null
  data_publicacao_bula: string | null
}

export interface Processamento {
  id: number
  estado: EstadoAnalise
  data_processamento: string
  versao_processamento: string
  modelo: string
}

export interface Relacao {
  id: number
  sentido: "DESCRITA_NA_BULA_PESQUISADA" | "DESCRITA_EM_OUTRA_BULA"
  alvo: string
  tipo_alvo: TipoAlvo
  resumo: string
  resumo_card: string | null
  resumo_card_simples?: string | null
  evidencia: string
  fonte: FonteBula
  processamento: Processamento
  medicamentos_relacionados: MedicamentoRelacionado[]
  total_medicamentos_relacionados: number
}

export interface AnaliseBula {
  estado: EstadoAnalise
  mensagem: string
  em_processamento: boolean
  processamento: Processamento | null
  fonte: FonteBula | null
}

export interface Cobertura {
  bulas_elegiveis: number
  bulas_analisadas: number
  bulas_em_revisao: number
  corpus_completo: boolean
  lista_exaustiva: boolean
  observacao: string
}

export interface RespostaInteracoes {
  medicamento: Medicamento
  analise_bula: AnaliseBula
  interacao_identificada: boolean | null
  mensagem: string
  cobertura: Cobertura
  relacoes: Relacao[]
  total_interacoes: number
  resumo_geral: {
    status: "CONCLUIDO" | "PENDENTE"
    texto: string | null
    texto_simples?: string | null
    gerado_em: string | null
    processamento_id: number | null
  }
  paginacao: { pagina: number; tamanho_pagina: number; total: number; total_paginas: number }
}

export interface ItemPar {
  interacao_id: number
  alvo: string
  tipo_alvo: TipoAlvo
  resumo: string
  resumo_card: string | null
  resumo_card_simples?: string | null
  criterio_vinculo: CriterioVinculo | null
  /** Texto do cadastro que fundamentou o vínculo (ex.: o princípio ativo "paracetamol"). */
  fundamento_vinculo?: string | null
}

export interface EvidenciaPar {
  sentido: "BULA_A_CITA_B" | "BULA_B_CITA_A"
  evidencia: string
  fonte: FonteBula
  processamento: Processamento
  itens: ItemPar[]
}

export interface RespostaPar {
  medicamento_a: Medicamento
  medicamento_b: Medicamento
  analise_a: AnaliseBula
  analise_b: AnaliseBula
  interacao_identificada: boolean | null
  mensagem: string
  cobertura_completa: boolean
  evidencias: EvidenciaPar[]
  alertas_nao_confirmados: EvidenciaPar[]
  mencoes_nao_vinculadas: EvidenciaPar[]
}

/** Erro já com a mensagem para o usuário (a API não expõe detalhes internos). */
export class ErroApi extends Error {
  status: number
  constructor(status: number, mensagem: string) {
    super(mensagem)
    this.status = status
  }
}

async function consultar<T>(caminho: string, parametros: Record<string, string | number>, sinal?: AbortSignal, corpo?: object): Promise<T> {
  const url = new URL(`${API_URL}/api${caminho}`)
  for (const [chave, valor] of Object.entries(parametros)) url.searchParams.set(chave, String(valor))
  let resposta: Response
  try {
    resposta = await fetch(url, { signal: sinal, method: corpo ? 'POST' : 'GET',
      headers: { Accept: 'application/json', ...(corpo ? { 'Content-Type': 'application/json' } : {}) },
      body: corpo ? JSON.stringify(corpo) : undefined, cache: 'no-store' })
  } catch (erro) {
    if (erro instanceof DOMException && erro.name === "AbortError") throw erro
    throw new ErroApi(0, `Não foi possível conectar à API em ${API_URL}. Verifique se ela está em execução.`)
  }
  if (!resposta.ok) {
    let mensagem = `A API respondeu com erro ${resposta.status}.`
    try {
      const corpo = await resposta.json()
      if (typeof corpo?.mensagem === "string") mensagem = corpo.mensagem
    } catch {
      // corpo sem JSON: mantém a mensagem genérica
    }
    throw new ErroApi(resposta.status, mensagem)
  }
  return resposta.json() as Promise<T>
}

export const api = {
  solicitar: (nome: string, sinal?: AbortSignal) =>
    consultar<Solicitacao>('/processamentos', {}, sinal, { nome }),
  andamento: (id: string, sinal?: AbortSignal) =>
    consultar<Solicitacao>(`/processamentos/${encodeURIComponent(id)}`, {}, sinal),
  selecionarAnvisa: (id: string, nome: string, sinal?: AbortSignal) =>
    consultar<Solicitacao>(`/processamentos/${encodeURIComponent(id)}/selecao`, {}, sinal, { nome_normalizado: nome }),
  sugestoes: (termo: string, limite = 8, sinal?: AbortSignal) =>
    consultar<{ termo: string; sugestoes: Medicamento[] }>("/medicamentos/sugestoes", { q: termo, limite }, sinal),
  interacoes: (nome: string, pagina = 1, tamanhoPagina = 50, sinal?: AbortSignal) =>
    consultar<RespostaInteracoes>("/interacoes", { medicamento: nome, pagina, tamanho_pagina: tamanhoPagina }, sinal),
  par: (a: string, b: string, sinal?: AbortSignal) =>
    consultar<RespostaPar>("/interacoes/par", { medicamento_a: a, medicamento_b: b }, sinal),
}

export const ehCancelamento = (erro: unknown) => erro instanceof DOMException && erro.name === "AbortError"

export interface Solicitacao {
  id: string | null
  nome_solicitado: string
  nome_confirmado: string | null
  estado: 'AGUARDANDO' | 'EM_PROCESSAMENTO' | 'AGUARDANDO_SELECAO' | 'CONCLUIDO' | 'NAO_ENCONTRADO' | 'FALHA' | 'REVISAO_NECESSARIA'
  etapa: string
  mensagem: string
  opcoes: { nome_normalizado: string; nome_produto: string; numero_registro: string; data_publicacao: string }[]
}
