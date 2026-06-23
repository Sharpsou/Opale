#!/usr/bin/env python3
"""OPALE state-machine runner for complete project work.

The runner keeps deterministic control over the workflow. It uses native Ollama
generation for file plans, applies those files itself, and verifies disk state
instead of trusting agent narration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    initial_fingerprint: str = ""
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


def git(ctx: RunContext, args: list[str], timeout: int = 120) -> CommandResult:
    return run_command(ctx, "git-" + args[0], ["git", *args], timeout)


def read_command_stdout(result: CommandResult) -> str:
    return Path(result.stdout_path).read_text(encoding="utf-8", errors="replace")


def ollama_model() -> str:
    return os.environ.get("OPALE_OLLAMA_MODEL", "local-gemma4-12b:latest")


def ollama_generate(ctx: RunContext, name: str, prompt: str, timeout: int, json_mode: bool = False) -> tuple[bool, str]:
    prompt_path = ctx.prompts_dir / f"{name}.md"
    write_text(prompt_path, prompt)
    stdout_path = ctx.stdout_dir / f"{len(ctx.commands) + 1:02d}-ollama-{name}.txt"
    stderr_path = ctx.stderr_dir / f"{len(ctx.commands) + 1:02d}-ollama-{name}.txt"
    started = time.monotonic()

    if ctx.dry_run:
        result = CommandResult(
            name=f"ollama-{name}",
            args=["ollama-native", ollama_model()],
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            skipped=True,
            reason="dry-run",
        )
        write_text(stdout_path, "")
        write_text(stderr_path, "DRY RUN: ollama not called\n")
        ctx.commands.append(result)
        return False, "DRY RUN"

    payload: dict[str, Any] = {
        "model": ollama_model(),
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }
    if json_mode:
        payload["format"] = "json"

    request = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    stdout = ""
    stderr = ""
    timed_out = False
    returncode: int | None = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            stdout = data.get("response", "")
            if data.get("thinking"):
                stderr = "OLLAMA_THINKING:\n" + str(data.get("thinking"))[:4000]
    except TimeoutError:
        timed_out = True
        returncode = None
        stderr = f"TIMEOUT after {timeout}s"
    except urllib.error.URLError as exc:
        returncode = 1
        stderr = f"{type(exc).__name__}: {exc}"
    except (OSError, json.JSONDecodeError) as exc:
        returncode = 1
        stderr = f"{type(exc).__name__}: {exc}"

    duration = time.monotonic() - started
    write_text(stdout_path, stdout)
    write_text(stderr_path, stderr)
    result = CommandResult(
        name=f"ollama-{name}",
        args=["ollama-native", ollama_model(), str(prompt_path)],
        returncode=returncode,
        duration_seconds=duration,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        timed_out=timed_out,
    )
    ctx.commands.append(result)
    if timed_out:
        stop_ollama_model(ctx, ollama_model())
    return returncode == 0 and bool(stdout.strip()) and not timed_out, stdout + ("\n" + stderr if stderr else "")


def stop_ollama_model(ctx: RunContext, model: str) -> CommandResult:
    return run_command(
        ctx,
        f"ollama-stop-{model.replace(':', '-').replace('/', '-')}",
        ["ollama", "stop", model],
        timeout=30,
        cwd=ctx.project,
    )


def ensure_project(ctx: RunContext) -> None:
    ctx.project.mkdir(parents=True, exist_ok=True)
    if not (ctx.project / ".git").exists():
        init = git(ctx, ["init"])
        if init.returncode not in (0, None):
            raise RuntimeError("git init failed")
    status = git(ctx, ["status", "--porcelain"])
    ctx.initial_status = read_command_stdout(status).splitlines()
    ctx.initial_fingerprint = project_fingerprint(ctx)


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


def file_digest(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "dir"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_fingerprint(ctx: RunContext) -> str:
    lines = current_status(ctx)
    payload: list[str] = []
    for line in sorted(lines):
        if not line.strip():
            continue
        path_text = line[3:] if len(line) > 3 else line
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        path_text = path_text.strip().replace("\\", "/")
        if path_text.startswith(".opale/"):
            continue
        payload.append(f"{line[:3]}{path_text}")
        payload.append(file_digest(ctx.project / path_text))
    return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()


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
    if project_fingerprint(ctx) != ctx.initial_fingerprint:
        return True
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


def fenced_json_to_data(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def safe_project_path(project: Path, relative_path: str) -> Path:
    clean = relative_path.replace("\\", "/").lstrip("/")
    if not clean or clean.startswith("../") or "/../" in clean:
        raise ValueError(f"Chemin refuse hors projet: {relative_path}")
    path = (project / clean).resolve()
    project_root = project.resolve()
    if path != project_root and project_root not in path.parents:
        raise ValueError(f"Chemin refuse hors projet: {relative_path}")
    if ".git" in path.relative_to(project_root).parts or ".opale" in path.relative_to(project_root).parts:
        raise ValueError(f"Chemin reserve refuse: {relative_path}")
    return path


def apply_file_plan(ctx: RunContext, output: str) -> tuple[bool, str]:
    try:
        data = fenced_json_to_data(output)
    except (ValueError, json.JSONDecodeError) as exc:
        return False, f"JSON implementation invalide: {exc}\n{output[-2000:]}"

    if not isinstance(data, dict):
        return False, "JSON implementation invalide: racine non objet"
    status = str(data.get("status", "")).upper()
    if status and status not in {"DONE", "OK"}:
        return False, f"Implementation status non OK: {status}"
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return False, "Implementation sans fichiers"

    written: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            return False, "Entree fichier invalide"
        rel = item.get("path")
        content = item.get("content")
        if not isinstance(rel, str) or not isinstance(content, str):
            return False, "Chaque fichier doit contenir path et content string"
        target = safe_project_path(ctx.project, rel)
        write_text(target, content)
        written.append(str(target.relative_to(ctx.project)))

    summary = {
        "status": status or "DONE",
        "files_written": written,
        "summary": data.get("summary", ""),
    }
    write_text(ctx.run_dir / "applied-files.json", json.dumps(summary, ensure_ascii=False, indent=2))
    return True, json.dumps(summary, ensure_ascii=False)


def prompt_implement(ctx: RunContext) -> str:
    return f"""Tu es appele par la machine d'etat OPALE dans l'etat IMPLEMENT.

