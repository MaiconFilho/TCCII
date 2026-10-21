import type { CriterioVinculo, EstadoAnalise, EvidenciaPar } from "@/lib/api"

/** Textos e auxiliares de exibição. Os dados vêm da API; nada aqui é resultado de bula. */

export const ANVISA_BULARIO_URL = "https://consultas.anvisa.gov.br/#/bulario/"

/** Link para a busca do medicamento no Bulário da Anvisa. */
export const bulaUrl = (nomeProduto: string) =>
  `https://consultas.anvisa.gov.br/#/bulario/q/?nomeProduto=${encodeURIComponent(fold(nomeProduto).trim())}`

export const fold = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")

/** Quebras, espaços repetidos e caixa do PDF não mudam a identidade da citação. */
export const chaveCitacao = (texto: string) => fold(texto).replace(/\s+/g, "")

export function citacoesUnicas<T extends { alvo: string; evidencia: string }>(trechos: T[]): T[] {
  const vistas = new Set<string>()
  return trechos.filter((trecho) => {
    const chave = `${fold(trecho.alvo)}\u0000${chaveCitacao(trecho.evidencia)}`
    if (vistas.has(chave)) return false
    vistas.add(chave)
    return true
  })
}

/** Mantém efeitos diferentes da mesma passagem, mas mostra a citação uma vez. */
export function evidenciasUnicas(evidencias: EvidenciaPar[]): EvidenciaPar[] {
  const grupos = new Map<string, EvidenciaPar>()
  for (const evidencia of evidencias) {
    const chave = [evidencia.sentido, evidencia.fonte.medicamento.nome_normalizado,
      evidencia.processamento.id, chaveCitacao(evidencia.evidencia)].join("\u0000")
    const existente = grupos.get(chave)
    if (!existente) {
      grupos.set(chave, { ...evidencia, itens: [...evidencia.itens] })
      continue
    }
    for (const item of evidencia.itens) {
      if (!existente.itens.some((anterior) =>
        fold(anterior.alvo) === fold(item.alvo) && anterior.tipo_alvo === item.tipo_alvo &&
        chaveCitacao(anterior.resumo) === chaveCitacao(item.resumo) &&
        anterior.criterio_vinculo === item.criterio_vinculo)) existente.itens.push(item)
    }
  }
  return [...grupos.values()]
}

const MINUSCULAS = new Set(["de", "da", "do", "das", "dos", "e", "com", "em", "a", "o"])

/**
 * Nome para exibição. O cadastro da Anvisa mistura "MAREVAN" e "sinvastatina";
 * só nomes inteiros em maiúsculas ou minúsculas são ajustados.
 */
export function nomeLegivel(nome: string): string {
  const letras = nome.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ]/g, "")
  const maiusculo = letras === letras.toUpperCase()
  if (!maiusculo && letras !== letras.toLowerCase()) return nome
  return nome
    .split(/(\s+)/)
    .map((parte, i) => {
      const minuscula = parte.toLowerCase()
      if (i > 0 && MINUSCULAS.has(minuscula)) return minuscula
      // Siglas curtas em maiúsculas continuam assim: "AAS", "XR", "Vitamina D".
      if (maiusculo && parte.length <= 3) return parte
      return minuscula.charAt(0).toUpperCase() + minuscula.slice(1)
    })
    .join("")
}

/** Como o medicamento citado foi ligado ao do banco. */
export const CRITERIO: Record<CriterioVinculo, string> = {
  PRINCIPIO_ATIVO_EXATO: "mesmo princípio ativo",
  PRINCIPIO_ATIVO_SEM_SAL: "mesmo princípio ativo (sal ou forma diferente)",
  COMPOSICAO_EQUIVALENCIA_DECLARADA: "princípio ativo declarado na composição",
  NOME_MEDICAMENTO_EXATO: "mesmo nome comercial",
}

type Tom = "ok" | "neutro" | "atencao"

/** Rótulo curto e tom visual do estado da análise da bula. */
export const ESTADO: Record<EstadoAnalise, { rotulo: string; tom: Tom }> = {
  CONCLUIDO_COM_RELACOES: { rotulo: "Bula analisada", tom: "ok" },
  CONCLUIDO_SEM_RELACOES: { rotulo: "Bula analisada", tom: "ok" },
  REVISAO_NECESSARIA: { rotulo: "Em revisão", tom: "atencao" },
  EM_PROCESSAMENTO: { rotulo: "Em análise agora", tom: "atencao" },
  NAO_PROCESSADO: { rotulo: "Ainda não analisada", tom: "neutro" },
  DESATUALIZADO: { rotulo: "Análise desatualizada", tom: "atencao" },
  RETIRADO: { rotulo: "Análise retirada", tom: "atencao" },
  FALHA: { rotulo: "Falha na análise", tom: "atencao" },
  FONTE_INCOERENTE: { rotulo: "Fonte incoerente", tom: "atencao" },
  SEM_TOPICO_INTERACOES: { rotulo: "Sem tópico de interações", tom: "neutro" },
  EXTRACAO_INDISPONIVEL: { rotulo: "Tópico indisponível", tom: "neutro" },
  SEM_BULA: { rotulo: "Sem bula coletada", tom: "neutro" },
}

export const TOM_BADGE: Record<Tom, string> = {
  ok: "border-emerald-200 bg-emerald-50 text-emerald-700",
  neutro: "border-zinc-200 bg-zinc-50 text-zinc-600",
  atencao: "border-amber-200 bg-amber-50 text-amber-700",
}

export function formatarData(iso: string | null | undefined): string | null {
  if (!iso) return null
  const data = new Date(iso)
  return Number.isNaN(data.getTime()) ? null : data.toLocaleDateString("pt-BR")
}
