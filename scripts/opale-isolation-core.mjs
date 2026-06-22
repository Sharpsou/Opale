import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import {
  cp, lstat, mkdir, mkdtemp, readFile, readdir, rename, rm, stat, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const WORKSPACE_PREFIX = "opale-workspace-";
const TRANSACTION_PREFIX = ".opale-tx-";
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_OUTPUT_BYTES = 64 * 1024;
const DEFAULT_TIMEOUT_MS = 10 * 60 * 1000;
const DEFAULT_STALE_AGE_MS = 24 * 60 * 60 * 1000;
const BLOCKED_EXECUTABLES = new Set([
  "curl", "curl.exe", "wget", "wget.exe", "ssh", "ssh.exe", "scp", "scp.exe",
  "sftp", "sftp.exe", "ftp", "ftp.exe", "nc", "ncat", "telnet",
]);
const INLINE_EVAL = new Map([
  ["powershell", new Set(["-command", "-c", "-encodedcommand", "-enc"])],
  ["powershell.exe", new Set(["-command", "-c", "-encodedcommand", "-enc"])],
  ["pwsh", new Set(["-command", "-c", "-encodedcommand", "-enc"])],
  ["pwsh.exe", new Set(["-command", "-c", "-encodedcommand", "-enc"])],
  ["cmd", new Set(["/c", "/k"])],
  ["cmd.exe", new Set(["/c", "/k"])],
  ["bash", new Set(["-c"])],
  ["sh", new Set(["-c"])],
  ["python", new Set(["-c"])],
  ["python.exe", new Set(["-c"])],
  ["node", new Set(["-e", "--eval"])],
  ["node.exe", new Set(["-e", "--eval"])],
]);

function normalizeRelative(root, requested) {
  if (!requested || path.isAbsolute(requested)) throw new Error("Le chemin doit etre relatif au projet.");
  const normalized = path.normalize(requested);
  if (normalized === ".." || normalized.startsWith(`..${path.sep}`)) throw new Error("Traversee de repertoire interdite.");
  const absolute = path.resolve(root, normalized);
  const resolvedRoot = path.resolve(root);
  const prefix = `${resolvedRoot}${path.sep}`.toLowerCase();
  if (absolute.toLowerCase() !== resolvedRoot.toLowerCase() && !absolute.toLowerCase().startsWith(prefix)) {
    throw new Error("Chemin hors projet interdit.");
  }
  return absolute;
}

function ignoredEntry(name) {
  const transaction = /\.opale-tx-[a-f0-9]{32}\.(?:new|bak)$/.test(name);
  return name === ".git" || transaction;
}

async function assertNoLinks(root) {
  async function walk(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      if (ignoredEntry(entry.name)) continue;
      const full = path.join(current, entry.name);
      const info = await lstat(full);
      if (info.isSymbolicLink()) throw new Error(`Lien symbolique interdit : ${path.relative(root, full)}`);
      if (info.isDirectory()) await walk(full);
    }
  }
  await walk(root);
}

async function copyProject(source, target) {
  await assertNoLinks(source);
  await cp(source, target, {
    recursive: true,
    force: false,
    filter: (item) => !ignoredEntry(path.basename(item)),
  });
}

async function fileHash(file) {
  const data = await readFile(file);
  return createHash("sha256").update(data).digest("hex");
}

async function pathExists(file) {
  try { await lstat(file); return true; } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

export async function buildManifest(root) {
  const manifest = new Map();
  async function walk(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      if (ignoredEntry(entry.name)) continue;
      const full = path.join(current, entry.name);
      const relative = path.relative(root, full).split(path.sep).join("/");
      const info = await lstat(full);
      if (info.isSymbolicLink()) throw new Error(`Lien symbolique interdit : ${relative}`);
      if (info.isDirectory()) await walk(full);
      else if (info.isFile()) manifest.set(relative, { hash: await fileHash(full), size: info.size });
    }
  }
  await walk(root);
  return manifest;
}

export function manifestChanges(before, after) {
  const paths = new Set([...before.keys(), ...after.keys()]);
  return [...paths].sort().flatMap((file) => {
    const left = before.get(file);
    const right = after.get(file);
    if (!left) return [{ path: file, status: "added" }];
    if (!right) return [{ path: file, status: "deleted" }];
    if (left.hash !== right.hash) return [{ path: file, status: "modified" }];
    return [];
  });
}

export function parseAllowedExecutables(projectContent) {
  const match = projectContent.match(/^\|\s*Executables autorises\s*\|\s*`?(\[[^\r\n]*\])`?\s*\|\s*$/mi);
  if (!match) throw new Error("PROJECT.md ne definit pas les executables autorises.");
  const values = JSON.parse(match[1]);
  if (!Array.isArray(values) || values.length === 0 || values.some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error("La liste des executables autorises est invalide.");
  }
  return new Set(values.map((value) => path.basename(value).toLowerCase()));
}

