/* eslint-disable @typescript-eslint/no-require-imports -- Este lançador é CommonJS para funcionar com o Node portátil. */
const { spawn } = require("child_process")
const fs = require("fs")
const path = require("path")

const root = path.resolve(__dirname, "..")
const [major, minor] = process.versions.node.split(".").map(Number)
const currentIsSupported = major > 20 || (major === 20 && minor >= 9)
const portableNode = path.join(root, ".tools", "node-v22.23.3-win-x64", "node.exe")

let node = process.execPath
if (!currentIsSupported) {
  if (!fs.existsSync(portableNode)) {
    console.error("O frontend requer Node.js 20.9 ou superior. Instale uma versão atual do Node.js e tente novamente.")
    process.exit(1)
  }
  node = portableNode
  console.log(`Usando Node.js portátil (${node})`)
}

const next = path.join(root, "node_modules", "next", "dist", "bin", "next")
const nodeDirectory = path.dirname(node)
const env = { ...process.env }
const pathKey = Object.keys(env).find((key) => key.toLowerCase() === "path") || "PATH"
env[pathKey] = nodeDirectory + path.delimiter + (env[pathKey] || "")
// npm pode definir NODE com o Node antigo; os processos de compilação herdam esse valor.
env.NODE = node
env.npm_node_execpath = node
const child = spawn(node, [next, ...process.argv.slice(2)], {
  cwd: root,
  stdio: "inherit",
  env,
})

child.on("error", (error) => {
  console.error(`Não foi possível iniciar o Next.js: ${error.message}`)
  process.exitCode = 1
})
child.on("exit", (code, signal) => {
  process.exitCode = code === null ? (signal ? 1 : 0) : code
})
