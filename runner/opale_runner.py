#!/usr/bin/env python3
"""OPALE state-machine runner for complete project work.

The runner keeps deterministic control over the workflow while OpenCode agents
produce architecture, implementation and verification content.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class State(str, Enum):
    INTAKE = "INTAKE"
    DISCOVER = "DISCOVER"
    ARCHITECTURE = "ARCHITECTURE"
    IMPLEMENT = "IMPLEMENT"
    BUILD = "BUILD"
    FUNCTIONAL_VERIFY = "FUNCTIONAL_VERIFY"
    REPAIR = "REPAIR"
    FINAL_REVIEW = "FINAL_REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"


class ProjectType(str, Enum):
    WEB = "web"
    PYTHON = "python"
    UNITY = "unity"
    ANDROID = "android"
    GENERIC = "generic"


class VerificationLevel(str, Enum):
    FULL = "full"
    LIMITED = "limited"
    FAILED = "failed"


@dataclass
class CommandResult:
    name: str
    args: list[str]
    returncode: int | None
    duration_seconds: float
    stdout_path: str
    stderr_path: str
    timed_out: bool = False
    skipped: bool = False
    reason: str | None = None


@dataclass
class RunContext:
    project: Path
    prompt: str
    max_repairs: int
    timeout_plan: int
    timeout_implement: int
    timeout_verify: int
    timeout_build: int
    opencode_bin: str | None
    dry_run: bool
    started_at: str
    run_dir: Path
    prompts_dir: Path
    stdout_dir: Path
    stderr_dir: Path
    state: State = State.INTAKE
    project_type: ProjectType = ProjectType.GENERIC
    task_kind: str = "complete_project"
    architecture: str = ""
    worker_output: str = ""
    verifier_output: str = ""
    last_error: str = ""
    repair_attempts: int = 0
    verification_level: VerificationLevel = VerificationLevel.FAILED
    initial_status: list[str] = field(default_factory=list)
    commands: list[CommandResult] = field(default_factory=list)
    states: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_command(
    ctx: RunContext,
    name: str,
    args: list[str],
    timeout: int,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> CommandResult:
    started = time.monotonic()
    stdout_path = ctx.stdout_dir / f"{len(ctx.commands) + 1:02d}-{name}.txt"
    stderr_path = ctx.stderr_dir / f"{len(ctx.commands) + 1:02d}-{name}.txt"

    if ctx.dry_run:
        result = CommandResult(
            name=name,
            args=args,
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            skipped=True,
            reason="dry-run",
        )
        write_text(stdout_path, "")
        write_text(stderr_path, "DRY RUN: command not executed\n")
        ctx.commands.append(result)
        return result

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    start_new_session = os.name != "nt"

    proc = subprocess.Popen(
        args,
        cwd=str(cwd or ctx.project),
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "Process tree did not terminate cleanly after timeout."
    duration = time.monotonic() - started
    if timed_out:
        stderr = (stderr or "") + f"\nTIMEOUT after {timeout}s\n"
    write_text(stdout_path, stdout or "")
    write_text(stderr_path, stderr or "")
    result = CommandResult(
        name=name,
        args=args,
        returncode=None if timed_out else proc.returncode,
        duration_seconds=duration,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        timed_out=timed_out,
    )

    ctx.commands.append(result)
    return result


def kill_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        time.sleep(2)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def runner_dir() -> Path:
    return Path(__file__).resolve().parent


def load_env_opencode_bin() -> str | None:
    env_path = runner_dir() / "opale.env.json"
    if not env_path.exists():
        return None
    try:
        data = json.loads(env_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("opencode_bin")
    if isinstance(value, str) and value.strip():
        return value
    return None


def resolve_opencode_bin(ctx: RunContext) -> str:
    candidates: list[str] = []
    candidates.extend([
        r"D:\npm-global\node_modules\opencode-ai\bin\opencode.exe",
    ])
    if ctx.opencode_bin:
        candidates.insert(0, ctx.opencode_bin)
    env_bin = os.environ.get("OPALE_OPENCODE_BIN")
    if env_bin:
        candidates.insert(0, env_bin)
    deployed_bin = load_env_opencode_bin()
    if deployed_bin:
        candidates.insert(0, deployed_bin)
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(str(Path(appdata) / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"))
    for name in ("opencode.exe", "opencode.cmd", "opencode"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.extend([
        r"D:\npm-global\opencode.cmd",
        r"D:\npm-global\opencode",
    ])

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)
    raise FileNotFoundError(
        "Executable opencode introuvable. Redeployez OPALE depuis un terminal ou "
        "passez --opencode-bin/OPALE_OPENCODE_BIN avec le chemin de opencode.cmd."
    )


def git(ctx: RunContext, args: list[str], timeout: int = 120) -> CommandResult:
    return run_command(ctx, "git-" + args[0], ["git", *args], timeout)


def read_command_output(result: CommandResult) -> str:
    stdout = Path(result.stdout_path).read_text(encoding="utf-8", errors="replace")
    stderr = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace")
    return stdout + stderr


def read_command_stdout(result: CommandResult) -> str:
    return Path(result.stdout_path).read_text(encoding="utf-8", errors="replace")


def ensure_project(ctx: RunContext) -> None:
    ctx.project.mkdir(parents=True, exist_ok=True)
    if not (ctx.project / ".git").exists():
        init = git(ctx, ["init"])
        if init.returncode not in (0, None):
            raise RuntimeError("git init failed")
    status = git(ctx, ["status", "--porcelain"])
    ctx.initial_status = read_command_stdout(status).splitlines()


def current_status(ctx: RunContext) -> list[str]:
    status = git(ctx, ["status", "--porcelain"])
    return read_command_stdout(status).splitlines()


def files_changed(ctx: RunContext) -> list[str]:
    paths: set[str] = set()
    for line in current_status(ctx):
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith(".opale/"):
            continue
        paths.add(path.strip())
    return sorted(paths)


def normalized_status(lines: list[str]) -> set[str]:
    normalized: set[str] = set()
    for line in lines:
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().replace("\\", "/")
        if path.startswith(".opale/"):
            continue
        normalized.add(line[:3] + path)
    return normalized


def real_project_change_exists(ctx: RunContext) -> bool:
    after = normalized_status(current_status(ctx))
    before = normalized_status(ctx.initial_status)
    return after != before


def capture_diff(ctx: RunContext) -> None:
    diff = git(ctx, ["diff", "--no-ext-diff", "--binary"], timeout=180)
    diff_text = read_command_stdout(diff)
    cached = git(ctx, ["diff", "--cached", "--no-ext-diff", "--binary"], timeout=180)
    cached_text = read_command_stdout(cached)
    write_text(ctx.run_dir / "diff.patch", diff_text + cached_text)
    write_text(ctx.run_dir / "files.json", json.dumps(files_changed(ctx), ensure_ascii=False, indent=2))


def classify_task(prompt: str) -> str:
    lowered = prompt.lower()
    analysis_terms = ("explique", "analyse", "audit", "pourquoi", "plan", "architecture seulement")
    change_terms = (
        "implemente",
        "implémente",
        "cree",
        "crée",
        "ajoute",
        "corrige",
        "modifie",
        "jeu",
        "app",
        "application",
        "projet",
        "unity",
        "android",
        "python",
        "web",
    )
    if any(term in lowered for term in change_terms):
        return "complete_project"
    if any(term in lowered for term in analysis_terms):
        return "analysis"
    return "small_change"


def has_any(project: Path, patterns: list[str]) -> bool:
    for pattern in patterns:
        if any(project.glob(pattern)):
            return True
    return False


def detect_project_type(project: Path) -> ProjectType:
    if (project / "ProjectSettings" / "ProjectVersion.txt").exists() or (
        (project / "Assets").exists() and (project / "Packages" / "manifest.json").exists()
    ):
        return ProjectType.UNITY
    if (
        (project / "settings.gradle").exists()
        or (project / "settings.gradle.kts").exists()
        or (project / "gradlew").exists()
        or (project / "gradlew.bat").exists()
        or (project / "app" / "build.gradle").exists()
        or (project / "app" / "build.gradle.kts").exists()
    ):
        return ProjectType.ANDROID
    if (
        (project / "package.json").exists()
        or has_any(project, ["vite.config.*"])
        or (project / "index.html").exists()
    ):
        return ProjectType.WEB
    if (
        (project / "pyproject.toml").exists()
        or (project / "requirements.txt").exists()
        or (project / "setup.py").exists()
        or has_any(project, ["*.py", "src/**/*.py", "tests/**/*.py"])
    ):
        return ProjectType.PYTHON
    return ProjectType.GENERIC


def package_scripts(project: Path) -> dict[str, str]:
    package = project / "package.json"
    if not package.exists():
        return {}
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    scripts = data.get("scripts", {})
    return scripts if isinstance(scripts, dict) else {}


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def find_unity_editor() -> str | None:
    candidates: list[Path] = []
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for root in program_files:
        if root:
            candidates.extend(Path(root).glob("Unity/Hub/Editor/*/Editor/Unity.exe"))
            candidates.extend(Path(root).glob("Unity*/Editor/Unity.exe"))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("Unity")


def state_event(ctx: RunContext, state: State, status: str, details: dict[str, Any] | None = None) -> None:
    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "state": state.value,
        "status": status,
        "details": details or {},
    }
    ctx.states.append(payload)
    append_jsonl(ctx.run_dir / "run.jsonl", payload)


def opencode_run(ctx: RunContext, state: State, agent: str, prompt: str, timeout: int) -> tuple[bool, str]:
    prompt_path = ctx.prompts_dir / f"{state.value.lower()}-{agent}.md"
    write_text(prompt_path, prompt)
    opencode_bin = resolve_opencode_bin(ctx)
    result = run_command(
        ctx,
        f"opencode-{state.value.lower()}-{agent}",
        [
            opencode_bin,
            "run",
            "--agent",
            agent,
            "--dir",
            str(ctx.project),
            "Execute les instructions du fichier joint et retourne le contrat STATUS/NEXT/SUMMARY/EVIDENCE.",
            "--file",
            str(prompt_path),
        ],
        timeout,
    )
    output = read_command_output(result)
    ok = result.returncode == 0 and not result.timed_out and bool(output.strip())
    return ok, output


def prompt_architecture(ctx: RunContext) -> str:
    return f"""Tu es appele par la machine d'etat OPALE.

