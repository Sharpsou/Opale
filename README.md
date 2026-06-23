# OPALE

OPALE signifie **Orchestration Pilotee d'Agents Locaux Encadres**. Cette version
`v0.3 Machine d'etat globale` configure OpenCode avec une equipe locale de cinq
roles et ajoute un runner Python global pour piloter les projets complets.

Pour les projets complets ou multi-fichiers, le runner OPALE devient
l'orchestrateur fiable : il genere un plan de fichiers via Ollama natif quand
necessaire, applique lui-meme les fichiers dans le projet reel, controle les
preuves disque, execute les commandes de profil et decide des transitions.

## Structure du depot

Le repertoire [`opencode/`](opencode/) est le payload runtime OPALE. Il reproduit
la structure attendue dans la configuration globale OpenCode :

```text
opencode/
  AGENTS.md       # unique fichier d'instructions globales OPALE
  opencode.json   # configuration OpenCode
  agents/         # definitions des agents OPALE
  tools/          # custom tools OpenCode globaux, dont opale_run

runner/
  opale_runner.py # machine d'etat OPALE
  opale.ps1       # wrapper Windows
  README.md
```

Les fichiers situes a la racine du depot servent uniquement a installer et
documenter ce payload. Les eventuelles instructions locales de developpement ou
de Codex doivent rester dans `.codex/`. Ce repertoire est ignore par Git et n'est
jamais deploye dans OpenCode.

## Prerequis

- Windows avec PowerShell 5.1 ou plus recent ;
- OpenCode ;
- Ollama en cours d'execution ;

## Installation

Cloner le depot :

```powershell
git clone https://github.com/Sharpsou/Opale.git
cd Opale
```

OPALE utilise Gemma 4 12B pour tous les roles actifs : front-office, architecte,
worker et verificateur. Cette variante Gemma-only evite que le `small_model`
OpenCode ou le worker bascule sur un modele qui consomme ses tokens en
raisonnement sans produire d'appel d'outil exploitable.

```powershell
ollama pull gemma4:12b
ollama cp gemma4:12b local-gemma4-12b
ollama list
```

Deployer OPALE dans la configuration globale OpenCode :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1
```

Le script copie les instructions, les agents et le runner dans
`%USERPROFILE%\.config\opencode` et adapte les chemins au compte Windows courant.
OpenCode utilisera ensuite `local-team` comme agent interactif par defaut.
Pour les projets complets, `local-team` dispose du tool global `opale_run` et peut
donc lancer la machine d'etat directement depuis l'interface OpenCode.
Le tool `opale_run` transmet le prompt au runner via un fichier UTF-8 temporaire,
pas en argument console direct, pour eviter les problemes de quoting,
d'encodage et de prompts longs.
Pour les projets complets depuis l'interface, `local-team` utilise `opale_run` en
mode `async: true` afin de retourner immediatement le PID et le dossier de logs
attendu.

> **Important : modifier ce depot ne met pas OpenCode a jour automatiquement.**
> Apres chaque changement apporte aux instructions, aux agents ou a la
> configuration OPALE, il faut redeployer les fichiers vers la configuration
> globale `%USERPROFILE%\.config\opencode`. Il faut ensuite fermer completement
> OpenCode, le relancer et ouvrir une nouvelle session : un processus ou une
> session deja ouverte peut conserver les anciens prompts et un
> contexte devenu faux.

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

## Utilisation du runner pour un projet complet

Pour un projet complet, preferer le runner global plutot qu'une simple session
interactive OpenCode :

```powershell
Set-Content -Encoding UTF8 -LiteralPath "$env:TEMP\opale-prompt.txt" -Value "je souhaite faire un jeu pong en web contre l'ordi. je veux une DA simple et futuriste. defini d'abord l'architecture puis implemente. fait tout tout seul sans me demander."
powershell "$HOME\.config\opencode\opale-runner\opale.ps1" `
  -Project "D:\prog\PongW" `
  -PromptFile "$env:TEMP\opale-prompt.txt"
