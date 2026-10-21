"use client"

import * as React from "react"
import {
  AlertTriangle, ArrowLeftRight, ArrowRight, BookOpen, Download, FileText, HelpCircle, Info, Link2, Loader2,
  Pill, Quote, Search, ShieldCheck, Sparkles, WifiOff, X,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { Card, CardContent, CardFooter } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DottedSurface } from "@/components/ui/dotted-surface"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"
import {
  api, ehCancelamento, ErroApi,
  type AnaliseBula, type CriterioVinculo, type EvidenciaPar, type FonteBula, type Medicamento,
  type MedicamentoRelacionado, type Processamento, type Relacao, type RespostaInteracoes, type RespostaPar,
} from "@/lib/api"
import { bulaUrl, chaveCitacao, citacoesUnicas, CRITERIO, ESTADO, evidenciasUnicas, TOM_BADGE, fold, formatarData, nomeLegivel } from "@/lib/interacoes-data"
import { citaParteDoComposto, ligacoesDoPar, paresDeMedicamentos, type Ligacao } from "@/lib/comparacao"

const TAMANHO_PAGINA = 50
// Máximo aceito pela API (API_AUTOCOMPLETE_LIMITE_MAXIMO).
const LIMITE_SUGESTOES = 20
const MEDICAMENTO_AUSENTE = 'Medicamento não encontrado entre os disponíveis para consulta. Verifique o nome ou selecione uma sugestão.'
/** Trecho em uma linha, sem o marcador de lista do PDF no início. */
const compactar = (texto: string) =>
  texto.replace(/\s+/g, " ").trim().replace(/^[-•▪●◦*]+\s*/, "")

/** Destaca no trecho os nomes citados, sem diferenciar caixa e acentos. */
function Destaque({ texto, termos }: { texto: string; termos: string[] }) {
  const base = fold(texto)
  const faixas: [number, number][] = []
  for (const termo of termos.map((t) => fold(t.trim())).filter(Boolean)) {
    for (let i = base.indexOf(termo); i >= 0; i = base.indexOf(termo, i + termo.length)) faixas.push([i, i + termo.length])
  }
  faixas.sort((x, y) => x[0] - y[0])
  const partes: React.ReactNode[] = []
  let inicio = 0
  for (const [a, b] of faixas) {
    if (a < inicio) continue
    partes.push(texto.slice(inicio, a), <mark key={a} className="rounded bg-amber-100 px-0.5 text-foreground">{texto.slice(a, b)}</mark>)
    inicio = b
  }
  partes.push(texto.slice(inicio))
  return <>{partes}</>
}

/* ---------- Como o par foi relacionado ---------- */
/** Substância no meio da frase: "cloridrato de pseudoefedrina", não "CLORIDRATO DE PSEUDOEFEDRINA". */
function substancia(texto: string) {
  const letras = texto.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ]/g, "")
  return letras === letras.toUpperCase() || letras === letras.toLowerCase() ? texto.toLowerCase() : texto
}

const lista = (itens: string[]) =>
  itens.length <= 1 ? itens.join("") : `${itens.slice(0, -1).join(", ")} e ${itens[itens.length - 1]}`

function partesDaLigacao(ligacao: Ligacao) {
  const origem = nomeLegivel(ligacao.origem.nome_exibicao)
  const vinculado = nomeLegivel(ligacao.vinculado.nome_exibicao)
  const mesmoNome = ligacao.citados.length === 1 && fold(ligacao.citados[0]) === fold(ligacao.vinculado.nome_exibicao)
  const ehSubstancia = ligacao.componentesCitados.length > 0
  // Princípio ativo no meio da frase sempre em minúsculas; nome comercial como está.
  const citado = mesmoNome ? vinculado
    : lista(ligacao.citados.map((c) => (ehSubstancia ? c.toLowerCase() : substancia(c))))
  const composto = ligacao.componentes.length > 1 && ehSubstancia
  const papel = mesmoNome ? null
    : composto ? (ligacao.componentesCitados.length === 1 ? "um dos componentes de" : "componentes de")
    : ehSubstancia ? "princípio ativo de" : "presente em"
  return { origem, vinculado, citado, papel, naoCitados: lista(ligacao.naoCitados.map((c) => c.toLowerCase())) }
}

/** "A bula de Bálsamo Branco cita paracetamol, um dos componentes de Paracetamol + …". */
function ExplicacaoLigacao({ ligacao }: { ligacao: Ligacao }) {
  const { origem, vinculado, citado, papel } = partesDaLigacao(ligacao)
  return (
    <span>
      A bula de <strong className="font-medium text-foreground">{origem}</strong> cita{" "}
      <strong className="font-medium text-foreground">{citado}</strong>
      {papel && <>, {papel} <strong className="font-medium text-foreground">{vinculado}</strong></>}.
    </span>
  )
}

function AvisoNaoCitados({ ligacao }: { ligacao: Ligacao }) {
  if (!citaParteDoComposto(ligacao)) return null
  return (
    <span className="mt-1.5 inline-flex flex-wrap items-center gap-1 rounded-full border border-dashed border-zinc-300 px-2.5 py-0.5 text-[11px] text-muted-foreground">
      Não citado neste trecho: <strong className="font-medium text-zinc-700">{partesDaLigacao(ligacao).naoCitados}</strong>
    </span>
  )
}

/** Bula de origem → nome citado → medicamento comparado que o contém. */
function FluxoLigacao({ ligacao }: { ligacao: Ligacao }) {
  const { origem, vinculado, citado, papel, naoCitados } = partesDaLigacao(ligacao)
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="h-auto rounded-full bg-background py-1"><FileText />Bula de {origem}</Badge>
        <ArrowRight className="size-3.5 shrink-0 text-blue-400" />
        <Badge variant="outline" className="h-auto rounded-full border-amber-200 bg-amber-100 py-1 text-amber-900">cita {citado}</Badge>
        {papel && <>
          <ArrowRight className="size-3.5 shrink-0 text-blue-400" />
          <Badge variant="outline" className="h-auto whitespace-normal rounded-full bg-background py-1 text-left"><Pill />{papel} {vinculado}</Badge>
        </>}
      </div>
      {citaParteDoComposto(ligacao) && (
        <p className="text-xs leading-relaxed text-blue-950/80">
          O trecho fala apenas de <strong>{citado}</strong>. {naoCitados.charAt(0).toUpperCase() + naoCitados.slice(1)},{" "}
          {ligacao.naoCitados.length === 1 ? "o outro componente" : "os outros componentes"} de {vinculado},{" "}
          {ligacao.naoCitados.length === 1 ? "não é mencionado" : "não são mencionados"}.
        </p>
      )}
    </div>
  )
}