Demande utilisateur:
{ctx.prompt}

Projet: {ctx.project}
Profil detecte: {ctx.project_type.value}
Type de tache: {ctx.task_kind}

Produis une architecture courte et exploitable pour livrer le projet complet.
Ne modifie aucun fichier. Ne pose pas de question sauf blocage absolu.

Termine exactement avec:
STATUS: DONE | FAIL | BLOCKED
NEXT: IMPLEMENT | FINISH
SUMMARY: decisions d'architecture retenues
EVIDENCE: hypotheses et criteres utilises
"""


def prompt_implement(ctx: RunContext) -> str:
    return f"""Tu es appele par la machine d'etat OPALE dans l'etat IMPLEMENT.

Travaille directement dans le repertoire reel du projet: {ctx.project}

Demande utilisateur:
{ctx.prompt}

Architecture a appliquer:
{ctx.architecture or "Pas d'architecture separee requise pour cette tache."}

Contraintes:
- Ecris les fichiers reels du projet avec les outils OpenCode.
- Ne termine pas sur une phrase d'intention.
- Ne demande pas de confirmation.
- Si tu ne peux pas ecrire, donne l'erreur exacte.

Termine exactement avec:
STATUS: DONE | FAIL | BLOCKED
NEXT: VERIFY
SUMMARY: changements reellement effectues
EVIDENCE: chemins des fichiers, commandes et sorties significatives
"""


def prompt_verify(ctx: RunContext, build_report: str) -> str:
    return f"""Tu es appele par la machine d'etat OPALE dans l'etat FUNCTIONAL_VERIFY.

