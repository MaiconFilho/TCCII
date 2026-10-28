"use client"

import * as React from 'react'
import { Loader2 } from 'lucide-react'
import { api, ehCancelamento, ErroApi, type Medicamento, type Solicitacao } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

const CHAVE = 'interamed-preparacao-v1'
const FINAIS = new Set(['CONCLUIDO', 'NAO_ENCONTRADO', 'FALHA', 'REVISAO_NECESSARIA'])
const FALHAS_POR_ETAPA: Record<string, string> = {
  ANVISA: 'Não foi possível buscar a bula no Bulário Eletrônico da Anvisa. Tente novamente mais tarde.',
  DOWNLOAD: 'A bula foi localizada, mas não foi possível baixar o documento no Bulário Eletrônico da Anvisa.',
  EXTRACAO: 'Não foi possível extrair as informações da bula deste medicamento.',
  IDENTIFICACAO: 'Não foi possível concluir a identificação das interações na bula.',
  VINCULOS: 'Não foi possível concluir a associação dos medicamentos citados na bula.',
  VETORES: 'Não foi possível concluir a preparação dos trechos da bula para consulta.',
  RESUMOS: 'Não foi possível concluir a geração dos resumos das interações.',
}

export interface ContextoPreparacao {
  chave: string
  nomes: string[]
  destino: 'consulta' | 'comparado'
  comparar: boolean
  pedidos?: Solicitacao[]
}
interface Atividade { contexto: ContextoPreparacao; pedidos: Solicitacao[] }

function recuperar(): Atividade | null {
  try {
    const valor = JSON.parse(localStorage.getItem(CHAVE) || 'null') as Atividade | null
    return valor && Array.isArray(valor.contexto?.nomes) && Array.isArray(valor.pedidos) ? valor : null
  } catch { return null }
}

