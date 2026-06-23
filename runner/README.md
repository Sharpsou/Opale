# OPALE Runner

Le runner OPALE est la machine d'etat globale qui pilote OpenCode pour les
projets complets. Il garde le controle des transitions, tandis que les agents
OpenCode produisent l'architecture, le code et la verification.

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
Local-Team -> opale_run -> opale.ps1 -> opale_runner.py -> agents OpenCode
```

`opale_run` ecrit le prompt dans un fichier UTF-8 temporaire et appelle
`opale.ps1 -PromptFile`. Le prompt n'est donc pas passe directement comme argument
console, ce qui evite les problemes de quoting, d'encodage et de longueur.
Pour les projets complets, `local-team` utilise `opale_run` en mode asynchrone et
retourne immediatement le PID, le `PROMPT_FILE` et le `RUN_DIR_EXPECTED`.

Le tool est deploye dans :

```text
%USERPROFILE%\.config\opencode\tools\opale_run.js
```

Le runner lit aussi :

```text
%USERPROFILE%\.config\opencode\opale-runner\opale.env.json
```

Ce fichier contient le chemin absolu de `opencode.exe` detecte au deploiement.
Il evite les erreurs `FileNotFoundError` quand OpenCode est lance depuis une
interface graphique dont le `PATH` ne contient pas le repertoire npm global.

## Etats

```text
INTAKE -> DISCOVER -> ARCHITECTURE -> IMPLEMENT -> BUILD
      -> FUNCTIONAL_VERIFY -> REPAIR -> FINAL_REVIEW -> DONE | FAILED
```

Le runner ne valide jamais uniquement le recit d'un agent. Il combine la sortie
OpenCode, l'etat du disque, `git status`, `git diff`, les commandes executees et
les regles du profil projet detecte.
Il appelle les agents primaires dedies `runner-product-architect`,
`runner-code-worker` et `runner-verifier`.

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

Si `failure_reason` contient `FileNotFoundError`, redeployer OPALE avec
`deploy.ps1 -Force` depuis un terminal ou lancer `opale.ps1` avec `-OpencodeBin`.