export function validateArgv(argv, allowed) {
  if (!Array.isArray(argv) || argv.length === 0 || argv.some((value) => typeof value !== "string" || value.includes("\0"))) {
    throw new Error("argv doit etre un tableau non vide de chaines.");
  }
  const executable = path.basename(argv[0]).toLowerCase();
  if (!allowed.has(executable)) throw new Error(`Executable non autorise : ${executable}`);
  if (BLOCKED_EXECUTABLES.has(executable)) throw new Error(`Executable reseau interdit : ${executable}`);
  const forbidden = INLINE_EVAL.get(executable);
  if (forbidden && argv.slice(1).some((value) => forbidden.has(value.toLowerCase()))) {
    throw new Error(`Evaluation de commande libre interdite pour ${executable}.`);
  }
  return argv;
}

export async function cleanupStaleWorkspaces(options = {}) {
  const root = options.root ?? tmpdir();
  const now = options.now ?? Date.now();
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_STALE_AGE_MS;
  const removed = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.startsWith(WORKSPACE_PREFIX)) continue;
    const full = path.join(root, entry.name);
    const info = await stat(full);
    if (now - info.mtimeMs <= maxAgeMs) continue;
    await rm(full, { recursive: true, force: true });
    removed.push(full);
  }
  return removed;
}

export async function createWorkspace(projectRoot, sessionID, agent) {
  const tempRoot = await mkdtemp(path.join(tmpdir(), WORKSPACE_PREFIX));
  const original = path.join(tempRoot, "original");
  const working = path.join(tempRoot, "working");
  await mkdir(original);
  await copyProject(projectRoot, original);
  await copyProject(original, working);
  return {
    sessionID,
    agent,
    projectRoot: path.resolve(projectRoot),
    tempRoot,
    original,
    working,
    originalManifest: await buildManifest(original),
    valid: true,
  };
}

export async function cleanupWorkspace(workspace) {
  workspace.valid = false;
  await rm(workspace.tempRoot, { recursive: true, force: true });
}

export async function readWorkspaceFile(workspace, requested) {
  const file = normalizeRelative(workspace.working, requested);
  const info = await stat(file);
  if (!info.isFile()) throw new Error("Le chemin ne designe pas un fichier.");
  if (info.size > MAX_FILE_BYTES) throw new Error("Fichier trop volumineux pour opale_read.");
  return readFile(file, "utf8");
}

export async function editWorkspaceFile(workspace, requested, oldText, newText) {
  const file = normalizeRelative(workspace.working, requested);
  await mkdir(path.dirname(file), { recursive: true });
  let content = "";
  try { content = await readFile(file, "utf8"); } catch (error) {
    if (error.code !== "ENOENT" || oldText !== "") throw error;
  }
  if (oldText !== "") {
    const first = content.indexOf(oldText);
    if (first < 0) throw new Error("Le texte recherche est absent.");
    if (content.indexOf(oldText, first + oldText.length) >= 0) throw new Error("Le texte recherche n'est pas unique.");
    content = `${content.slice(0, first)}${newText}${content.slice(first + oldText.length)}`;
  } else {
    if (content.length > 0) throw new Error("old_text vide est reserve a la creation d'un fichier absent.");
    content = newText;
  }
  await writeFile(file, content, "utf8");
  return `Fichier isole modifie : ${requested}`;
}

async function terminateProcessTree(child) {
  if (!child.pid) return;
  if (process.platform === "win32") {
    await new Promise((resolve) => {
      const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, shell: false });
      killer.on("error", resolve);
      killer.on("close", resolve);
    });
    return;
  }
  try { process.kill(-child.pid, "SIGKILL"); } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

export async function executeWorkspace(workspace, argv, options = {}) {
  if (!workspace.valid) throw new Error("La copie de travail a ete invalidee.");
  let allowed = options.allowedExecutables;
  if (!allowed) {
    const project = await readFile(path.join(workspace.projectRoot, "PROJECT.md"), "utf8");
    allowed = parseAllowedExecutables(project);
  }
  validateArgv(argv, allowed);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const abortSignal = options.abortSignal;
  const env = {
    ...process.env,
    NO_PROXY: "", no_proxy: "",
    HTTP_PROXY: "http://127.0.0.1:9",
    HTTPS_PROXY: "http://127.0.0.1:9",
    ALL_PROXY: "http://127.0.0.1:9",
    PIP_NO_INDEX: "1",
    NPM_CONFIG_OFFLINE: "true", npm_config_offline: "true",
  };
  return new Promise((resolve, reject) => {
    const child = spawn(argv[0], argv.slice(1), {
      cwd: workspace.working,
      env,
      shell: false,
      windowsHide: true,
      detached: process.platform !== "win32",
    });
    let output = "";
    let truncated = false;
    let termination = null;
    let settled = false;
    const append = (chunk) => {
      if (output.length >= MAX_OUTPUT_BYTES) { truncated = true; return; }
      output += chunk.toString().slice(0, MAX_OUTPUT_BYTES - output.length);
    };
    const stop = async (reason) => {
      if (termination || settled) return;
      termination = reason;
      await terminateProcessTree(child);
    };
    child.stdout.on("data", append);
    child.stderr.on("data", append);
    child.on("error", (error) => { if (!settled) { settled = true; reject(error); } });
    const timer = setTimeout(() => { void stop("timeout"); }, timeoutMs);
    const onAbort = () => { void stop("aborted"); };
    if (abortSignal) {
      if (abortSignal.aborted) void stop("aborted");
      else abortSignal.addEventListener("abort", onAbort, { once: true });
    }
    child.on("close", (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      abortSignal?.removeEventListener("abort", onAbort);
      resolve({
        code,
        signal,
        termination,
        output: `${output}${truncated ? "\n[sortie tronquee]" : ""}`,
      });
    });
  });
}

