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

function psQuote(value) {
  return `'${String(value).replaceAll("'", "''")}'`
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
    const stdoutDir = join(runDir, "stdout")
    const stderrDir = join(runDir, "stderr")
    await mkdir(stdoutDir, { recursive: true })
    await mkdir(stderrDir, { recursive: true })
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
      const bootstrap = {
        mode: "async",
        project,
        runner: runnerScript,
        prompt_file: promptFile,
        run_dir: runDir,
        args: psArgs,
        started_at: new Date().toISOString(),
      }
      await writeFile(join(runDir, "bootstrap.json"), JSON.stringify(bootstrap, null, 2), "utf8")
      await writeFile(
        join(runDir, "run.jsonl"),
        `${JSON.stringify({
          time: new Date().toISOString(),
          state: "BOOTSTRAP",
          status: "STARTED",
          details: {
            project,
            runner: runnerScript,
            prompt_file: promptFile,
          },
        })}\n`,
        "utf8",
      )
      const bootstrapStdout = join(stdoutDir, "00-bootstrap.stdout.txt")
      const bootstrapStderr = join(stderrDir, "00-bootstrap.stderr.txt")
      const launchScript = join(runDir, "bootstrap-launch.ps1")
      const launchContent = [
        "$ErrorActionPreference = 'Stop'",
        "$argsList = @(",
        "  '-NoProfile',",
        "  '-ExecutionPolicy', 'Bypass',",
        `  '-File', ${psQuote(runnerScript)},`,
        `  '-Project', ${psQuote(project)},`,
        `  '-PromptFile', ${psQuote(promptFile)},`,
        `  '-MaxRepairs', ${psQuote(String(maxRepairs))},`,
        `  '-RunDir', ${psQuote(runDir)}`,
        ")",
        `$p = Start-Process -FilePath 'powershell' -ArgumentList $argsList -RedirectStandardOutput ${psQuote(bootstrapStdout)} -RedirectStandardError ${psQuote(bootstrapStderr)} -WindowStyle Hidden -PassThru`,
        "Write-Output $p.Id",
      ].join("\n")
      await writeFile(launchScript, launchContent, "utf8")
      const launch = await runProcess("powershell", [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        launchScript,
      ])
      const pid = (launch.stdout || "").trim().split(/\s+/).pop() || "unknown"
      if (launch.code !== 0) {
        await writeFile(join(stderrDir, "00-launch-error.txt"), `${launch.stdout}\n${launch.stderr}\n`, "utf8")
        return [
          "OPALE_RUN_MODE: async",
          "OPALE_CONTROL: launch_failed",
          "LOCAL_TEAM_NEXT_ACTION: report_failure_only; do_not_fallback_to_manual_agents",
          `PROJECT: ${project}`,
          `RUNNER: ${runnerScript}`,
          `PROMPT_FILE: ${promptFile}`,
          `RUN_DIR: ${runDir}`,
          `LAUNCH_EXIT_CODE: ${launch.code}`,
          `${launch.stdout || ""}${launch.stderr || ""}`.trim(),
        ].join("\n")
      }
      return [
        "OPALE_RUN_MODE: async",
        "OPALE_CONTROL: transferred_to_runner",
        "LOCAL_TEAM_NEXT_ACTION: stop_after_reporting_paths; do_not_call_task_agents",
        `PID: ${pid}`,
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
      "OPALE_CONTROL: runner_finished",
      "LOCAL_TEAM_NEXT_ACTION: report_runner_output_only; do_not_fallback_to_manual_agents",
      `OPALE_RUN_EXIT_CODE: ${result.code}`,
      `PROJECT: ${project}`,
      `RUNNER: ${runnerScript}`,
      `PROMPT_FILE: ${promptFile}`,
      `RUN_DIR: ${runDir}`,
      output || "Aucune sortie du runner.",
    ].join("\n")
  },
})
