import type { EvidenciaPar, Medicamento, RespostaPar } from "@/lib/api"
import { fold } from "@/lib/interacoes-data"

/** Cada combinação aparece uma única vez, independentemente da ordem de seleção. */
export function paresDeMedicamentos(medicamentos: Medicamento[]): [Medicamento, Medicamento][] {
  const unicos = [...new Map(medicamentos.map((m) => [m.nome_normalizado, m])).values()]
  const pares: [Medicamento, Medicamento][] = []
  for (let i = 0; i < unicos.length; i++) {
    for (let j = i + 1; j < unicos.length; j++) pares.push([unicos[i], unicos[j]])
  }
  return pares
}

const palavras = (texto: string) => new Set(fold(texto).match(/[a-z0-9]+/g) ?? [])

/**
 * Como uma evidência ligou os dois medicamentos comparados: a bula que contém
 * o trecho, o nome citado e o medicamento comparado que tem esse nome entre os
 * seus princípios ativos. Em medicamento composto, o trecho pode citar só um
 * componente ("paracetamol" em "Paracetamol + Cloridrato de Pseudoefedrina").
 */
export interface Ligacao {
  origem: Medicamento
  vinculado: Medicamento
  /** Nomes citados no trecho, como estão escritos na bula. */
  citados: string[]
  /** Princípios ativos do medicamento vinculado. */
  componentes: string[]
  /** Componentes do vinculado que correspondem ao que o trecho cita. */
  componentesCitados: string[]
  /** Componentes do vinculado que o trecho não cita. */
  naoCitados: string[]
}

export function ligacaoDaEvidencia(dados: RespostaPar, evidencia: EvidenciaPar): Ligacao | null {
  const itens = evidencia.itens.filter((item) => item.criterio_vinculo)
  if (itens.length === 0) return null
  const vinculado = evidencia.sentido === "BULA_A_CITA_B" ? dados.medicamento_b : dados.medicamento_a
  return montarLigacao(evidencia.fonte.medicamento, vinculado, itens)
}

export function montarLigacao(
  origem: Medicamento,
  vinculado: Medicamento,
  itens: { alvo: string; fundamento_vinculo?: string | null }[],
): Ligacao {
  const componentes = vinculado.principios_ativos ?? []
  // O componente corresponde ao princípio do cadastro que fundamentou o vínculo,
  // ou contém todas as palavras do nome citado ("varfarina" em "varfarina sódica").
  const corresponde = (componente: string) => itens.some((item) => {
    if (item.fundamento_vinculo && fold(item.fundamento_vinculo.trim()) === fold(componente.trim())) return true
    const doCitado = palavras(item.alvo)
    const doComponente = palavras(componente)
    return doCitado.size > 0 && [...doCitado].every((p) => doComponente.has(p))
  })
  const componentesCitados = componentes.filter(corresponde)
  return {
    origem,
    vinculado,
    citados: [...new Map(itens.map((item) => [fold(item.alvo), item.alvo])).values()],
    componentes,
    componentesCitados,
    // Sem componente reconhecido (vínculo pelo nome comercial), não se afirma o que falta.
    naoCitados: componentesCitados.length > 0 ? componentes.filter((c) => !corresponde(c)) : [],
  }
}

/** Uma ligação por bula de origem, medicamento vinculado e nomes citados. */
export function ligacoesDoPar(dados: RespostaPar, evidencias: EvidenciaPar[]): Ligacao[] {
  const unicas = new Map<string, Ligacao>()
  for (const evidencia of evidencias) {
    const ligacao = ligacaoDaEvidencia(dados, evidencia)
    if (!ligacao) continue
    const chave = [ligacao.origem.nome_normalizado, ligacao.vinculado.nome_normalizado,
      ...ligacao.citados.map(fold).sort()].join("\u0000")
    if (!unicas.has(chave)) unicas.set(chave, ligacao)
  }
  return [...unicas.values()]
}

/** Composto em que o trecho cita só parte dos componentes: é o caso que precisa de aviso. */
export const citaParteDoComposto = (ligacao: Ligacao) =>
  ligacao.componentes.length > 1 && ligacao.componentesCitados.length > 0 && ligacao.naoCitados.length > 0