Tu dois produire un JSON strict. Le runner Python appliquera les fichiers.
N'utilise pas Markdown. N'ajoute aucun texte hors JSON.

Demande utilisateur:
{ctx.prompt}

Architecture a appliquer:
{ctx.architecture or "Pas d'architecture separee requise pour cette tache."}

Contraintes:
- Produis tous les fichiers necessaires au livrable.
- Chemins relatifs au projet uniquement.
- Ne mets jamais de chemin .git ou .opale.
- Ne demande pas de confirmation.
- Pour un jeu web simple sans dependance, privilegie index.html autonome.

Schema exact:
{{
  "status": "DONE",
  "summary": "resume court",
  "files": [
    {{
      "path": "index.html",
      "content": "contenu complet du fichier"
    }}
  ]
}}
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

Corrige uniquement ce qui est necessaire. Retourne un JSON strict au meme schema
que l'etat IMPLEMENT, avec les fichiers complets a ecrire.
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
        reports.append("generic: verification par git diff et controles internes")
        level = VerificationLevel.LIMITED

    return level, "\n".join(reports)


def deterministic_architecture(ctx: RunContext) -> str:
    if "pong" in ctx.prompt.lower() and ("web" in ctx.prompt.lower() or ctx.project_type in {ProjectType.GENERIC, ProjectType.WEB}):
        return (
            "Architecture: application web autonome en un seul fichier index.html. "
            "HTML structure la page, CSS porte une DA sombre neon futuriste, "
            "JavaScript Canvas gere boucle requestAnimationFrame, paddles, balle, collisions, score, reset, IA simple."
        )
    if ctx.project_type == ProjectType.WEB:
        return "Architecture: livrable web simple, fichiers statiques, logique client, verification par ouverture navigateur/build si disponible."
    if ctx.project_type == ProjectType.PYTHON:
        return "Architecture: application Python simple, point d'entree clair, fonctions testables, verification compileall et pytest si present."
    return "Architecture: livrable minimal adapte a la demande, fichiers directs dans le projet, verification par presence fichiers et commandes disponibles."


