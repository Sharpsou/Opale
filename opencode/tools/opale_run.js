import { tool } from "@opencode-ai/plugin"
import { mkdir, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { spawn } from "node:child_process"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { randomUUID } from "node:crypto"

const currentDir = dirname(fileURLToPath(import.meta.url))
const runnerScript = resolve(currentDir, "..", "opale-runner", "opale.ps1")

function timestamp() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, "0")
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

function runProcess(command, args) {
  return new Promise((resolvePromise) => {
    const child = spawn(command, args, {
      windowsHide: true,
      shell: false,
    })
    let stdout = ""
    let stderr = ""

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString()
    })
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString()
    })
    child.on("error", (error) => {
      resolvePromise({
        code: 1,
        stdout,
        stderr: `${stderr}\n${error.message}`,
      })
    })
    child.on("close", (code) => {
      resolvePromise({
        code: code ?? 1,
        stdout,
        stderr,
      })
    })
  })
}

export default tool({
  description:
    "Lance la machine d'etat OPALE globale pour realiser un projet complet dans le repertoire courant ou indique.",
  args: {
    prompt: tool.schema.string().describe("Demande utilisateur complete a executer par OPALE."),
    project: tool.schema
      .string()
      .optional()
      .describe("Repertoire projet cible. Par defaut, le repertoire courant OpenCode."),
    max_repairs: tool.schema
      .number()
      .optional()
      .describe("Nombre maximal de tentatives de correction. Defaut: 2."),
    async: tool.schema
      .boolean()
      .optional()
      .describe("Si true, lance le runner en arriere-plan et retourne immediatement les chemins de suivi."),
  },
  async execute(args, context) {
    const project = args.project || context.directory
    const maxRepairs = args.max_repairs ?? 2
    const asyncMode = args.async ?? false
    const promptDir = join(tmpdir(), "opale-prompts")
    await mkdir(promptDir, { recursive: true })
    const promptFile = join(promptDir, `${randomUUID()}.txt`)
    await writeFile(promptFile, args.prompt, "utf8")
    const runDir = join(project, ".opale", "runs", timestamp())
    const psArgs = [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      runnerScript,
      "-Project",
      project,
      "-PromptFile",
      promptFile,
      "-MaxRepairs",
      String(maxRepairs),
      "-RunDir",
      runDir,
    ]

    if (asyncMode) {
      const child = spawn("powershell", psArgs, {
        detached: true,
        windowsHide: true,
        shell: false,
        stdio: "ignore",
      })
      child.unref()
      return [
        "OPALE_RUN_MODE: async",
        `PID: ${child.pid}`,
        `PROJECT: ${project}`,
        `RUNNER: ${runnerScript}`,
        `PROMPT_FILE: ${promptFile}`,
        `RUN_DIR: ${runDir}`,
        `FOLLOW: Get-Content "${runDir}\\run.jsonl" -Wait`,
      ].join("\n")
    }

    const result = await runProcess("powershell", psArgs)
    const output = `${result.stdout || ""}${result.stderr || ""}`.trim()
    return [
      "OPALE_RUN_MODE: sync",
      `OPALE_RUN_EXIT_CODE: ${result.code}`,
      `PROJECT: ${project}`,
      `RUNNER: ${runnerScript}`,
      `PROMPT_FILE: ${promptFile}`,
      `RUN_DIR: ${runDir}`,
      output || "Aucune sortie du runner.",
    ].join("\n")
  },
})
