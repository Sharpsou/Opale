import { tool } from "@opencode-ai/plugin"
import { readdir, readFile, stat } from "node:fs/promises"
import { join, resolve } from "node:path"

async function exists(path) {
  try {
    await stat(path)
    return true
  } catch {
    return false
  }
}

async function latestRunDir(project) {
  const runsRoot = join(project, ".opale", "runs")
  const entries = await readdir(runsRoot, { withFileTypes: true })
  const dirs = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
  if (!dirs.length) {
    throw new Error(`Aucun run OPALE trouve dans ${runsRoot}`)
  }
  return join(runsRoot, dirs[dirs.length - 1])
}

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"))
}

async function readJsonlTail(path, count) {
  if (!(await exists(path))) return []
  const content = await readFile(path, "utf8")
  return content
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(-count)
    .map((line) => {
      try {
        return JSON.parse(line)
      } catch {
        return { raw: line }
      }
    })
}

function formatStates(states) {
  if (!states.length) return "- aucun etat lu"
  return states
    .map((event) => {
      const state = event.state || "UNKNOWN"
      const status = event.status || "UNKNOWN"
      const details = event.details ? ` ${JSON.stringify(event.details)}` : ""
      return `- ${state}: ${status}${details}`
    })
    .join("\n")
}

export default tool({
  description:
    "Lit l'avancement ou le resultat d'un run OPALE depuis .opale/runs et le resume dans le chat.",
  args: {
    project: tool.schema
      .string()
      .optional()
      .describe("Repertoire projet cible. Par defaut, le repertoire courant OpenCode."),
    run_dir: tool.schema
      .string()
      .optional()
      .describe("Dossier exact du run OPALE. Si absent, utilise le dernier run du projet."),
  },
  async execute(args, context) {
    const project = resolve(args.project || context.directory)
    const runDir = resolve(args.run_dir || (await latestRunDir(project)))
    const summaryPath = join(runDir, "summary.json")
    const runJsonlPath = join(runDir, "run.jsonl")
    const hasSummary = await exists(summaryPath)
    const tail = await readJsonlTail(runJsonlPath, 12)

    if (!hasSummary) {
      return [
        "OPALE_STATUS: RUNNING_OR_INCOMPLETE",
        `PROJECT: ${project}`,
        `RUN_DIR: ${runDir}`,
        "SUMMARY_JSON: absent",
        "",
        "Derniers evenements:",
        formatStates(tail),
        "",
        `FOLLOW: Get-Content "${runJsonlPath}" -Wait`,
      ].join("\n")
    }

    const summary = await readJson(summaryPath)
    const filesChanged = Array.isArray(summary.files_changed) ? summary.files_changed : []
    const states = Array.isArray(summary.states) ? summary.states : tail
    const lastStates = states.slice(-10)

    return [
      `OPALE_STATUS: ${summary.status || "UNKNOWN"}`,
      `PROJECT: ${summary.project || project}`,
      `RUN_DIR: ${runDir}`,
      `FINAL_STATE: ${summary.final_state || "UNKNOWN"}`,
      `PROJECT_TYPE: ${summary.project_type || "unknown"}`,
      `VERIFICATION_LEVEL: ${summary.verification_level || "unknown"}`,
      `REPAIR_ATTEMPTS: ${summary.repair_attempts ?? "unknown"}`,
      `FILES_CHANGED: ${filesChanged.length ? filesChanged.join(", ") : "<none>"}`,
      `FAILURE_REASON: ${summary.failure_reason || "<none>"}`,
      "",
      "Derniers etats:",
      formatStates(lastStates),
      "",
      `SUMMARY_JSON: ${summaryPath}`,
      `FOLLOW: Get-Content "${runJsonlPath}" -Wait`,
    ].join("\n")
  },
})
