# OPALE Runner

Le runner OPALE est la machine d'etat globale appelee depuis OpenCode pour les
projets complets. Il garde le controle des transitions, genere des plans de
fichiers via l'API native Ollama quand necessaire, applique les fichiers lui-meme
et verifie l'etat reel du disque.

## Usage

```powershell
Set-Content -Encoding UTF8 -LiteralPath "$env:TEMP\opale-prompt.txt" -Value "je souhaite faire un jeu pong en web contre l'ordi. je veux une DA simple et futuriste. defini d'abord l'architecture puis implemente. fait tout tout seul sans me demander."
powershell "$HOME\.config\opencode\opale-runner\opale.ps1" `
  -Project "D:\prog\PongW" `
  -PromptFile "$env:TEMP\opale-prompt.txt"
```

Depuis l'interface OpenCode, `Local-Team` peut lancer ce runner via le custom tool
global `opale_run`. Pour un projet complet, le comportement attendu est :

```text
Local-Team -> opale_run -> opale.ps1 -> opale_runner.py -> Ollama natif + disque
```

`opale_run` ecrit le prompt dans un fichier UTF-8 temporaire et appelle
`opale.ps1 -PromptFile`. Le prompt n'est donc pas passe directement comme argument
console, ce qui evite les problemes de quoting, d'encodage et de longueur.
Pour les projets complets, `local-team` utilise `opale_run` en mode asynchrone et
retourne immediatement le PID, le `PROMPT_FILE` et le `RUN_DIR`.
Pour afficher ensuite l'avancement ou le resultat dans la discussion, demander
`statut OPALE` : `local-team` appelle le tool `opale_status`, qui lit le dernier
run du projet ou le `RUN_DIR` fourni.

Le tool est deploye dans :

```text
%USERPROFILE%\.config\opencode\tools\opale_run.js
%USERPROFILE%\.config\opencode\tools\opale_status.js
```

## Etats

```text
INTAKE -> DISCOVER -> ARCHITECTURE -> IMPLEMENT -> BUILD
      -> FUNCTIONAL_VERIFY -> REPAIR -> FINAL_REVIEW -> DONE | FAILED
```

Le runner ne valide jamais uniquement le recit d'un agent. Il combine la sortie
Ollama, l'etat du disque, `git status`, `git diff`, les commandes executees et les
regles du profil projet detecte. Il n'appelle plus `opencode run` pour ses etapes
internes, afin d'eviter les blocages de tool-calling constates avec certains
modeles locaux.

## Profils

- `web` : `package.json`, `vite.config.*`, `index.html`.
- `python` : `pyproject.toml`, `requirements.txt`, `setup.py`, fichiers `.py`.
- `unity` : `ProjectSettings/ProjectVersion.txt`, `Assets/`, `Packages/manifest.json`.
- `android` : Gradle wrapper, `settings.gradle`, `app/build.gradle`.
- `generic` : fallback sans stack claire.

Les logs sont ecrits dans :

```text
<project>\.opale\runs\<timestamp>\
```

OPALE n'effectue jamais de commit automatique. Git sert uniquement de capteur de
changements et de garde-fou.

## Diagnostic

Un run reussi ou echoue proprement doit creer :

```text
<project>\.opale\runs\<timestamp>\summary.json
```

Dans ce fichier, verifier :

- `states` : trace des etats executes.
- `commands` : commandes lancees par le runner.
- `failure_reason` : cause exacte en cas d'echec.
- `files_changed` : fichiers modifies hors `.opale`.

Si le dossier de run ne progresse pas, inspecter `run.jsonl`, `summary.json` et
les fichiers `stdout/` et `stderr/`, puis redeployer OPALE avec `deploy.ps1
-Force` avant de relancer une nouvelle session OpenCode.