export async function workspaceChanges(workspace) {
  return manifestChanges(workspace.originalManifest, await buildManifest(workspace.working));
}

export async function generateWorkspacePatch(workspace) {
  return new Promise((resolve, reject) => {
    const child = spawn("git", [
      "diff", "--no-index", "--binary", "--src-prefix=a/", "--dst-prefix=b/", "--", "original", "working",
    ], { cwd: workspace.tempRoot, shell: false, windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0 && code !== 1) return reject(new Error(`Generation du patch impossible : ${stderr.trim()}`));
      const normalized = stdout
        .replaceAll("a/original/", "a/")
        .replaceAll("b/working/", "b/");
      resolve(normalized);
    });
  });
}

export async function createChangeTrace(workspace) {
  const changes = await workspaceChanges(workspace);
  const patch = await generateWorkspacePatch(workspace);
  const inventory = changes.length
    ? changes.map((item) => `- ${item.status}: ${item.path}`).join("\n")
    : "- Aucun changement";
  return `# OPALE last change\n\nGenerated at: ${new Date().toISOString()}\n\n## Files\n\n${inventory}\n\n## Patch\n\n${patch}`;
}

export async function writeChangeTrace(workspace, trace) {
  const target = normalizeRelative(workspace.working, ".opale/last-change.patch");
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, trace, "utf8");
}

export async function hostChanges(workspace) {
  return manifestChanges(workspace.originalManifest, await buildManifest(workspace.projectRoot));
}

async function commitTreeState(targetRoot, sourceRoot, desiredManifest, options = {}) {
  const currentManifest = await buildManifest(targetRoot);
  const changes = manifestChanges(currentManifest, desiredManifest);
  const transaction = randomUUID().replaceAll("-", "");
  const prepared = [];
  const committed = [];
  let completed = false;
  try {
    for (const change of changes) {
      const target = normalizeRelative(targetRoot, change.path);
      const staged = `${target}${TRANSACTION_PREFIX}${transaction}.new`;
      const backup = `${target}${TRANSACTION_PREFIX}${transaction}.bak`;
      const hadOriginal = await pathExists(target);
      await mkdir(path.dirname(target), { recursive: true });
      if (change.status !== "deleted") {
        await cp(normalizeRelative(sourceRoot, change.path), staged, { force: false });
      }
      prepared.push({ change, target, staged, backup, hadOriginal });
    }
    for (const item of prepared) {
      if (item.hadOriginal) await rename(item.target, item.backup);
      committed.push(item);
      if (item.change.status !== "deleted") await rename(item.staged, item.target);
      if (options.failAfter === committed.length) throw new Error("Panne transactionnelle injectee.");
    }
    const validation = manifestChanges(desiredManifest, await buildManifest(targetRoot));
    if (validation.length) throw new Error(`Etat final invalide : ${validation.map((item) => item.path).join(', ')}`);
    completed = true;
    for (const item of prepared) await rm(item.backup, { force: true }).catch(() => {});
    return changes;
  } catch (error) {
    const rollbackErrors = [];
    for (const item of [...committed].reverse()) {
      try {
        await rm(item.target, { force: true });
        if (item.hadOriginal && await pathExists(item.backup)) await rename(item.backup, item.target);
      } catch (rollbackError) {
        rollbackErrors.push(rollbackError);
      }
    }
    if (rollbackErrors.length) throw new AggregateError([error, ...rollbackErrors], "Echec de transaction et de rollback.");
    throw error;
  } finally {
    for (const item of prepared) {
      await rm(item.staged, { force: true });
      if (completed) await rm(item.backup, { force: true });
    }
  }
}

export async function restoreHost(workspace, options = {}) {
  return commitTreeState(workspace.projectRoot, workspace.original, workspace.originalManifest, options);
}

export async function applyWorkspace(workspace, options = {}) {
  const conflicts = await hostChanges(workspace);
  if (conflicts.length) throw new Error(`Conflit avec le projet hote : ${conflicts.map((item) => item.path).join(', ')}`);
  const workingManifest = await buildManifest(workspace.working);
  return commitTreeState(workspace.projectRoot, workspace.working, workingManifest, options);
}