Demande utilisateur:
{ctx.prompt}

Architecture:
{ctx.architecture or "Non applicable."}

Profil projet detecte: {ctx.project_type.value}
Fichiers changes detectes par git:
{json.dumps(files_changed(ctx), ensure_ascii=False, indent=2)}

Commandes deja executees par le runner:
{build_report}

Inspecte les fichiers reels et verifie que le livrable repond a la demande.
Ne modifie rien.

Termine exactement avec:
STATUS: DONE | FAIL | BLOCKED
NEXT: FINISH | REPAIR
SUMMARY: verdict PASS, FAIL ou BLOCKED et cause
EVIDENCE: fichiers inspectes, commandes et sorties significatives
"""


def prompt_repair(ctx: RunContext) -> str:
    return f"""Tu es appele par la machine d'etat OPALE dans l'etat REPAIR.

Travaille directement dans le repertoire reel du projet: {ctx.project}

Demande utilisateur:
{ctx.prompt}

Architecture:
{ctx.architecture or "Non applicable."}

Diagnostic a corriger:
{ctx.last_error}

Corrige uniquement ce qui est necessaire. Si la correction est impossible,
explique l'erreur exacte. Ne demande pas de confirmation.

Termine exactement avec:
STATUS: DONE | FAIL | BLOCKED
NEXT: VERIFY
SUMMARY: corrections reellement effectuees
EVIDENCE: chemins des fichiers, commandes et sorties significatives
"""


def run_build_profile(ctx: RunContext) -> tuple[VerificationLevel, str]:
    reports: list[str] = []
    level = VerificationLevel.FULL

    if ctx.project_type == ProjectType.WEB:
        scripts = package_scripts(ctx.project)
        if (ctx.project / "package.json").exists() and not (ctx.project / "node_modules").exists():
            if command_exists("npm"):
                result = run_command(ctx, "npm-install", ["npm", "install"], ctx.timeout_build)
                reports.append(f"npm install: {result.returncode}")
                if result.returncode != 0:
                    level = VerificationLevel.FAILED
            else:
                reports.append("npm install: skipped, npm introuvable")
                level = VerificationLevel.LIMITED
        if "build" in scripts and command_exists("npm"):
            result = run_command(ctx, "npm-run-build", ["npm", "run", "build"], ctx.timeout_build)
            reports.append(f"npm run build: {result.returncode}")
            if result.returncode != 0:
                level = VerificationLevel.FAILED
        if "test" in scripts and command_exists("npm"):
            result = run_command(ctx, "npm-test", ["npm", "test"], ctx.timeout_build)
            reports.append(f"npm test: {result.returncode}")
            if result.returncode != 0:
                level = VerificationLevel.FAILED
        if not reports:
            reports.append("web: aucun script package.json exploitable; verification limitee au verifier")
            level = VerificationLevel.LIMITED

    elif ctx.project_type == ProjectType.PYTHON:
        py_files = [p for p in ctx.project.rglob("*.py") if ".opale" not in p.parts and ".git" not in p.parts]
        if py_files:
            result = run_command(ctx, "python-compileall", [sys.executable, "-m", "compileall", "."], ctx.timeout_build)
            reports.append(f"python -m compileall .: {result.returncode}")
            if result.returncode != 0:
                level = VerificationLevel.FAILED
        pytest_markers = [
            ctx.project / "pytest.ini",
            ctx.project / "pyproject.toml",
            ctx.project / "tests",
        ]
        if any(path.exists() for path in pytest_markers):
            result = run_command(ctx, "pytest", [sys.executable, "-m", "pytest"], ctx.timeout_build)
            reports.append(f"python -m pytest: {result.returncode}")
            if result.returncode != 0:
                level = VerificationLevel.FAILED
        if (ctx.project / "main.py").exists():
            result = run_command(ctx, "python-main", [sys.executable, "main.py"], min(ctx.timeout_build, 120))
            reports.append(f"python main.py: {result.returncode}")
            if result.returncode != 0:
                level = VerificationLevel.FAILED
        if not reports:
            reports.append("python: aucun fichier Python exploitable; verification limitee au verifier")
            level = VerificationLevel.LIMITED

    elif ctx.project_type == ProjectType.ANDROID:
        gradlew = ctx.project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if gradlew.exists():
            test = run_command(ctx, "gradlew-test", [str(gradlew), "test"], ctx.timeout_build)
            reports.append(f"gradlew test: {test.returncode}")
            assemble = run_command(ctx, "gradlew-assembleDebug", [str(gradlew), "assembleDebug"], ctx.timeout_build)
            reports.append(f"gradlew assembleDebug: {assemble.returncode}")
            if test.returncode != 0 or assemble.returncode != 0:
                level = VerificationLevel.FAILED
        else:
            reports.append("android: gradle wrapper absent; verification environnement limitee")
            level = VerificationLevel.LIMITED

    elif ctx.project_type == ProjectType.UNITY:
        unity = find_unity_editor()
        if unity:
            result = run_command(
                ctx,
                "unity-batchmode",
                [
                    unity,
                    "-batchmode",
                    "-quit",
                    "-projectPath",
                    str(ctx.project),
                    "-logFile",
                    str(ctx.run_dir / "unity.log"),
                ],
                ctx.timeout_build,
            )
            reports.append(f"Unity batchmode: {result.returncode}")
            if result.returncode != 0:
                level = VerificationLevel.FAILED
        else:
            reports.append("unity: Unity Editor introuvable; verification environnement limitee")
            level = VerificationLevel.LIMITED

    else:
        reports.append("generic: verification par git diff et verifier OpenCode")
        level = VerificationLevel.LIMITED

    return level, "\n".join(reports)


def has_success_status(output: str) -> bool:
    normalized = output.upper()
    return "STATUS: DONE" in normalized or "PASS" in normalized


def write_summary(ctx: RunContext, status: str) -> None:
    capture_diff(ctx)
    summary = {
        "status": status,
        "project_type": ctx.project_type.value,
        "final_state": ctx.state.value,
        "project": str(ctx.project),
        "prompt": ctx.prompt,
        "states": ctx.states,
        "files_changed": files_changed(ctx),
        "commands": [command.__dict__ for command in ctx.commands],
        "repair_attempts": ctx.repair_attempts,
        "verification_level": ctx.verification_level.value,
        "failure_reason": ctx.failure_reason,
        "started_at": ctx.started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_text(ctx.run_dir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2))


def run_state_machine(ctx: RunContext) -> int:
    try:
        ctx.state = State.INTAKE
        ctx.task_kind = classify_task(ctx.prompt)
        state_event(ctx, ctx.state, "DONE", {"task_kind": ctx.task_kind})

        ctx.state = State.DISCOVER
        ensure_project(ctx)
        ctx.project_type = detect_project_type(ctx.project)
        state_event(ctx, ctx.state, "DONE", {"project_type": ctx.project_type.value})

        needs_architecture = ctx.task_kind == "complete_project"
        if needs_architecture:
            ctx.state = State.ARCHITECTURE
            ok, output = opencode_run(
                ctx,
                ctx.state,
                "runner-product-architect",
                prompt_architecture(ctx),
                ctx.timeout_plan,
            )
            ctx.architecture = output
            if not ok or "STATUS: FAIL" in output.upper() or "STATUS: BLOCKED" in output.upper():
                ctx.failure_reason = "architecture failed, blocked, empty, or timed out"
                state_event(ctx, ctx.state, "FAILED", {"reason": ctx.failure_reason})
                ctx.state = State.FAILED
                write_summary(ctx, "FAILED")
                return 1
            if "NEXT: FINISH" in output.upper():
                ctx.state = State.DONE
                ctx.verification_level = VerificationLevel.LIMITED
                state_event(ctx, ctx.state, "DONE", {"reason": "architecture-only request"})
                write_summary(ctx, "DONE")
                return 0
            state_event(ctx, State.ARCHITECTURE, "DONE", {"bytes": len(output)})

        ctx.state = State.IMPLEMENT
        ok, output = opencode_run(
            ctx,
            ctx.state,
            "runner-code-worker",
            prompt_implement(ctx),
            ctx.timeout_implement,
        )
        ctx.worker_output = output
        if not ok:
            ctx.last_error = "worker failed, empty, or timed out\n" + output[-4000:]
            state_event(ctx, ctx.state, "FAILED", {"reason": ctx.last_error[:500]})
            ctx.state = State.REPAIR
        elif real_project_change_exists(ctx):
            state_event(ctx, ctx.state, "DONE", {"files_changed": files_changed(ctx)})
            ctx.state = State.BUILD
        else:
            ctx.last_error = "Aucun changement reel detecte dans le projet apres IMPLEMENT."
            state_event(ctx, ctx.state, "NO_CHANGE", {"reason": ctx.last_error})
            ctx.state = State.REPAIR

        build_report = ""
        while ctx.state == State.REPAIR and ctx.repair_attempts < ctx.max_repairs:
            ctx.repair_attempts += 1
            ok, output = opencode_run(
                ctx,
                State.REPAIR,
                "runner-code-worker",
                prompt_repair(ctx),
                ctx.timeout_implement,
            )
            ctx.worker_output = output
            if ok and real_project_change_exists(ctx):
                state_event(ctx, State.REPAIR, "DONE", {"attempt": ctx.repair_attempts})
                ctx.state = State.BUILD
                break
            ctx.last_error = "Repair did not produce a real project change.\n" + output[-4000:]
            state_event(ctx, State.REPAIR, "FAILED", {"attempt": ctx.repair_attempts})

        if ctx.state == State.REPAIR:
            ctx.failure_reason = ctx.last_error or "repair attempts exhausted"
            ctx.state = State.FAILED
            write_summary(ctx, "FAILED")
            return 1

        while ctx.state in (State.BUILD, State.FUNCTIONAL_VERIFY, State.REPAIR):
            if ctx.state == State.BUILD:
                ctx.verification_level, build_report = run_build_profile(ctx)
                status = "DONE" if ctx.verification_level != VerificationLevel.FAILED else "FAILED"
                state_event(ctx, ctx.state, status, {"verification_level": ctx.verification_level.value})
                if ctx.verification_level == VerificationLevel.FAILED:
                    ctx.last_error = "Build/profile verification failed.\n" + build_report
                    ctx.state = State.REPAIR
                else:
                    ctx.state = State.FUNCTIONAL_VERIFY

            elif ctx.state == State.FUNCTIONAL_VERIFY:
                ok, output = opencode_run(
                    ctx,
                    ctx.state,
                    "runner-verifier",
                    prompt_verify(ctx, build_report),
                    ctx.timeout_verify,
                )
                ctx.verifier_output = output
                if ok and has_success_status(output) and "STATUS: FAIL" not in output.upper():
                    state_event(ctx, ctx.state, "DONE", {"bytes": len(output)})
                    ctx.state = State.FINAL_REVIEW
                else:
                    ctx.last_error = "Verifier failed, blocked, empty, or timed out.\n" + output[-4000:]
                    state_event(ctx, ctx.state, "FAILED", {"reason": ctx.last_error[:500]})
                    ctx.state = State.REPAIR

            elif ctx.state == State.REPAIR:
                if ctx.repair_attempts >= ctx.max_repairs:
                    ctx.failure_reason = ctx.last_error or "repair attempts exhausted"
                    ctx.state = State.FAILED
                    write_summary(ctx, "FAILED")
                    return 1
                ctx.repair_attempts += 1
                ok, output = opencode_run(
                    ctx,
                    State.REPAIR,
                    "runner-code-worker",
                    prompt_repair(ctx),
                    ctx.timeout_implement,
                )
                if ok and real_project_change_exists(ctx):
                    state_event(ctx, State.REPAIR, "DONE", {"attempt": ctx.repair_attempts})
                    ctx.state = State.BUILD
                else:
                    ctx.last_error = "Repair failed to produce a real project change.\n" + output[-4000:]
                    state_event(ctx, State.REPAIR, "FAILED", {"attempt": ctx.repair_attempts})

        ctx.state = State.FINAL_REVIEW
        capture_diff(ctx)
        if not real_project_change_exists(ctx):
            ctx.failure_reason = "final review found no real project change"
            state_event(ctx, ctx.state, "FAILED", {"reason": ctx.failure_reason})
            ctx.state = State.FAILED
            write_summary(ctx, "FAILED")
            return 1

        state_event(ctx, ctx.state, "DONE", {"files_changed": files_changed(ctx)})
        ctx.state = State.DONE
        state_event(ctx, ctx.state, "DONE", {"verification_level": ctx.verification_level.value})
        write_summary(ctx, "DONE")
        return 0

    except Exception as exc:  # noqa: BLE001 - top-level runner must log all failures.
        ctx.failure_reason = f"{type(exc).__name__}: {exc}"
        state_event(ctx, State.FAILED, "FAILED", {"reason": ctx.failure_reason})
        ctx.state = State.FAILED
        write_summary(ctx, "FAILED")
        return 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OPALE as a deterministic state machine.")
    parser.add_argument("--project", required=True, help="Project directory to modify.")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="User request to execute.")
    prompt_group.add_argument("--prompt-file", help="UTF-8 file containing the user request to execute.")
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument("--timeout-plan", type=int, default=600)
    parser.add_argument("--timeout-implement", type=int, default=1800)
    parser.add_argument("--timeout-verify", type=int, default=900)
    parser.add_argument("--timeout-build", type=int, default=900)
    parser.add_argument("--opencode-bin", default=None, help="Explicit path to opencode.cmd/opencode.")
    parser.add_argument("--log-dir", default=None, help="Override run log directory.")
    parser.add_argument("--run-dir", default=None, help="Exact run directory to use.")
    parser.add_argument("--dry-run", action="store_true", help="Create logs without executing external commands.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    project = Path(args.project).expanduser().resolve()
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8-sig")
    started = datetime.now().isoformat(timespec="seconds")
    if args.run_dir:
        run_dir = Path(args.run_dir).expanduser().resolve()
    else:
        run_root = Path(args.log_dir).expanduser().resolve() if args.log_dir else project / ".opale" / "runs"
        run_dir = run_root / now_stamp()
    ctx = RunContext(
        project=project,
        prompt=prompt,
        max_repairs=args.max_repairs,
        timeout_plan=args.timeout_plan,
        timeout_implement=args.timeout_implement,
        timeout_verify=args.timeout_verify,
        timeout_build=args.timeout_build,
        opencode_bin=args.opencode_bin,
        dry_run=args.dry_run,
        started_at=started,
        run_dir=run_dir,
        prompts_dir=run_dir / "prompts",
        stdout_dir=run_dir / "stdout",
        stderr_dir=run_dir / "stderr",
    )
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    ctx.prompts_dir.mkdir(parents=True, exist_ok=True)
    ctx.stdout_dir.mkdir(parents=True, exist_ok=True)
    ctx.stderr_dir.mkdir(parents=True, exist_ok=True)

    exit_code = run_state_machine(ctx)
    print(f"OPALE {ctx.state.value}: logs={ctx.run_dir}")
    if ctx.failure_reason:
        print(f"Reason: {ctx.failure_reason}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