export function PreparacaoMedicamento({ contexto, onConcluido, onEncerrar }: {
  contexto: ContextoPreparacao | null
  onConcluido: (medicamentos: Medicamento[], contexto: ContextoPreparacao) => void
  onEncerrar: () => void
}) {
  const [atividade, setAtividade] = React.useState<Atividade | null>(null)
  const [erro, setErro] = React.useState<string | null>(null)
  const [tentativa, setTentativa] = React.useState(0)
  const finalizado = React.useRef<string | null>(null)
  const concluir = React.useRef(onConcluido)
  React.useEffect(() => { concluir.current = onConcluido }, [onConcluido])

  React.useEffect(() => {
    const timer = setTimeout(() => {
      setErro(null)
      setAtividade(contexto ? { contexto, pedidos: contexto.pedidos ?? [] } : recuperar())
    }, 0)
    return () => clearTimeout(timer)
  }, [contexto])

  const chave = atividade?.contexto.chave
  React.useEffect(() => {
    if (!atividade || !chave) return
    const controle = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let atual = atividade
    const consultados = new Set<string>()
    const salvar = (valor: Atividade) => {
      atual = valor
      if (controle.signal.aborted) return
      try { localStorage.setItem(CHAVE, JSON.stringify(valor)) } catch { /* acompanhamento continua nesta página */ }
      setAtividade(valor)
    }
    // Se a página fechar antes da resposta do POST, o nome/contexto permitem
    // reencontrar a solicitação ativa sem criar outro trabalho.
    try { localStorage.setItem(CHAVE, JSON.stringify(atual)) } catch { /* armazenamento opcional */ }
    const acompanhar = async () => {
      try {
        const pedidos = [...atual.pedidos]
        for (let i = 0; i < atual.contexto.nomes.length; i++) {
          if (!pedidos[i]) pedidos[i] = await api.solicitar(atual.contexto.nomes[i], controle.signal)
          // Uma leitura inicial também atualiza resultados finais restaurados do
          // armazenamento local; depois disso, estados finais encerram o polling.
          else if (pedidos[i].id && (!FINAIS.has(pedidos[i].estado) || !consultados.has(pedidos[i].id!))) {
            consultados.add(pedidos[i].id!)
            pedidos[i] = await api.andamento(pedidos[i].id!, controle.signal)
          }
          salvar({ contexto: atual.contexto, pedidos: [...pedidos] })
        }
        if (pedidos.every((p) => p.estado === 'CONCLUIDO')) {
          const medicamentos = await Promise.all(pedidos.map(async (p) => {
            const nome = p.nome_confirmado!
            const lista = await api.sugestoes(nome, 20, controle.signal)
            return lista.sugestoes.find((m) => m.nome_normalizado === nome)
              ?? { nome_normalizado: nome, nome_exibicao: nome }
          }))
          if (!controle.signal.aborted && finalizado.current !== chave) {
            finalizado.current = chave
            try { localStorage.removeItem(CHAVE) } catch { /* sem armazenamento local */ }
            setAtividade(null)
            concluir.current(medicamentos, atual.contexto)
          }
        } else if (pedidos.some((p) => !FINAIS.has(p.estado))) timer = setTimeout(acompanhar, 5000)
      } catch (e) {
        if (!ehCancelamento(e)) setErro(e instanceof ErroApi ? e.message : 'Não foi possível consultar o andamento. O processamento já iniciado continua.')
      }
    }
    void acompanhar()
    return () => { controle.abort(); clearTimeout(timer) }
    // O polling usa seu snapshot e só reinicia em uma nova consulta ou tentativa manual.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chave, tentativa])

  const selecionar = async (pedido: Solicitacao, nome: string) => {
    try {
      setErro(null)
      const novo = await api.selecionarAnvisa(pedido.id!, nome)
      const atual = { ...atividade!, pedidos: atividade!.pedidos.map((p) => p.id === pedido.id ? novo : p) }
      try { localStorage.setItem(CHAVE, JSON.stringify(atual)) } catch { /* acompanhamento local */ }
      setAtividade(atual)
      setTentativa((t) => t + 1)
    } catch (e) { setErro(e instanceof ErroApi ? e.message : 'Não foi possível confirmar o medicamento.') }
  }

  if (!atividade) return null
  const emAndamento = atividade.pedidos.length < atividade.contexto.nomes.length || atividade.pedidos.some((p) => !FINAIS.has(p.estado))
  return <Card className="mt-7">
    <CardContent className="space-y-4 pt-6" aria-live="polite">
      <p className="font-semibold">Preparação dos medicamentos para consulta</p>
      <p className="text-sm text-muted-foreground">Vamos consultar a Anvisa e preparar as informações que faltam. Você pode sair desta página e voltar para acompanhar.</p>
      {atividade.pedidos.length === 0 && <p role="status" className="text-sm">Solicitando processamento…</p>}
      {atividade.pedidos.map((pedido, i) => <div key={i} className="space-y-2 rounded-lg border p-3">
        <p className="font-medium">{pedido.nome_solicitado}</p>
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          {!FINAIS.has(pedido.estado) && pedido.estado !== 'AGUARDANDO_SELECAO' && <Loader2 className="size-4 shrink-0 animate-spin" />}
          {pedido.estado === 'FALHA' ? FALHAS_POR_ETAPA[pedido.etapa] ?? pedido.mensagem : pedido.mensagem}
        </p>
        {pedido.estado === 'AGUARDANDO_SELECAO' && <div className="flex flex-col gap-2">
          {pedido.opcoes.map((opcao) => <Button key={opcao.nome_normalizado} variant="outline"
            className="h-auto justify-start whitespace-normal py-2 text-left" onClick={() => void selecionar(pedido, opcao.nome_normalizado)}>
            {opcao.nome_produto} · Registro {opcao.numero_registro}
          </Button>)}
        </div>}
      </div>)}
      {erro && <div role="alert" className="space-y-2 text-sm text-destructive"><p>{erro}</p>
        <Button variant="outline" onClick={() => { setErro(null); setTentativa((t) => t + 1) }}>Consultar andamento novamente</Button>
      </div>}
      {!emAndamento && <Button variant="outline" onClick={() => {
        try { localStorage.removeItem(CHAVE) } catch { /* sem armazenamento local */ }
        setAtividade(null); onEncerrar()
      }}>Fechar acompanhamento</Button>}
    </CardContent>
  </Card>
}
