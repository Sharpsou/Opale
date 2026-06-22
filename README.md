# OPALE

OPALE signifie **Orchestration Pilotee d'Agents Locaux Encadres**. Cette version
`v0.2 Prompt global` configure OpenCode avec une equipe locale de cinq roles :
orchestrateur, architecte, chercheur technique, worker et verificateur.

Les modifications passent par une copie temporaire, une verification independante
et une approbation avant application. OPALE limite les erreurs accidentelles, mais
ne constitue pas une sandbox systeme.

## Prerequis

- Windows avec PowerShell 5.1 ou plus recent ;
- OpenCode ;
- Ollama en cours d'execution ;
- Node.js et npm.

## Installation

Cloner le depot :

```powershell
git clone https://github.com/Sharpsou/Opale.git
cd Opale
```

Installer les deux modeles locaux utilises par defaut et creer leurs alias OPALE :

```powershell
ollama pull gemma4:12b
ollama pull qwen3.5:9b
ollama cp gemma4:12b local-gemma4-12b
ollama cp qwen3.5:9b local-qwen35-9b
```

Deployer OPALE dans la configuration globale OpenCode :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1
```

Le script copie les agents dans `%USERPROFILE%\.config\opencode`, adapte les
chemins au compte Windows courant et installe la dependance du plugin avec
`npm ci`. OpenCode utilisera ensuite `local-team` comme agent par defaut.

Si la cible contient deja une configuration, la sauvegarder puis autoriser son
remplacement :

```powershell
Copy-Item "$HOME\.config\opencode" "$HOME\.config\opencode.backup" -Recurse
.\deploy.ps1 -Force
```

Pour deployer vers un autre emplacement :

```powershell
.\deploy.ps1 -Target "D:\chemin\opencode"
```

## Desinstallation

Restaurer la sauvegarde de la configuration OpenCode ou supprimer les fichiers
ajoutes dans `%USERPROFILE%\.config\opencode`.
