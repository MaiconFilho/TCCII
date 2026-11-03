"use client"

import * as React from "react"
import { ArrowLeft, ArrowRight, BookOpen, Info, Pill, Search, ShieldAlert } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { ESTADO, TOM_BADGE } from "@/lib/interacoes-data"
import { cn } from "@/lib/utils"

const CHAVE_CONCLUSAO = "interamed:guia-concluido"
const TELAS = [
  { titulo: "Bem-vindo ao InteraMed", icone: Pill },
  { titulo: "Como usar", icone: Search },
  { titulo: "Como ler os resultados", icone: BookOpen },
  { titulo: "Limitações importantes", icone: ShieldAlert },
] as const

const INSTRUCOES = [
  "Digite o nome comercial ou o princípio ativo e escolha uma das sugestões.",
  "Para ver interações entre medicamentos, ative “Verificar interações entre medicamentos” e adicione os outros.",
  "Clique em “Consultar interações”.",
  "Em cada resultado, clique em “Ver evidência” para ler o trecho da bula e abrir o documento completo no Bulário Eletrônico.",
]

const SITUACOES = [
  { estado: ESTADO.CONCLUIDO_COM_RELACOES, descricao: "o tópico de interações foi lido e processado." },
  { estado: ESTADO.REVISAO_NECESSARIA, descricao: "parte do conteúdo aguarda conferência." },
  { estado: ESTADO.NAO_PROCESSADO, descricao: "a bula está na base, mas ainda não foi processada." },
  { estado: ESTADO.SEM_TOPICO_INTERACOES, descricao: "a bula não possui essa seção." },
]

const LIMITACOES = [
  "Só estão no sistema medicamentos com bula profissional no Bulário Eletrônico da Anvisa. Se o medicamento não tem bula lá, ele não aparece aqui.",
  "As bulas foram coletadas até setembro de 2026. Atualizações posteriores da Anvisa podem não estar refletidas, e a inclusão de novos medicamentos está temporariamente indisponível.",
  "Apenas o tópico de interações da bula é analisado. Contraindicações, doses, alimentos, doenças e condições de cada paciente não são considerados.",
  "O sistema não classifica a gravidade das interações.",
  "Não encontrar uma interação não garante que ela não exista.",
]

