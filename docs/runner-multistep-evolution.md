# Evolution OPALE - Runner multi-step

Ce document decrit une evolution cible du runner OPALE. Il ne decrit pas le
comportement actuellement disponible.

## Constat

Le runner actuel sait garder le controle d'un livrable dans une machine d'etat :

```text
INTAKE -> DISCOVER -> ARCHITECTURE -> IMPLEMENT -> BUILD
      -> FUNCTIONAL_VERIFY -> REPAIR -> FINAL_REVIEW -> DONE | FAILED
```

Il sait aussi tenter des corrections via `REPAIR`, avec un nombre maximal de
tentatives controle par `MaxRepairs`.

Cette boucle couvre les echecs simples : build casse, verification incomplete,
absence de changement reel, sortie agent vide ou timeout. Elle ne couvre pas
encore les demandes larges qui doivent etre decoupees en plusieurs livraisons
successives.

## Objectif cible

OPALE doit pouvoir traiter un prompt ambitieux sans sortir du runner, meme si le
travail doit etre livre en plusieurs etapes.

Exemples :

- site web avec plusieurs pages, authentification, dashboard et design complet ;
- application Python avec CLI, persistance, tests et documentation ;
- app Android avec ecrans multiples et validation Gradle ;
- projet Unity avec gameplay, menus, prefabs et build check.

Le comportement attendu est :

```text
MISSION_INTAKE
-> MISSION_PLAN
-> STEP_1_RUN
-> STEP_1_VERIFY
-> STEP_2_RUN
-> STEP_2_VERIFY
...
-> FINAL_ACCEPTANCE
-> DONE | FAILED
```

Chaque step doit rester sous controle du runner. Aucun agent interactif ne doit
prendre le relais manuellement sans demande explicite de l'utilisateur.

## Principe d'architecture

Ajouter un orchestrateur de mission au-dessus du runner actuel.

Le runner actuel reste responsable d'une unite de travail executable :

```text
prompt step -> architecture -> implementation -> build -> verification -> repair
```

Le nouvel orchestrateur serait responsable de :

- transformer une demande large en backlog ordonne ;
- definir les criteres de succes observables de chaque step ;
- lancer le runner actuel pour une step precise ;
- lire le `summary.json` de chaque run ;
- decider de continuer, reparer, reduire le perimetre ou echouer ;
- maintenir un journal persistant de mission.

## Etat persistant propose

Chaque mission ecrirait un fichier :

```text
<project>/.opale/missions/<timestamp>/mission.json
```

Structure indicative :

```json
{
  "status": "RUNNING",
  "prompt": "demande utilisateur complete",
  "current_step": 2,
  "steps": [
    {
      "id": "step-1",
      "title": "Scaffold web minimal",
      "prompt": "Creer la base Vite/React...",
      "success_criteria": [
        "package.json present",
        "npm run build passe",
        "page principale rendue"
      ],
      "run_dir": ".opale/runs/20260623-120000",
      "status": "DONE"
    }
  ],
  "failure_reason": null
}
```

Ce fichier devient la source de reprise apres interruption.

## Politique de decoupage

Une demande large devrait etre decoupee si elle contient :

- plusieurs surfaces produit independantes ;
- plusieurs stacks ou outils ;
- un nombre important de fichiers attendus ;
- une dependance claire entre fondations et features ;
- un risque de timeout ou de contexte trop long.

Exemple pour un site riche :

```text
Step 1 - Scaffold et design system minimal
Step 2 - Navigation, layout et pages principales
Step 3 - Features interactives prioritaires
Step 4 - Donnees mockees, etats vides/erreurs/loading
Step 5 - Build, verification UX, corrections finales
```

Chaque step doit livrer quelque chose de verifiable avant la suivante.

## Gestion des echecs

En cas d'echec d'une step :

1. utiliser d'abord la boucle `REPAIR` interne du runner ;
2. si la step reste en echec, produire un diagnostic depuis le `summary.json` ;
3. tenter au plus une reduction automatique de perimetre si l'echec vient d'une
   step trop large ;
4. relancer une nouvelle step plus petite ;
5. si l'echec persiste, marquer la mission `FAILED`.

Le point important : l'echec reste dans le systeme de mission. Il ne declenche
pas de fallback manuel implicite.

## Comportement utilisateur attendu

Depuis OpenCode, `local-team` devrait appeler un outil de mission, par exemple :

```text
opale_mission_run
```

Cet outil lancerait l'orchestrateur de mission et retournerait :

- le statut final si le mode est synchrone ;
- le dossier `MISSION_DIR` ;
- le dernier `RUN_DIR` ;
- la liste des steps terminees ;
- la cause exacte en cas d'echec.

Depuis l'interface OpenCode, le mode asynchrone doit rester le chemin recommande
pour les projets complets : `local-team` transfere le controle au runner, affiche
les chemins de suivi, puis s'arrete. Le mode synchrone reste utile pour les tests
courts en terminal.

## Non-objectifs

- Ne pas ajouter de nouveaux agents pour contourner le runner.
- Ne pas permettre a `local-team` de reprendre une mission en manuel apres echec.
- Ne pas valider une step sur le recit d'un agent.
- Ne pas cacher un `FAILED` derriere une reponse conversationnelle optimiste.

## Statut

Cette evolution n'est pas encore implementee.

Le runner actuel fournit seulement :

- un run controle pour une unite de travail ;
- une boucle de repair interne limitee ;
- des logs par run dans `.opale/runs/<timestamp>/`;
- un `summary.json` exploitable par un futur orchestrateur de mission.