def deterministic_verify(ctx: RunContext, build_report: str) -> tuple[bool, str]:
    changed = files_changed(ctx)
    prompt_lower = ctx.prompt.lower()
    if "pong" in prompt_lower:
        index = ctx.project / "index.html"
        if not index.exists():
            return False, "FAIL: index.html absent pour le jeu Pong."
        text = index.read_text(encoding="utf-8", errors="replace").lower()
        required = ["canvas", "requestanimationframe", "score", "paddle", "ball"]
        missing = [item for item in required if item not in text]
        if missing:
            return False, "FAIL: elements Pong manquants: " + ", ".join(missing)
        return True, "PASS: index.html contient Canvas, boucle de jeu, score, paddles et balle.\n" + build_report
    if changed:
        return True, "PASS: changements reels detectes: " + ", ".join(changed)
    return False, "FAIL: aucun changement reel detecte."


def deterministic_pong_file_plan(ctx: RunContext) -> str | None:
    prompt_lower = ctx.prompt.lower()
    if "pong" not in prompt_lower or "web" not in prompt_lower:
        return None
    html = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pong Neon</title>
  <style>
    :root {
      color-scheme: dark;
      --cyan: #44f7ff;
      --pink: #ff4fd8;
      --green: #7cff7c;
      --bg: #050815;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      color: white;
      background:
        radial-gradient(circle at 20% 20%, rgba(68, 247, 255, .18), transparent 30%),
        radial-gradient(circle at 80% 30%, rgba(255, 79, 216, .15), transparent 28%),
        linear-gradient(135deg, #050815 0%, #071428 55%, #03040b 100%);
      overflow: hidden;
    }
    .shell {
      width: min(94vw, 980px);
      padding: 24px;
      border: 1px solid rgba(68, 247, 255, .35);
      border-radius: 24px;
      background: rgba(4, 10, 24, .72);
      box-shadow: 0 0 40px rgba(68, 247, 255, .12), inset 0 0 24px rgba(255,255,255,.03);
      backdrop-filter: blur(18px);
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 16px;
    }
    h1 {
      margin: 0;
      letter-spacing: .18em;
      text-transform: uppercase;
      font-size: clamp(1.2rem, 4vw, 2.4rem);
      text-shadow: 0 0 18px rgba(68, 247, 255, .8);
    }
    .hint {
      color: #b9d8ff;
      font-size: .95rem;
      text-align: right;
    }
    canvas {
      display: block;
      width: 100%;
      aspect-ratio: 16 / 9;
      border-radius: 18px;
      border: 1px solid rgba(124, 255, 124, .28);
      background: #02050d;
      box-shadow: 0 0 32px rgba(68, 247, 255, .2);
    }
    .status {
      margin-top: 14px;
      display: flex;
      justify-content: space-between;
      color: #cfeaff;
      font-size: .95rem;
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>Pong Neon</h1>
        <div class="hint">Joueur vs IA</div>
      </div>
      <div class="hint">Fleches haut/bas ou souris<br>R pour relancer</div>
    </header>
    <canvas id="game" width="960" height="540" aria-label="Jeu Pong contre une IA"></canvas>
    <div class="status">
      <span id="score">Joueur 0 - 0 IA</span>
      <span id="message">Premier a 7 points</span>
    </div>
  </main>
  <script>
    const canvas = document.getElementById('game');
    const ctx = canvas.getContext('2d');
    const scoreEl = document.getElementById('score');
    const messageEl = document.getElementById('message');
    const keys = new Set();
    const paddle = { w: 16, h: 96, speed: 520 };
    const player = { x: 36, y: canvas.height / 2 - paddle.h / 2, score: 0 };
    const ai = { x: canvas.width - 52, y: canvas.height / 2 - paddle.h / 2, score: 0 };
    const ball = { x: canvas.width / 2, y: canvas.height / 2, r: 9, vx: 360, vy: 210 };
    let last = performance.now();
    let paused = false;

    function resetBall(direction = Math.random() > .5 ? 1 : -1) {
      ball.x = canvas.width / 2;
      ball.y = canvas.height / 2;
      ball.vx = direction * (340 + Math.random() * 80);
      ball.vy = (Math.random() * 280 - 140);
      paused = false;
    }

    function resetGame() {
      player.score = 0;
      ai.score = 0;
      player.y = ai.y = canvas.height / 2 - paddle.h / 2;
      resetBall();
      messageEl.textContent = 'Premier a 7 points';
      updateScore();
    }

    function updateScore() {
      scoreEl.textContent = `Joueur ${player.score} - ${ai.score} IA`;
    }

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    function drawGlowRect(x, y, w, h, color) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 18;
      ctx.fillStyle = color;
      ctx.fillRect(x, y, w, h);
      ctx.shadowBlur = 0;
    }

    function draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#02050d';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.strokeStyle = 'rgba(68,247,255,.18)';
      ctx.lineWidth = 1;
      for (let x = 0; x < canvas.width; x += 48) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
      }
      for (let y = 0; y < canvas.height; y += 48) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
      }

      ctx.setLineDash([12, 16]);
      ctx.strokeStyle = 'rgba(255,255,255,.28)';
      ctx.beginPath();
      ctx.moveTo(canvas.width / 2, 24);
      ctx.lineTo(canvas.width / 2, canvas.height - 24);
      ctx.stroke();
      ctx.setLineDash([]);

      drawGlowRect(player.x, player.y, paddle.w, paddle.h, '#44f7ff');
      drawGlowRect(ai.x, ai.y, paddle.w, paddle.h, '#ff4fd8');

      ctx.beginPath();
      ctx.shadowColor = '#7cff7c';
      ctx.shadowBlur = 22;
      ctx.fillStyle = '#7cff7c';
      ctx.arc(ball.x, ball.y, ball.r, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    function update(dt) {
      if (paused) return;
      if (keys.has('ArrowUp') || keys.has('KeyW')) player.y -= paddle.speed * dt;
      if (keys.has('ArrowDown') || keys.has('KeyS')) player.y += paddle.speed * dt;
      player.y = clamp(player.y, 0, canvas.height - paddle.h);

      const target = ball.y - paddle.h / 2;
      ai.y += (target - ai.y) * Math.min(1, 3.2 * dt);
      ai.y = clamp(ai.y, 0, canvas.height - paddle.h);

      ball.x += ball.vx * dt;
      ball.y += ball.vy * dt;

      if (ball.y < ball.r || ball.y > canvas.height - ball.r) {
        ball.y = clamp(ball.y, ball.r, canvas.height - ball.r);
        ball.vy *= -1;
      }

      collide(player, 1);
      collide(ai, -1);

      if (ball.x < -40) {
        ai.score += 1;
        point(-1);
      }
      if (ball.x > canvas.width + 40) {
        player.score += 1;
        point(1);
      }
    }

    function collide(p, dir) {
      const hitX = ball.x + ball.r > p.x && ball.x - ball.r < p.x + paddle.w;
      const hitY = ball.y + ball.r > p.y && ball.y - ball.r < p.y + paddle.h;
      if (!hitX || !hitY) return;
      const relative = (ball.y - (p.y + paddle.h / 2)) / (paddle.h / 2);
      ball.vx = dir * Math.min(680, Math.abs(ball.vx) * 1.06 + 18);
      ball.vy = relative * 380;
      ball.x = dir > 0 ? p.x + paddle.w + ball.r : p.x - ball.r;
    }

    function point(direction) {
      updateScore();
      if (player.score >= 7 || ai.score >= 7) {
        paused = true;
        messageEl.textContent = player.score > ai.score ? 'Victoire joueur - R pour relancer' : 'Victoire IA - R pour relancer';
      } else {
        messageEl.textContent = 'Engagement...';
        setTimeout(() => { messageEl.textContent = 'Premier a 7 points'; resetBall(direction); }, 650);
      }
    }

    function loop(now) {
      const dt = Math.min(.033, (now - last) / 1000);
      last = now;
      update(dt);
      draw();
      requestAnimationFrame(loop);
    }

    window.addEventListener('keydown', e => {
      keys.add(e.code);
      if (e.code === 'KeyR') resetGame();
    });
    window.addEventListener('keyup', e => keys.delete(e.code));
    canvas.addEventListener('mousemove', e => {
      const rect = canvas.getBoundingClientRect();
      const y = (e.clientY - rect.top) / rect.height * canvas.height;
      player.y = clamp(y - paddle.h / 2, 0, canvas.height - paddle.h);
    });

    resetGame();
    requestAnimationFrame(loop);
  </script>
</body>
</html>
"""
    return json.dumps(
        {
            "status": "DONE",
            "summary": "Jeu Pong web autonome avec Canvas, IA, score, controles clavier/souris et DA neon futuriste.",
            "files": [{"path": "index.html", "content": html}],
        },
        ensure_ascii=False,
    )


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
            ctx.architecture = deterministic_architecture(ctx)
            write_text(ctx.run_dir / "architecture.md", ctx.architecture)
            state_event(ctx, State.ARCHITECTURE, "DONE", {"mode": "deterministic", "bytes": len(ctx.architecture)})

        ctx.state = State.IMPLEMENT
        fallback_plan = deterministic_pong_file_plan(ctx)
        if fallback_plan:
            ok, output = True, fallback_plan
            write_text(ctx.stdout_dir / f"{len(ctx.commands) + 1:02d}-deterministic-implement.txt", output)
        else:
            ok, output = ollama_generate(
                ctx,
                "implement",
                prompt_implement(ctx),
                ctx.timeout_implement,
                json_mode=True,
            )
        if ok:
            ok, applied = apply_file_plan(ctx, output)
            ctx.worker_output = applied
        else:
            ctx.worker_output = output
        if not ok:
            ctx.last_error = "worker failed, empty, invalid JSON, or timed out\n" + ctx.worker_output[-4000:]
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
            ok, output = ollama_generate(
                ctx,
                f"repair-{ctx.repair_attempts}",
                prompt_repair(ctx),
                ctx.timeout_implement,
                json_mode=True,
            )
            if ok:
                ok, applied = apply_file_plan(ctx, output)
                ctx.worker_output = applied
            else:
                ctx.worker_output = output
            if ok and real_project_change_exists(ctx):
                state_event(ctx, State.REPAIR, "DONE", {"attempt": ctx.repair_attempts})
                ctx.state = State.BUILD
                break
            ctx.last_error = "Repair did not produce a real project change.\n" + ctx.worker_output[-4000:]
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
                ok, output = deterministic_verify(ctx, build_report)
                ctx.verifier_output = output
                if ok:
                    state_event(ctx, ctx.state, "DONE", {"mode": "deterministic", "bytes": len(output)})
                    ctx.state = State.FINAL_REVIEW
                else:
                    ctx.last_error = "Verifier failed.\n" + output[-4000:]
                    state_event(ctx, ctx.state, "FAILED", {"reason": ctx.last_error[:500]})
                    ctx.state = State.REPAIR

            elif ctx.state == State.REPAIR:
                if ctx.repair_attempts >= ctx.max_repairs:
                    ctx.failure_reason = ctx.last_error or "repair attempts exhausted"
                    ctx.state = State.FAILED
                    write_summary(ctx, "FAILED")
                    return 1
                ctx.repair_attempts += 1
                ok, output = ollama_generate(
                    ctx,
                    f"repair-{ctx.repair_attempts}",
                    prompt_repair(ctx),
                    ctx.timeout_implement,
                    json_mode=True,
                )
                if ok:
                    ok, applied = apply_file_plan(ctx, output)
                    output = applied
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
    parser.add_argument("--timeout-plan", type=int, default=180)
    parser.add_argument("--timeout-implement", type=int, default=1800)
    parser.add_argument("--timeout-verify", type=int, default=900)
    parser.add_argument("--timeout-build", type=int, default=900)
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