```

Depuis l'interface OpenCode, l'agent `Local-Team` doit appeler le tool `opale_run`
pour les demandes de projet complet. Si ce tool n'apparait pas encore, fermer
completement OpenCode, redeployer OPALE, puis relancer une nouvelle session.
`Local-Team` agit comme agent de cadrage : il enrichit la demande en objectif,
non-objectifs, criteres de succes, contraintes, hypotheses et points a verifier,
puis transmet ce brief au runner.

Pour forcer un test depuis l'interface :

```text
Utilise le tool opale_run pour creer un jeu Pong web complet contre une IA avec style futuriste neon.
```

Un vrai lancement affiche un appel d'outil `opale_run` dans le fil OpenCode, puis
cree un dossier `.opale\runs` dans le projet cible.
Pour obtenir l'avancement ou le resultat directement dans la discussion OpenCode,
demander ensuite `statut OPALE` ou fournir le `RUN_DIR` affiche par `opale_run`.
`local-team` appelle alors `opale_status`, qui lit `summary.json` et `run.jsonl`
sans relancer le runner.

Le runner suit les etats :

```text
INTAKE -> DISCOVER -> ARCHITECTURE -> IMPLEMENT -> BUILD
      -> FUNCTIONAL_VERIFY -> REPAIR -> FINAL_REVIEW -> DONE | FAILED
```

Il detecte les profils `web`, `python`, `unity`, `android` et `generic`. Il ne
croit pas uniquement le texte d'un modele : il controle `git status`, `git diff`,
les fichiers reels, les commandes executees et ses verifications fonctionnelles.
Pour eviter les blocages observes avec `opencode run`, le runner n'appelle plus
les agents `runner-*` comme sous-processus OpenCode. Il appelle Ollama via son API
native pour les plans de fichiers, puis applique et verifie lui-meme les fichiers.

Les logs sont ecrits dans :

```text
<projet>\.opale\runs\<timestamp>\
```

OPALE n'effectue jamais de commit automatique. Git sert uniquement de capteur de
changements et de garde-fou.

Pour l'evolution cible vers des missions decoupees en plusieurs runs controles,
voir [`docs/runner-multistep-evolution.md`](docs/runner-multistep-evolution.md).

## Diagnostic rapide

Verifier que le tool global est deploye :

```powershell
Get-ChildItem "$HOME\.config\opencode\tools\opale_run.js"
Get-ChildItem "$HOME\.config\opencode\opale-runner\opale_runner.py"
```

Verifier qu'un run est passe par la machine d'etat :

```powershell
$run = Get-ChildItem "D:\prog\PongW\.opale\runs" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content "$($run.FullName)\summary.json"
```

Depuis OpenCode, le meme diagnostic peut etre demande dans le chat avec :

```text
statut OPALE
```

ou :

```text
statut OPALE pour D:\prog\PongW\.opale\runs\<timestamp>
```

Dans `summary.json`, verifier `status`, `states`, `files_changed`,
`verification_level` et `failure_reason`. Un run correct doit produire un
`summary.json` meme en cas d'echec.

## Mise a jour apres modification du depot

La copie de reference se trouve dans `opencode/` et `runner/`, tandis qu'OpenCode
execute la copie deployee dans `%USERPROFILE%\.config\opencode`. Pour prendre en
compte une modification locale :

```powershell
Copy-Item "$HOME\.config\opencode" "$HOME\.config\opencode.backup" -Recurse
.\deploy.ps1 -Force
```

`-Force` remplace les fichiers OPALE globaux. Le script preserve les modeles deja
declares dans `provider.ollama.models`, mais une sauvegarde reste recommandee pour
les autres permissions ou reglages personnels.
Apres le deploiement, redemarrer OpenCode et creer une nouvelle session dans le
projet cible.

## Desinstallation

Restaurer la sauvegarde de la configuration OpenCode ou supprimer les fichiers
ajoutes dans `%USERPROFILE%\.config\opencode`.