export function GuiaInicial() {
  const [aberto, setAberto] = React.useState(false)
  const [modo, setModo] = React.useState<"automatico" | "consulta">("automatico")
  const [passo, setPasso] = React.useState(0)
  const [segundos, setSegundos] = React.useState(5)
  const restante = React.useRef(5000)
  const titulo = React.useRef<HTMLHeadingElement | null>(null)
  const focarTitulo = React.useCallback((elemento: HTMLHeadingElement | null) => {
    titulo.current = elemento
    elemento?.focus({ preventScroll: true })
  }, [])
  const automatico = modo === "automatico"
  const ultimaTela = passo === TELAS.length - 1
  const bloqueado = automatico && segundos > 0
  const { titulo: textoTitulo, icone: Icone } = TELAS[passo]

  React.useEffect(() => {
    const tarefa = window.setTimeout(() => {
      try {
        if (sessionStorage.getItem(CHAVE_CONCLUSAO)) return
      } catch {
        // Armazenamento indisponível: mantém a apresentação do guia.
      }
      setAberto(true)
    }, 0)
    return () => window.clearTimeout(tarefa)
  }, [])

  React.useEffect(() => {
    if (!aberto || !automatico || !ultimaTela || restante.current <= 0) return

    let inicio: number | null = null
    let tarefa: number | undefined
    const descontar = () => {
      if (inicio === null) return
      const agora = performance.now()
      restante.current = Math.max(0, restante.current - (agora - inicio))
      inicio = agora
      setSegundos(Math.ceil(restante.current / 1000))
    }
    const agendar = () => {
      tarefa = window.setTimeout(() => {
        descontar()
        if (restante.current > 0) agendar()
      }, restante.current % 1000 || 1000)
    }
    const pausar = () => {
      window.clearTimeout(tarefa)
      descontar()
      inicio = null
    }
    const acompanharVisibilidade = () => {
      pausar()
      if (document.visibilityState === "visible" && restante.current > 0) {
        inicio = performance.now()
        agendar()
      }
    }

    acompanharVisibilidade()
    document.addEventListener("visibilitychange", acompanharVisibilidade)
    return () => {
      pausar()
      document.removeEventListener("visibilitychange", acompanharVisibilidade)
    }
  }, [aberto, automatico, ultimaTela])

  const concluir = () => {
    if (automatico && (!ultimaTela || restante.current > 0)) return
    try {
      sessionStorage.setItem(CHAVE_CONCLUSAO, "true")
    } catch {
      // A falha de persistência não impede o acesso ao sistema.
    }
    setAberto(false)
  }

  return (
    <Dialog open={aberto} modal disablePointerDismissal={automatico}
      onOpenChange={(proximo, detalhes) => {
        if (!proximo && automatico && !(detalhes.reason === "close-press" && ultimaTela && restante.current <= 0)) {
          detalhes.cancel()
          return
        }
        if (proximo && detalhes.reason === "trigger-press") {
          setModo("consulta")
          setPasso(0)
        }
        setAberto(proximo)
      }}>
      <DialogTrigger render={<Button variant="ghost" className="size-10 text-blue-600 sm:w-auto sm:gap-2 sm:px-3" />} aria-label="Como usar">
        <Info className="size-4" aria-hidden="true" />
        <span className="hidden sm:inline">Como usar</span>
      </DialogTrigger>
      <DialogContent showCloseButton={!automatico} initialFocus={titulo}
        className="flex h-[calc(100dvh-1rem)] max-h-[calc(100dvh-1rem)] max-w-[calc(100%-1rem)] flex-col gap-0 overflow-hidden p-0 motion-reduce:animate-none sm:h-auto sm:max-h-[min(90dvh,50rem)] sm:max-w-xl">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b px-5 py-4 pr-12 sm:px-6 sm:pr-12">
          <span className="text-xs font-medium text-muted-foreground">Guia inicial · {passo + 1} de {TELAS.length}</span>
          <div className="flex gap-1.5" aria-hidden="true">
            {TELAS.map((tela, indice) => <span key={tela.titulo} className={cn("size-2 rounded-full", indice === passo ? "bg-blue-600" : indice < passo ? "bg-blue-300" : "bg-muted")} />)}
          </div>
        </div>

        <div key={passo} className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-right-2 motion-safe:duration-200 sm:p-6">
          <div className={cn("mb-4 flex size-11 items-center justify-center rounded-xl", ultimaTela ? "bg-amber-50 text-amber-700" : "bg-blue-50 text-blue-600")}>
            <Icone className="size-5" aria-hidden="true" />
          </div>
          <DialogTitle ref={focarTitulo} tabIndex={-1} className="mb-2 text-xl leading-snug font-semibold tracking-tight outline-none sm:text-2xl">{textoTitulo}</DialogTitle>
          <DialogDescription className="sr-only">Conheça o sistema, aprenda a consultar as bulas e confira as limitações.</DialogDescription>

          <div className="mt-4 space-y-4 text-sm leading-relaxed text-muted-foreground">
            {passo === 0 && <>
              <p>O InteraMed reúne as interações medicamentosas descritas nas bulas profissionais publicadas no Bulário Eletrônico da Anvisa.</p>
              <p>Ele mostra quais medicamentos se relacionam e em qual bula a interação foi encontrada, sempre com o trecho original para conferência.</p>
              <p className="rounded-xl border border-blue-200 bg-blue-50/40 p-4 text-foreground">É uma ferramenta informativa, desenvolvida como Trabalho de Conclusão de Curso.</p>
            </>}
            {passo === 1 && <ol className="space-y-3">
              {INSTRUCOES.map((instrucao, indice) => <li key={instrucao} className="flex gap-3 rounded-xl border border-border/70 bg-card/80 p-3.5">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-50 text-xs font-semibold text-blue-600" aria-hidden="true">{indice + 1}</span>
                <span>{instrucao}</span>
              </li>)}
            </ol>}
            {passo === 2 && <>
              <p>“Citado na bula de…” indica em qual bula a interação aparece. Basta uma das bulas citar o outro medicamento.</p>
              <p>Os resumos em linguagem simples são gerados automaticamente por um modelo de inteligência artificial. Na dúvida, vale o trecho original da bula.</p>
              <div className="rounded-xl border border-border/70 bg-card/80 p-4">
                <h3 className="mb-3 font-semibold text-foreground">Situação da bula</h3>
                <ul className="space-y-3">
                  {SITUACOES.map(({ estado, descricao }) => <li key={estado.rotulo}>
                    <Badge variant="outline" className={cn("mb-1 rounded-full", TOM_BADGE[estado.tom])}>{estado.rotulo}</Badge>
                    <p className="text-xs">{descricao}</p>
                  </li>)}
                </ul>
              </div>
            </>}
            {passo === 3 && <>
              <ul className="space-y-3">
                {LIMITACOES.map((limitacao, indice) => <li key={limitacao} className="flex gap-2.5">
                  <ShieldAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden="true" />
                  {indice === 0 ? <strong className="font-semibold text-foreground">{limitacao}</strong> : <span>{limitacao}</span>}
                </li>)}
              </ul>
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-950">
                <p className="font-semibold">Não substitui a orientação de um médico ou farmacêutico.</p>
                <p className="mt-1">Não inicie, suspenda ou altere medicamentos com base nesta consulta. Em caso de intoxicação, ligue para o Disque-Intoxicação: <a href="tel:08007226001" className="font-semibold underline underline-offset-2">0800 722 6001</a>.</p>
              </div>
            </>}
          </div>
        </div>

        <div className="shrink-0 border-t bg-muted/40 p-4 sm:px-6">
          <p className="sr-only" aria-live="polite" aria-atomic="true">{automatico && segundos === 0 ? "Leitura concluída. O botão Acessar o InteraMed está disponível." : ""}</p>
          <div className="flex items-center justify-between gap-2">
            <Button variant="outline" className="h-10 gap-1 px-3" disabled={passo === 0} onClick={() => setPasso((atual) => atual - 1)}>
              <ArrowLeft className="size-4" aria-hidden="true" />Voltar
            </Button>
            {ultimaTela ? <DialogClose disabled={bloqueado} onClick={concluir}
              render={<Button className="h-10 bg-blue-600 px-3 text-white hover:bg-blue-700" />} aria-live="off">
              {automatico ? `Acessar o InteraMed${bloqueado ? ` (${segundos})` : ""}` : "Voltar ao InteraMed"}
            </DialogClose> : <Button className="h-10 gap-2 bg-blue-600 px-4 text-white hover:bg-blue-700" onClick={() => setPasso((atual) => atual + 1)}>
              Próximo<ArrowRight className="size-4" aria-hidden="true" />
            </Button>}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
