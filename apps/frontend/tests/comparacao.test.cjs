/* eslint-disable @typescript-eslint/no-require-imports -- Testes executados pelo Node. */
const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

function load(file, dependencies = {}) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const exports = {}
  vm.runInNewContext(js, { exports, require: (id) => dependencies[id] ?? require(id) })
  return exports
}
const data = load('lib/interacoes-data.ts')
const { paresDeMedicamentos, montarLigacao, citaParteDoComposto } = load('lib/comparacao.ts', { '@/lib/interacoes-data': data })
const med = (name, componentes = []) => ({ nome_normalizado: name, nome_exibicao: name, principios_ativos: componentes })

test('seleção múltipla consulta todos os pares uma vez mesmo com repetição', () => {
  const pairs = paresDeMedicamentos([med('a'), med('b'), med('c'), med('a')])
  assert.equal(JSON.stringify(pairs.map(([a,b]) => [a.nome_normalizado,b.nome_normalizado])), JSON.stringify([['a','b'],['a','c'],['b','c']]))
})

test('componente citado não atribui a evidência aos demais componentes', () => {
  const link = montarLigacao(med('origem'), med('associação', ['paracetamol','cafeína']), [{ alvo: 'paracetamol' }])
  assert.equal(JSON.stringify(link.componentesCitados), JSON.stringify(['paracetamol']))
  assert.equal(JSON.stringify(link.naoCitados), JSON.stringify(['cafeína']))
  assert.equal(citaParteDoComposto(link), true)
})

test('vínculo por nome ou equivalência não inventa fundamento de componente', () => {
  const link = montarLigacao(med('origem'), med('marca', ['cloridrato de substância','cafeína']), [{ alvo: 'marca' }])
  assert.equal(link.componentesCitados.length, 0)
  assert.equal(link.naoCitados.length, 0)
  assert.equal(citaParteDoComposto(link), false)
})
