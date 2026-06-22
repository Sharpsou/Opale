import { tool } from "@opencode-ai/plugin";
import { existsSync } from "node:fs";
import path from "node:path";
import {
  applyWorkspace,
  cleanupStaleWorkspaces,
  cleanupWorkspace,
  createChangeTrace,
  createWorkspace,
  editWorkspaceFile,
  executeWorkspace,
  hostChanges,
  readWorkspaceFile,
  restoreHost,
  writeChangeTrace,
  workspaceChanges,
} from "../scripts/opale-isolation-core.mjs";

const workspaces = new Map();
const allowedAgents = new Set(["local-code-worker", "local-verifier"]);
const globalAllowedExecutables = new Set(["powershell", "git", "rg", "node", "npm", "python"]);

function keyFor(context) {
  return `${context.sessionID}:${context.agent}`;
}

async function workspace(context) {
  if (!allowedAgents.has(context.agent)) throw new Error(`Outil isole interdit au role ${context.agent}.`);
  const key = keyFor(context);
  if (!workspaces.has(key)) workspaces.set(key, await createWorkspace(context.worktree, context.sessionID, context.agent));
  return workspaces.get(key);
}

async function invalidate(context, current) {
  workspaces.delete(keyFor(context));
  await cleanupWorkspace(current);
}

async function cleanupSession(sessionID) {
  const matches = [...workspaces.entries()].filter(([, value]) => value.sessionID === sessionID);
  for (const [key, current] of matches) {
    workspaces.delete(key);
    await cleanupWorkspace(current);
  }
}

function formatChanges(changes) {
  return changes.map((item) => `${item.status}:${item.path}`);
}

export const OpaleIsolationPlugin = async (input) => {
  const localPlugin = path.join(input.directory, ".opencode", "plugin", "opale-isolation.js");
  if (existsSync(localPlugin)) return {};
  await cleanupStaleWorkspaces();
  return {
    dispose: async () => {
      const active = [...workspaces.values()];
      workspaces.clear();
      await Promise.allSettled(active.map(cleanupWorkspace));
    },
    event: async ({ event }) => {
      if (event.type === "session.deleted") await cleanupSession(event.properties.info.id);
    },
    tool: {
      opale_read: tool({
        description: "Lire un fichier de la copie de travail isolee OPALE.",
        args: { path: tool.schema.string().describe("Chemin relatif au projet") },
        async execute(args, context) { return readWorkspaceFile(await workspace(context), args.path); },
      }),
      opale_edit: tool({
        description: "Remplacer un texte unique dans la copie isolee. old_text vide cree un fichier absent.",
        args: {
          path: tool.schema.string(),
          old_text: tool.schema.string(),
          new_text: tool.schema.string(),
        },
        async execute(args, context) {
          if (context.agent !== "local-code-worker") throw new Error("Seul le worker peut modifier la copie isolee.");
          return editWorkspaceFile(await workspace(context), args.path, args.old_text, args.new_text);
        },
      }),
      opale_exec: tool({
        description: "Executer sans shell une commande autorisee dans la copie isolee.",
        args: { argv: tool.schema.array(tool.schema.string()).min(1) },
        async execute(args, context) {
          const current = await workspace(context);
          const before = await hostChanges(current);
          if (before.length) {
            await invalidate(context, current);
            return `BLOCKED host_changed_before_exec\n${formatChanges(before).join("\n")}`;
          }
          const result = await executeWorkspace(current, args.argv, {
            abortSignal: context.abort,
            allowedExecutables: globalAllowedExecutables,
          });
          const after = await hostChanges(current);
          if (after.length) {
            let restored = false;
            try {
              await context.ask({
                permission: "opale_restore_host",
                patterns: formatChanges(after),
                always: [],
                metadata: { changes: after },
              });
              await restoreHost(current);
              restored = true;
            } catch {
              restored = false;
            } finally {
              await invalidate(context, current);
            }
            return `BLOCKED host_modified_during_exec restored=${restored}\n${formatChanges(after).join("\n")}`;
          }
          const verdict = result.termination ? "BLOCKED" : result.code === 0 ? "PASS" : "FAIL";
          return `${verdict} exit=${result.code}${result.signal ? ` signal=${result.signal}` : ""}${result.termination ? ` reason=${result.termination}` : ""}\n${result.output}`;
        },
      }),
      opale_diff: tool({
        description: "Afficher le patch exact des changements de la copie isolee.",
        args: {},
        async execute(_args, context) {
          const current = await workspace(context);
          const changes = await workspaceChanges(current);
          return changes.length ? createChangeTrace(current) : "Aucun changement.";
        },
      }),
      opale_submit: tool({
        description: "Demander confirmation puis appliquer les changements isoles au projet hote.",
        args: {},
        async execute(_args, context) {
          if (context.agent !== "local-code-worker") throw new Error("Seul le worker peut soumettre des changements.");
          const current = await workspace(context);
          const changes = await workspaceChanges(current);
          if (!changes.length) return "Aucun changement a appliquer.";
          const trace = await createChangeTrace(current);
          await context.ask({
            permission: "opale_apply_host",
            patterns: [...formatChanges(changes), `PATCH PREVIEW\n${trace.slice(0, 6000)}`],
            always: [],
            metadata: { changes, trace },
          });
          await writeChangeTrace(current, trace);
          const applied = await applyWorkspace(current);
          await invalidate(context, current);
          return `Changements appliques :\n${applied.map((item) => `${item.status}\t${item.path}`).join("\n")}`;
        },
      }),
    },
  };
};

export default OpaleIsolationPlugin;