/* ---------- Autocomplete ligado à API ---------- */
function MedicineInput({ id, value, onChange, selected, onSelect, onSubmit, placeholder, orientacaoAusente }: {
  id: string
  value: string
  onChange: (texto: string) => void
  selected: Medicamento | null
  onSelect: (medicamento: Medicamento) => void
  /** Recebe a sugestão escolhida agora: o estado do pai ainda não foi atualizado. */
  onSubmit: (escolhido?: Medicamento) => void
  placeholder: string
  orientacaoAusente: string
}) {
  const [open, setOpen] = React.useState(false)
  const [active, setActive] = React.useState(0)
  // Guarda o termo de cada lista: sugestões de um termo anterior não aparecem.
  const [sugestoes, setSugestoes] = React.useState<{ termo: string; lista: Medicamento[] }>({ termo: "", lista: [] })
  const [carregando, setCarregando] = React.useState(false)
  const [erro, setErro] = React.useState<string | null>(null)
  const q = value.trim()
  const buscar = q.length > 0 && !selected

  React.useEffect(() => {
    if (!buscar) return
    const controle = new AbortController()
    const espera = setTimeout(async () => {
      setCarregando(true)
      setErro(null)
      try {
        const resposta = await api.sugestoes(q, LIMITE_SUGESTOES, controle.signal)
        setSugestoes({ termo: q, lista: resposta.sugestoes })
        setActive(0)
      } catch (e) {
        if (ehCancelamento(e)) return
        setSugestoes({ termo: q, lista: [] })
        setErro(e instanceof ErroApi ? e.message : "Não foi possível carregar as sugestões.")
      } finally {
        if (!controle.signal.aborted) setCarregando(false)
      }
    }, 200)
    return () => { clearTimeout(espera); controle.abort() }
  }, [q, buscar])

  // Com as setas, a opção ativa acompanha a rolagem da lista.
  React.useEffect(() => {
    if (open) document.getElementById(`${id}-opcao-${active}`)?.scrollIntoView({ block: "nearest" })
  }, [active, open, id])

  const visiveis = buscar && sugestoes.termo === q ? sugestoes.lista : []
  const pick = (m: Medicamento) => { onSelect(m); setOpen(false); onSubmit(m) }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const show = open && visiveis.length > 0
    if (e.key === "ArrowDown" && show) { e.preventDefault(); setActive((a) => (a + 1) % visiveis.length) }
    else if (e.key === "ArrowUp" && show) { e.preventDefault(); setActive((a) => (a - 1 + visiveis.length) % visiveis.length) }
    else if (e.key === "Enter") {
      e.preventDefault()
      if (show) pick(visiveis[active])
      else { setOpen(false); onSubmit() }
    } else if (e.key === "Escape") setOpen(false)
  }

  return (
    <div className="relative flex-1">
      <Search className="pointer-events-none absolute left-3 top-5.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        id={id} value={value} placeholder={placeholder} autoComplete="off"
        role="combobox" aria-expanded={open} aria-controls={`${id}-list`}
        className="h-11 pl-9 pr-9 text-[15px]"
        onChange={(e) => { onChange(e.target.value); setOpen(true); setActive(0) }}
        onFocus={() => setOpen(true)} onBlur={(e) => { if (!e.currentTarget.parentElement?.contains(e.relatedTarget)) setOpen(false) }} onKeyDown={onKeyDown}
      />
      {carregando && <Loader2 className="absolute right-3 top-5.5 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />}
      {buscar && (open || visiveis.length === 0) && (
        <div id={`${id}-list`} role="listbox" className={cn(
          "z-30 max-h-80 overflow-y-auto overscroll-contain rounded-lg border bg-popover p-1 text-popover-foreground shadow-lg",
          visiveis.length > 0
            ? "absolute inset-x-0 top-[calc(100%+6px)]"
            : "relative mt-1.5 sm:absolute sm:inset-x-0 sm:top-[calc(100%+6px)] sm:mt-0",
          // No celular, o aviso permanece no fluxo ao sair do campo para não mover o botão durante o clique.
          !open && "sm:hidden",
        )}
          // Arrastar a barra de rolagem não pode tirar o foco do campo (fecharia a lista).
          onMouseDown={(e) => e.preventDefault()}>
          {erro ? (
            <p className="px-3 py-4 text-center text-sm text-destructive">{erro}</p>
          ) : visiveis.length === 0 ? (
            <div className="space-y-2 p-3 text-center text-sm text-muted-foreground">
              <p>{sugestoes.termo !== q || carregando ? 'Buscando…' : orientacaoAusente}</p>
            </div>
          ) : (
            <>
              <p className="px-2 pb-1 pt-1.5 text-xs font-medium text-muted-foreground">
                {visiveis.length === LIMITE_SUGESTOES
                  ? `Primeiros ${LIMITE_SUGESTOES} medicamentos; continue digitando para refinar`
                  : "Medicamentos com bula disponível"}
              </p>
              {visiveis.map((m, i) => {
                const nome = nomeLegivel(m.nome_exibicao)
                const at = fold(nome).indexOf(fold(q))
                return (
                  <div key={m.nome_normalizado} id={`${id}-opcao-${i}`} role="option" aria-selected={i === active}
                    onMouseDown={(e) => { e.preventDefault(); pick(m) }} onMouseEnter={() => setActive(i)}
                    className={cn("flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-sm", i === active && "bg-accent")}>
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-blue-50"><Pill className="size-3.5 text-blue-600" /></span>
                    <span className="flex-1 text-muted-foreground">
                      {at >= 0 ? (
                        <>{nome.slice(0, at)}<strong className="font-semibold text-foreground">{nome.slice(at, at + q.length)}</strong>{nome.slice(at + q.length)}</>
                      ) : <span className="text-foreground">{nome}</span>}
                    </span>
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/* ---------- Evidência (janela) ---------- */
interface Trecho {
  alvo: string
  destacar: string[]
  evidencia: string
  criterio?: CriterioVinculo | null
  medicamentoVinculado?: string
  fonte?: FonteBula
  processamento?: Processamento
}
interface DetalheEvidencia {
  titulo: string
  descricao: string
  trechos: Trecho[]
  fonte: FonteBula
  processamento: Processamento
  relacionados?: { lista: MedicamentoRelacionado[]; total: number }
  /** Como cada trecho liga os medicamentos (bula → nome citado → medicamento). */
  ligacoes?: Ligacao[]
}

function JanelaEvidencia({ detalhe, onClose }: { detalhe: DetalheEvidencia | null; onClose: () => void }) {
  const bulas = detalhe ? [...new Map(detalhe.trechos.map((trecho) => {
    const fonte = trecho.fonte ?? detalhe.fonte
    return [fonte.medicamento.nome_normalizado, fonte] as const
  })).values()] : []
  return (
    <Dialog open={!!detalhe} onOpenChange={(aberta) => !aberta && onClose()}>
      {detalhe && (
        <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <Badge variant="outline" className="w-fit rounded-full border-blue-200 bg-blue-50 text-blue-700"><Quote />Trecho da bula</Badge>
            <DialogTitle>{detalhe.titulo}</DialogTitle>
            <DialogDescription>{detalhe.descricao}</DialogDescription>
          </DialogHeader>
          {detalhe.ligacoes && detalhe.ligacoes.length > 0 && (
            <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50 px-3.5 py-3 text-sm text-blue-950">
              <p className="flex items-center gap-2 font-medium"><Info className="size-4 shrink-0 text-blue-600" />Como este resultado foi encontrado</p>
              {detalhe.ligacoes.map((ligacao, i) => <FluxoLigacao key={i} ligacao={ligacao} />)}
            </div>
          )}
          <div className="space-y-4">
            {detalhe.trechos.map((t, i) => (
              <div key={i}>
                <p className="mb-1.5 text-xs font-medium text-muted-foreground">
                  Bula de {nomeLegivel((t.fonte ?? detalhe.fonte).medicamento.nome_exibicao)}
                  {formatarData((t.fonte ?? detalhe.fonte).data_publicacao_bula) && ` · publicada em ${formatarData((t.fonte ?? detalhe.fonte).data_publicacao_bula)}`}
                </p>
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>Citado no trecho: <strong className="font-semibold text-foreground">{t.alvo}</strong></span>
                  {t.criterio && (
                    <Badge variant="outline" className="rounded-full">
                      <Link2 />
                      {t.medicamentoVinculado ? `Vínculo com ${t.medicamentoVinculado}: ` : ""}{CRITERIO[t.criterio]}
                    </Badge>
                  )}
                </div>
                <blockquote className="rounded-lg border bg-muted/40 p-4 text-sm leading-relaxed">
                  “<Destaque texto={compactar(t.evidencia)} termos={t.destacar} />”
                </blockquote>
              </div>
            ))}
            {detalhe.relacionados && detalhe.relacionados.lista.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-medium text-muted-foreground">
                  Medicamentos disponíveis com essa substância ({detalhe.relacionados.total})
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {detalhe.relacionados.lista.map((m) => (
                    <Badge key={m.nome_normalizado} variant="secondary" className="rounded-full" title={CRITERIO[m.criterio_vinculo]}>{nomeLegivel(m.nome_exibicao)}</Badge>
                  ))}
                  {detalhe.relacionados.total > detalhe.relacionados.lista.length && (
                    <Badge variant="outline" className="rounded-full">+{detalhe.relacionados.total - detalhe.relacionados.lista.length}</Badge>
                  )}
                </div>
              </div>
            )}
          </div>
          <DialogFooter className="items-center gap-3 sm:flex-row sm:justify-between">
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-blue-100 bg-blue-50"><FileText className="size-4 text-blue-600" /></span>
              <div className="text-sm">
                <p className="font-medium">{bulas.length === 1 ? `Bula de ${nomeLegivel(detalhe.fonte.medicamento.nome_exibicao)}` : `${bulas.length} bulas consultadas`}</p>
                <p className="text-muted-foreground">
                  {bulas.length === 1 ? [
                    formatarData(detalhe.fonte.data_publicacao_bula) && `Publicada em ${formatarData(detalhe.fonte.data_publicacao_bula)}`,
                    detalhe.fonte.numero_registro && `Registro ${detalhe.fonte.numero_registro}`,
                  ].filter(Boolean).join(" · ") : bulas.map((b) => nomeLegivel(b.medicamento.nome_exibicao)).join(" · ")}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 flex-nowrap gap-2">
              <Button variant="outline" onClick={onClose}>Fechar</Button>
              {bulas.map((b) => <a key={b.medicamento.nome_normalizado} className={cn(buttonVariants(), "bg-blue-600 hover:bg-blue-700")} href={bulaUrl(b.medicamento.nome_exibicao)} target="_blank" rel="noopener noreferrer"><Download className="size-4" />{bulas.length === 1 ? "Consultar no Bulário" : `Bula de ${nomeLegivel(b.medicamento.nome_exibicao)}`}</a>)}
            </div>
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  )
}

/* ---------- Cartões ---------- */
function CartaoInteracao({ titulo, subtitulo, resumo, extra, relacionados, onEvidence }: {
  titulo: React.ReactNode
  subtitulo: React.ReactNode
  resumo: string
  extra?: string
  relacionados?: { lista: MedicamentoRelacionado[]; total: number }
  onEvidence: () => void
}) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border border-blue-100 bg-blue-50"><ArrowLeftRight className="size-3.5 text-blue-600" /></span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold leading-snug">{titulo}</p>
          <p className="text-xs text-muted-foreground">{subtitulo}</p>
          <p className="mt-2.5 text-sm leading-relaxed text-zinc-600">{resumo}</p>
          {relacionados && relacionados.total > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">
              Presente em: {relacionados.lista.slice(0, 4).map((m) => nomeLegivel(m.nome_exibicao)).join(", ")}
              {relacionados.total > 4 && ` e mais ${relacionados.total - 4}`}
            </p>
          )}
        </div>
      </div>
      <div className="ml-10 mt-3 flex items-center justify-between gap-3 border-t pt-3">
        <span className="text-xs text-muted-foreground">{extra}</span>
        <Button variant="outline" size="sm" className="h-8 bg-background" onClick={onEvidence}><FileText className="size-3.5" />Ver evidência</Button>
      </div>
    </div>
  )
}

function BadgeEstado({ analise }: { analise: AnaliseBula }) {
  const estado = ESTADO[analise.estado]
  return <Badge variant="outline" className={cn("rounded-full", TOM_BADGE[estado.tom])} title={analise.mensagem}>{estado.rotulo}</Badge>
}

/* ---------- Resultado de um medicamento ---------- */
interface ResultadoMedicamento {
  dados: RespostaInteracoes
  relacoes: Relacao[]
  pagina: number
  carregandoMais: boolean
  erroMais: string | null
}

function agrupar<T>(itens: T[], chave: (item: T) => string): T[][] {
  const grupos = new Map<string, T[]>()
  for (const item of itens) {
    const k = chave(item)
    grupos.set(k, [...(grupos.get(k) ?? []), item])
  }
  return [...grupos.values()]
}

function relacionadosDe(relacoes: Relacao[]) {
  const vistos = new Map<string, MedicamentoRelacionado>()
  for (const r of relacoes) for (const m of r.medicamentos_relacionados) vistos.set(m.nome_normalizado, m)
  return { lista: [...vistos.values()], total: Math.max(...relacoes.map((r) => r.total_medicamentos_relacionados), vistos.size) }
}

function VistaMedicamento({ resultado, onMais, onEvidence }: {
  resultado: ResultadoMedicamento
  onMais: () => Promise<Relacao[]>
  onEvidence: (detalhe: DetalheEvidencia) => void
}) {
  const { dados, relacoes } = resultado
  const nome = nomeLegivel(dados.medicamento.nome_exibicao)
  const proprias = relacoes.filter((r) => r.sentido === "DESCRITA_NA_BULA_PESQUISADA")
  const grupos = agrupar(proprias, (r) => fold(r.alvo))
  const [detalhesVisiveis, setDetalhesVisiveis] = React.useState(false)
  const [carregadasAgora, setCarregadasAgora] = React.useState(0)
  const faltam = dados.paginacao.total - relacoes.length

  const carregarMais = async () => {
    setCarregadasAgora(0)
    const novas = await onMais()
    setCarregadasAgora(novas.length)
  }

  return (
    <section className="mt-7">
      <Card>
        <CardContent className="flex flex-wrap items-start justify-between gap-5 py-5">
          <div className="flex items-start gap-3.5">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-blue-100 bg-blue-50"><Pill className="size-5 text-blue-600" /></div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-lg font-semibold tracking-tight">{nome}</p>
                <BadgeEstado analise={dados.analise_bula} />
              </div>
              <p className="text-sm text-muted-foreground">
                {dados.total_interacoes} {dados.total_interacoes === 1 ? "interação registrada nesta bula" : "interações registradas nesta bula"}
              </p>
            </div>
          </div>
          <p className="max-w-md text-xs leading-relaxed text-muted-foreground">{dados.mensagem}</p>
        </CardContent>
        <CardContent className="space-y-4 border-t pt-5">
          <h2 className="text-sm font-semibold">Interações na bula de {nome}</h2>
          {dados.resumo_geral?.texto ? (
            <div className="space-y-3 text-sm leading-relaxed text-muted-foreground">
              {dados.resumo_geral.texto.split(/\n\s*\n/).map((paragrafo, indice) => <p key={indice}>{paragrafo}</p>)}
              <p className="text-xs">Resumo das interações identificadas nesta bula. Confira os trechos originais em “Ver evidência”.</p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{dados.total_interacoes > 0
              ? "O resumo desta bula ainda não está disponível. Você pode consultar as interações e os trechos originais nos detalhes."
              : dados.analise_bula.mensagem}</p>
          )}
          {dados.total_interacoes > 0 && <Button variant="outline" aria-expanded={detalhesVisiveis}
            aria-controls="detalhes-interacoes" onClick={() => setDetalhesVisiveis(!detalhesVisiveis)}>
            {detalhesVisiveis ? "Ocultar detalhes" : "Ver detalhes"}
          </Button>}
        </CardContent>
      </Card>

      {detalhesVisiveis && (
        <div id="detalhes-interacoes" className="mt-5">
          <p className="mb-3 text-xs text-muted-foreground">Interações identificadas no tópico de interações da bula de {nome}.</p>
          <div className="space-y-2.5">
            {grupos.map((grupo) => {
              const r = grupo[0]
              const alvo = nomeLegivel(r.alvo)
              const relacionados = relacionadosDe(grupo)
              const citacoes = citacoesUnicas(grupo)
              const resumos = [...new Map(grupo.map((item) => {
                const texto = item.resumo_card || item.resumo
                return [chaveCitacao(texto), texto]
              })).values()]
              return (
                <CartaoInteracao
                  key={r.id}
                  titulo={<>{nome} <span className="font-normal text-muted-foreground">×</span> {alvo}</>}
                  subtitulo={`Citado na bula de ${nome}`}
                  resumo={resumos.join(" ")}
                  extra={citacoes.length > 1 ? `${citacoes.length} trechos da bula` : undefined}
                  relacionados={relacionados}
                  onEvidence={() => onEvidence({
                    titulo: `${nome} × ${alvo}`,
                    descricao: `Trecho do tópico de interações da bula de ${nome} que cita ${alvo}.`,
                    trechos: citacoes.map((g) => ({ alvo: g.alvo, destacar: [g.alvo], evidencia: g.evidencia })),
                    fonte: r.fonte, processamento: r.processamento, relacionados,
                  })}
                />
              )
            })}
          </div>
          {faltam > 0 && (
            <div className="mt-5 flex flex-col items-center gap-2">
              <Button variant="outline" onClick={() => void carregarMais()} disabled={resultado.carregandoMais}>
                {resultado.carregandoMais ? <Loader2 className="size-4 animate-spin" /> : null}
                Carregar mais ({relacoes.length} de {dados.paginacao.total} trechos)
              </Button>
              {carregadasAgora > 0 && <p role="status" className="text-xs text-muted-foreground">{carregadasAgora} novos registros carregados.</p>}
              {resultado.erroMais && <p className="text-xs text-destructive">{resultado.erroMais}</p>}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

/* ---------- Resultado de um par ---------- */
function VistaPar({ dados, onEvidence }: { dados: RespostaPar; onEvidence: (detalhe: DetalheEvidencia) => void }) {
  const a = nomeLegivel(dados.medicamento_a.nome_exibicao)
  const b = nomeLegivel(dados.medicamento_b.nome_exibicao)
  const evidencias = evidenciasUnicas(dados.evidencias)
  const mencoes = evidenciasUnicas(dados.mencoes_nao_vinculadas)
  const alertas = evidenciasUnicas(dados.alertas_nao_confirmados)
  // Composto em que a bula cita só parte dos componentes: o veredito diz qual.
  const parciais = ligacoesDoPar(dados, evidencias).filter(citaParteDoComposto)
  const componentesParciais = [...new Set(parciais.flatMap((l) => l.componentesCitados.map((c) => c.toLowerCase())))]
  const veredito = dados.interacao_identificada === true
    ? {
        Icone: AlertTriangle,
        titulo: componentesParciais.length > 0
          ? `Interação identificada pelo${componentesParciais.length > 1 ? "s componentes" : " componente"} ${lista(componentesParciais)}`
          : "Interação identificada",
        classe: "border-amber-200 bg-amber-50", icone: "text-amber-600",
      }
    : dados.interacao_identificada === false
      ? { Icone: Info, titulo: "Nenhuma interação identificada", classe: "border-zinc-200 bg-zinc-50", icone: "text-zinc-500" }
      : { Icone: HelpCircle, titulo: "Sem conclusão", classe: "border-zinc-200 bg-zinc-50", icone: "text-zinc-500" }

  const cartao = (trechos: EvidenciaPar[]) => {
    const origens = [...new Set(trechos.map((ev) => nomeLegivel(ev.fonte.medicamento.nome_exibicao)))]
    const resumos = [...new Map(trechos.flatMap((ev) => ev.itens).map((item) => {
      const texto = item.resumo_card || item.resumo
      return [chaveCitacao(texto), texto]
    })).values()]
    const ligacoes = ligacoesDoPar(dados, trechos)
    const origemTexto = `${origens.length === 1 ? "Citado na bula de" : "Citado nas bulas de"} ${origens.join(" e ")}`
    return (
      <CartaoInteracao
        titulo={<>{a} <span className="font-normal text-muted-foreground">×</span> {b}</>}
        subtitulo={ligacoes.length > 0
          ? <span className="flex flex-col items-start gap-0.5">
              {ligacoes.map((ligacao, i) => (
                <span key={i} className="flex flex-col items-start">
                  <ExplicacaoLigacao ligacao={ligacao} />
                  <AvisoNaoCitados ligacao={ligacao} />
                </span>
              ))}
            </span>
          : origemTexto}
        resumo={resumos.join(" ") || compactar(trechos[0].evidencia)}
        extra={`${trechos.length} ${trechos.length === 1 ? "trecho da bula" : "trechos das bulas"}`}
        onEvidence={() => onEvidence({
          titulo: `${a} × ${b}`,
          descricao: `${trechos.length === 1 ? "Trecho" : "Trechos"} do tópico de interações ${origens.length === 1 ? "da bula" : "das bulas"} de ${lista(origens)}.`,
          ligacoes,
          trechos: trechos.map((ev) => {
            const alvos = [...new Set(ev.itens.map((it) => it.alvo))]
            return { alvo: alvos.join(", "), destacar: alvos, evidencia: ev.evidencia,
              criterio: ev.itens[0]?.criterio_vinculo,
              medicamentoVinculado: ev.sentido === "BULA_A_CITA_B" ? b : a,
              fonte: ev.fonte, processamento: ev.processamento }
          }),
          fonte: trechos[0].fonte, processamento: trechos[0].processamento,
        })}
      />
    )
  }

  return (
    <section className="mt-7 space-y-5">
      <div className={cn("rounded-xl border p-5", veredito.classe)}>
        <div className="flex items-start gap-3">
          <veredito.Icone className={cn("mt-0.5 size-5 shrink-0", veredito.icone)} />
          <div className="flex-1">
            <p className="font-semibold">{a} × {b}: {veredito.titulo}</p>
            <p className="mt-1 text-sm leading-relaxed text-zinc-600">
              {dados.interacao_identificada === true && parciais.length > 0
                ? parciais.map((ligacao, i) => (
                    <React.Fragment key={i}>
                      {i > 0 && " "}
                      <ExplicacaoLigacao ligacao={ligacao} />{" "}
                      {partesDaLigacao(ligacao).naoCitados.charAt(0).toUpperCase() + partesDaLigacao(ligacao).naoCitados.slice(1)}{" "}
                      {ligacao.naoCitados.length === 1 ? "não é citado" : "não são citados"} nesse trecho.
                    </React.Fragment>
                  ))
                : dados.mensagem}
            </p>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">{a}: <BadgeEstado analise={dados.analise_a} /></span>
              <span className="inline-flex items-center gap-1.5">{b}: <BadgeEstado analise={dados.analise_b} /></span>
            </div>
          </div>
        </div>
      </div>

      {evidencias.length > 0 && (
        <div>
          <p className="mb-2.5 text-sm font-semibold">Evidências nas bulas</p>
          {cartao(evidencias)}
        </div>
      )}
      {mencoes.length > 0 && (
        <div>
          <p className="text-sm font-semibold">Menções sem vínculo</p>
          <p className="mb-2.5 text-xs text-muted-foreground">Uma bula cita um nome contido nos nomes do outro medicamento, mas sem vínculo gravado. Confira o trecho.</p>
          {cartao(mencoes)}
        </div>
      )}
      {alertas.length > 0 && (
        <div>
          <p className="text-sm font-semibold">Alertas sobre classes</p>
          <p className="mb-2.5 text-xs text-muted-foreground">Citam uma classe de medicamentos; não confirmam interação específica com o outro medicamento.</p>
          {cartao(alertas)}
        </div>
      )}
    </section>
  )
}

interface ResultadoPar {
  a: Medicamento
  b: Medicamento
  dados?: RespostaPar
  erro?: string
}

function VistaComparacao({ resultados, onEvidence }: {
  resultados: ResultadoPar[]
  onEvidence: (detalhe: DetalheEvidencia) => void
}) {
  const identificadas = resultados.filter((r) => r.dados?.interacao_identificada === true).length
  const naoIdentificadas = resultados.filter((r) => r.dados?.interacao_identificada === false).length
  const semConclusao = resultados.filter((r) => r.dados?.interacao_identificada === null).length
  const falhas = resultados.filter((r) => r.erro).length
  const detalhados = resultados.filter((r) => r.dados?.interacao_identificada !== false)
  const negativos = resultados.filter((r) => r.dados?.interacao_identificada === false)
  return (
    <section className="mt-7">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 py-5">
          <div>
            <p className="font-semibold">Interações entre os medicamentos selecionados</p>
            <p className="text-sm text-muted-foreground">{resultados.length} {resultados.length === 1 ? "par consultado" : "pares consultados"}</p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge variant="outline" className="border-amber-200 bg-amber-50">{identificadas} {identificadas === 1 ? "par com interação identificada" : "pares com interação identificada"}</Badge>
            <Badge variant="outline">{naoIdentificadas} {naoIdentificadas === 1 ? "par sem interação direta identificada" : "pares sem interação direta identificada"}</Badge>
            {semConclusao > 0 && <Badge variant="outline">{semConclusao} sem conclusão</Badge>}
            {falhas > 0 && <Badge variant="outline">{falhas} com erro na consulta</Badge>}
          </div>
        </CardContent>
      </Card>
      {detalhados.map((resultado) => {
        const chave = `${resultado.a.nome_normalizado}-${resultado.b.nome_normalizado}`
        return resultado.dados
          ? <VistaPar key={chave} dados={resultado.dados} onEvidence={onEvidence} />
          : <div key={chave} className="mt-5 rounded-xl border border-destructive/30 bg-destructive/5 p-5">
              <p className="font-semibold">{nomeLegivel(resultado.a.nome_exibicao)} × {nomeLegivel(resultado.b.nome_exibicao)}: consulta indisponível</p>
              <p className="mt-1 text-sm text-muted-foreground">{resultado.erro}</p>
            </div>
      })}
      {negativos.length > 0 && (
        <div className="mt-5 rounded-xl border bg-zinc-50 p-5">
          <div className="flex items-start gap-3">
            <Info className="mt-0.5 size-5 shrink-0 text-zinc-500" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold">Pares sem interação direta identificada</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Cada linha abaixo se refere somente àquele par. Ela não altera as interações identificadas nos outros pares selecionados.
              </p>
              <div className="mt-3 divide-y rounded-lg border bg-background">
                {negativos.map((resultado) => (
                  <div key={`${resultado.a.nome_normalizado}-${resultado.b.nome_normalizado}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-sm">
                    <span className="font-medium">{nomeLegivel(resultado.a.nome_exibicao)} × {nomeLegivel(resultado.b.nome_exibicao)}</span>
                    <span className="text-xs text-muted-foreground">Nenhuma interação direta identificada nas fontes analisadas</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

/* ---------- Página ---------- */
type Estado =
  | { tipo: "inicio" }
  | { tipo: "carregando"; concluido?: number; total?: number }
  | { tipo: "erro"; mensagem: string; semConexao: boolean }
  | ({ tipo: "medicamento" } & ResultadoMedicamento)
  | { tipo: "comparacao"; resultados: ResultadoPar[] }

export default function InteraMed() {
  const [texto, setTexto] = React.useState("")
  const [selecionado, setSelecionado] = React.useState<Medicamento | null>(null)
  const [textoComparacao, setTextoComparacao] = React.useState("")
  const [comparados, setComparados] = React.useState<Medicamento[]>([])
  const [erroComparacao, setErroComparacao] = React.useState<string | null>(null)
  const [compareOn, setCompareOn] = React.useState(false)
  const [estado, setEstado] = React.useState<Estado>({ tipo: "inicio" })
  const [evidencia, setEvidencia] = React.useState<DetalheEvidencia | null>(null)
  const controle = React.useRef<AbortController | null>(null)
  const controleMais = React.useRef<AbortController | null>(null)

  const escolherA = (m: Medicamento) => { setSelecionado(m); setTexto(nomeLegivel(m.nome_exibicao)) }

  const adicionarComparado = async (escolhido?: Medicamento) => {
    try {
      const medicamento = escolhido ?? await resolver(textoComparacao, null, new AbortController().signal)
      if (!medicamento) {
        if (textoComparacao.trim()) {
          setErroComparacao(MEDICAMENTO_AUSENTE)
        }
        return
      }
      if (medicamento.nome_normalizado === selecionado?.nome_normalizado || comparados.some((m) => m.nome_normalizado === medicamento.nome_normalizado)) {
        setErroComparacao("Este medicamento já está selecionado para a consulta de interações.")
        return
      }
      setComparados((atual) => [...atual, medicamento])
      setTextoComparacao("")
      setErroComparacao(null)
    } catch (e) {
      setErroComparacao(e instanceof ErroApi ? e.message : "Não foi possível adicionar o medicamento.")
    }
  }

  // A sugestão aproximada exige escolha explícita; nunca consulta outro medicamento automaticamente.
  const resolver = async (valor: string, escolhido: Medicamento | null, sinal: AbortSignal) => {
    if (escolhido) return escolhido
    if (!valor.trim()) return null
    const resposta = await api.sugestoes(valor.trim(), LIMITE_SUGESTOES, sinal)
    const normal = (v: string) => fold(v).replace(/\s+/g, ' ').trim()
    return resposta.sugestoes.find((m) => normal(m.nome_normalizado) === normal(valor)
      || normal(m.nome_exibicao) === normal(valor)) ?? null
  }

  const buscar = async (escolhidos: { a?: Medicamento; medicamentos?: Medicamento[]; comparar?: boolean } = {}) => {
    if (!escolhidos.a && !escolhidos.medicamentos?.length && !selecionado && !texto.trim()) return
    controle.current?.abort()
    controleMais.current?.abort()
    const atual = new AbortController()
    controle.current = atual
    setEstado({ tipo: "carregando" })
    try {
      const a = escolhidos.medicamentos?.[0] ?? escolhidos.a ?? await resolver(texto, selecionado, atual.signal)
      if (!a) {
        setEstado({ tipo: 'erro', mensagem: MEDICAMENTO_AUSENTE, semConexao: false })
        return
      }
      escolherA(a)
      const comparar = escolhidos.comparar ?? (compareOn && (comparados.length > 0 || !!textoComparacao.trim()))
      let medicamentos = escolhidos.medicamentos ?? [a]
      if (comparar && !escolhidos.medicamentos) {
        const pendente = await resolver(textoComparacao, null, atual.signal)
        if (textoComparacao.trim() && !pendente) {
          setErroComparacao(MEDICAMENTO_AUSENTE)
          setEstado({ tipo: 'erro', mensagem: MEDICAMENTO_AUSENTE, semConexao: false })
          return
        }
        medicamentos = [a, ...comparados, ...(pendente ? [pendente] : [])]
        if (pendente) { setComparados((itens) => [...itens, pendente]); setTextoComparacao('') }
      }
      if (new Set(medicamentos.map((m) => m.nome_normalizado)).size !== medicamentos.length) {
        setEstado({ tipo: 'erro', mensagem: 'Cada medicamento deve aparecer apenas uma vez na consulta de interações.', semConexao: false })
        return
      }
      if (comparar) {
        const pares = paresDeMedicamentos(medicamentos)
        if (new Set(medicamentos.map((m) => m.nome_normalizado)).size !== medicamentos.length) {
          setEstado({ tipo: "erro", mensagem: "Cada medicamento deve aparecer apenas uma vez na consulta de interações.", semConexao: false })
          return
        }
        const resultados: ResultadoPar[] = new Array(pares.length)
        let proximo = 0
        let concluido = 0
        setEstado({ tipo: "carregando", concluido, total: pares.length })
        const trabalhador = async () => {
          while (proximo < pares.length && !atual.signal.aborted) {
            const indice = proximo++
            const [primeiro, segundo] = pares[indice]
            try {
              const dados = await api.par(primeiro.nome_normalizado, segundo.nome_normalizado, atual.signal)
              resultados[indice] = { a: primeiro, b: segundo, dados }
            } catch (e) {
              if (ehCancelamento(e)) return
              resultados[indice] = { a: primeiro, b: segundo, erro: e instanceof ErroApi ? e.message : "Erro inesperado ao consultar este par." }
            }
            concluido++
            if (!atual.signal.aborted) setEstado({ tipo: "carregando", concluido, total: pares.length })
          }
        }
        await Promise.all(Array.from({ length: Math.min(4, pares.length) }, trabalhador))
        if (!atual.signal.aborted) setEstado({ tipo: "comparacao", resultados })
      } else {
        const dados = await api.interacoes(a.nome_normalizado, 1, TAMANHO_PAGINA, atual.signal)
        setEstado({ tipo: "medicamento", dados, relacoes: dados.relacoes, pagina: 1, carregandoMais: false, erroMais: null })
      }
    } catch (e) {
      if (ehCancelamento(e) || atual.signal.aborted) return
      setEstado({
        tipo: "erro",
        mensagem: e instanceof ErroApi ? e.message : "Erro inesperado ao consultar a API.",
        semConexao: e instanceof ErroApi && e.status === 0,
      })
    }
  }

  const carregarMais = async (): Promise<Relacao[]> => {
    if (estado.tipo !== "medicamento" || estado.carregandoMais) return []
    const anterior = estado
    const atual = new AbortController()
    controleMais.current = atual
    setEstado({ ...anterior, carregandoMais: true, erroMais: null })
    try {
      const proxima = await api.interacoes(anterior.dados.medicamento.nome_normalizado, anterior.pagina + 1, TAMANHO_PAGINA, atual.signal)
      if (atual.signal.aborted) return []
      setEstado((vigente) => vigente.tipo === "medicamento" && vigente.dados.medicamento.nome_normalizado === anterior.dados.medicamento.nome_normalizado && vigente.pagina === anterior.pagina
        ? { ...vigente, relacoes: [...vigente.relacoes, ...proxima.relacoes], pagina: vigente.pagina + 1, carregandoMais: false, erroMais: null }
        : vigente)
      return proxima.relacoes
    } catch (e) {
      if (ehCancelamento(e) || atual.signal.aborted) return []
      setEstado((vigente) => vigente.tipo === "medicamento" && vigente.dados.medicamento.nome_normalizado === anterior.dados.medicamento.nome_normalizado
        ? { ...vigente, carregandoMais: false, erroMais: e instanceof ErroApi ? e.message : "Não foi possível carregar mais resultados." }
        : vigente)
      return []
    }
  }

  return (
    <div className="interamed-app min-h-screen bg-background text-foreground antialiased">
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur">
        <div className="interamed-container mx-auto flex w-full max-w-[1456px] items-center justify-between px-6 py-3.5">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 items-center justify-center rounded-lg bg-blue-600"><Pill className="size-4 text-white" /></div>
            <span className="text-[17px] font-semibold tracking-tight">InteraMed</span>
          </div>
        </div>
      </header>

      <section className="relative overflow-hidden border-b">
        <DottedSurface size={3} opacity={0.48} sizeAttenuation={false} className="z-0 [mask-image:linear-gradient(to_bottom,#000_20%,transparent_100%)]" />
        <div className="absolute inset-0 z-1 bg-gradient-to-b from-background/0 via-background/10 to-background/65" />
        <div className="interamed-container relative z-10 mx-auto w-full max-w-[1456px] px-6 pb-28 pt-16 text-center">
          <Badge variant="outline" className="mb-5 gap-2 rounded-full border-blue-200 bg-blue-50 px-3 py-1 text-blue-700"><ShieldCheck className="size-3.5" />Verificador de interações medicamentosas</Badge>
          <h1 className="mx-auto max-w-2xl text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
            Verifique interações com <span className="bg-gradient-to-r from-blue-600 to-sky-500 bg-clip-text text-transparent">segurança</span>
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">Pesquise um medicamento ou selecione vários para verificar as interações entre eles.</p>
        </div>
      </section>

      <main className="interamed-container relative mx-auto -mt-20 w-full max-w-[1456px] px-6 pb-16">
        {/* overflow-visible: a lista de sugestões passa da borda do card sem ser cortada. */}
        <Card className="relative z-20 overflow-visible! shadow-xl shadow-zinc-900/5">
          <CardContent className="space-y-4 pt-6">
            <div className="flex items-center justify-between">
              <Label htmlFor="med">Medicamento</Label>
              <span className="text-xs text-muted-foreground">Use ↑ ↓ para navegar nas sugestões</span>
            </div>
            <div className="flex flex-col gap-2.5 sm:flex-row">
              <MedicineInput
                id="med" value={texto} selected={selecionado} placeholder="Digite o medicamento (ex.: Marevan)"
                onChange={(v) => { setTexto(v); setSelecionado(null) }}
                onSelect={escolherA} onSubmit={(m) => { if (!compareOn) void buscar({ a: m }) }}
                orientacaoAusente={MEDICAMENTO_AUSENTE}
              />
              <Button className="h-11 bg-blue-600 px-5 hover:bg-blue-700" onClick={() => void buscar()} disabled={estado.tipo === "carregando"}>
                {estado.tipo === "carregando" ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}Consultar interações
              </Button>
            </div>
            {compareOn && (
              <div className="space-y-2.5">
                <Label htmlFor="cmp">Outros medicamentos</Label>
                {comparados.length > 0 && (
                  <div className="flex flex-wrap gap-2" aria-label="Medicamentos selecionados para verificar interações">
                    {comparados.map((m) => (
                      <Badge key={m.nome_normalizado} variant="secondary" className="gap-1.5 rounded-full py-1 pl-3 pr-1">
                        {nomeLegivel(m.nome_exibicao)}
                        <button type="button" className="rounded-full p-1 hover:bg-muted-foreground/15" aria-label={`Remover ${nomeLegivel(m.nome_exibicao)} da consulta de interações`}
                          onClick={() => setComparados((atual) => atual.filter((item) => item.nome_normalizado !== m.nome_normalizado))}>
                          <X className="size-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                )}
                <div className="flex flex-col gap-2 sm:flex-row">
                  <MedicineInput
                    id="cmp" value={textoComparacao} selected={null} placeholder="Adicione outro medicamento para verificar interações"
                    onChange={(v) => { setTextoComparacao(v); setErroComparacao(null) }}
                    onSelect={() => {}} onSubmit={(m) => void adicionarComparado(m)}
                    orientacaoAusente={MEDICAMENTO_AUSENTE}
                  />
                  <Button variant="outline" className="h-11" onClick={() => void adicionarComparado()} disabled={!textoComparacao.trim()}>Adicionar</Button>
                </div>
                {erroComparacao && <p role="alert" className="text-xs text-destructive">{erroComparacao}</p>}
                <p className="text-xs text-muted-foreground">Adicione os medicamentos e clique em Consultar interações. O sistema verificará as interações registradas entre cada par selecionado.</p>
              </div>
            )}
          </CardContent>
          <CardFooter className="flex items-center rounded-b-xl border-t bg-muted/40 py-3">
            <div className="flex items-center gap-2.5">
              <Switch id="cmp-toggle" checked={compareOn} onCheckedChange={setCompareOn} />
              <Label htmlFor="cmp-toggle" className="text-sm leading-snug">Verificar interações entre medicamentos</Label>
            </div>
          </CardFooter>
        </Card>


        {estado.tipo === "carregando" && (
          <div className="mt-7 space-y-3">
            <p role="status" className="text-sm text-muted-foreground">{estado.total !== undefined ? `Consultando interações: ${estado.concluido} de ${estado.total} pares` : "Consultando interações…"}</p>
            <Skeleton className="h-24 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" />
          </div>
        )}

        {estado.tipo === "inicio" && (
          <section className="mt-10">
            <p className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground/70">Como funciona</p>
            <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {([
                [Pill, "Escolha um medicamento", "Busque pelo nome comercial ou princípio ativo e selecione uma sugestão."],
                [ArrowLeftRight, "Escolha a consulta", "Consulte um medicamento ou adicione outros para verificar as interações entre eles."],
                [Sparkles, "Veja os resultados", "Confira quais medicamentos foram relacionados e em qual bula a interação foi encontrada."],
                [BookOpen, "Leia a evidência", "Abra o trecho da bula e acesse o documento completo no Bulário Eletrônico."],
              ] as const).map(([Icone, titulo, desc], i) => (
                <li key={titulo} className="rounded-xl border border-border/70 bg-card/80 p-4 shadow-sm transition-colors hover:border-blue-200 hover:bg-blue-50/30">
                  <div className="flex items-center justify-between">
                    <span className="flex size-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><Icone className="size-4" /></span>
                    <span className="text-xs font-semibold text-blue-600/80">0{i + 1}</span>
                  </div>
                  <p className="mt-3 text-sm font-semibold">{titulo}</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{desc}</p>
                </li>
              ))}
            </ol>
            <div className="mt-4 flex items-center gap-3 rounded-xl border border-dashed border-blue-200 bg-blue-50/40 px-4 py-3 text-xs text-muted-foreground">
              <ArrowRight className="size-4 shrink-0 text-blue-600" />
              <span><strong className="font-semibold text-foreground">Exemplo:</strong> Ibuprofeno + outro medicamento → interação encontrada → trecho da bula para conferência.</span>
            </div>
          </section>
        )}

        {estado.tipo === "erro" && (
          <div className="mt-7 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-8 text-center">
            <div className="mx-auto mb-3.5 flex size-11 items-center justify-center rounded-lg bg-destructive/10">
              {estado.semConexao ? <WifiOff className="size-5 text-destructive" /> : <AlertTriangle className="size-5 text-destructive" />}
            </div>
            <p className="font-semibold">{estado.semConexao ? "API indisponível" : "Não foi possível concluir a consulta"}</p>
            <p className="mx-auto mt-1 max-w-lg text-sm text-muted-foreground">{estado.mensagem}</p>
            {estado.semConexao && (
              <p className="mx-auto mt-3 max-w-lg font-mono text-xs text-muted-foreground">apps\api_interacoes\.venv\Scripts\python -m apps.api_interacoes</p>
            )}
          </div>
        )}

        {estado.tipo === "medicamento" && (
          <VistaMedicamento
            key={estado.dados.medicamento.nome_normalizado}
            resultado={estado} onMais={carregarMais} onEvidence={setEvidencia}
          />
        )}

        {estado.tipo === "comparacao" && <VistaComparacao resultados={estado.resultados} onEvidence={setEvidencia} />}

        <footer className="mt-14 flex flex-wrap justify-between gap-4 border-t pt-5 text-xs text-muted-foreground">
          <span>InteraMed</span>
          <span className="max-w-lg">Fonte: tópico de interações das bulas profissionais publicadas pela Anvisa. A ausência de resultado não garante ausência de interação. Não substitui a orientação de um médico ou farmacêutico.</span>
        </footer>
      </main>

      <JanelaEvidencia detalhe={evidencia} onClose={() => setEvidencia(null)} />
    </div>
  )
}
