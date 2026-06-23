# Post-mortem du benchmark `think`

## Objectif

Le benchmark devait comparer quatre combinaisons du mode `think` de Gemma sur un
prompt demandant de concevoir puis d'implementer un Pong Web. Chaque combinaison
devait etre executee trois fois dans un depot vierge, avec un plafond de trente
minutes par generation.

## Resultats observes

- Les quatre microtests de delegation ont termine sans erreur en 377 a 452
  secondes.
- Sept generations completes ont ete lancees avant l'arret du benchmark.
- Les sept generations ont atteint le plafond de 1 800 secondes.
- Deux generations n'ont cree aucun fichier.
- Cinq generations ont cree une structure partielle de un a trois fichiers.
- Aucun cas n'a livre un Pong complet et valide.

Les traces montrent que l'architecture etait generalement produite, mais que la
suite accumulait plusieurs sources de latence : temps de reponse de l'orchestrateur
Gemma, compactages de contexte, resultats worker vides ou partiels, verification
puis tentative de correction. Le mode `think` n'a donc pas pu etre compare sur la
qualite finale, faute de cas complet admissible.

## Decision initiale

Le benchmark incomplet, son runner et ses projets generes ont ete supprimes. Une
variante `v0.3 Interpreteur` a ete testee ensuite, avec Qwen comme interpreteur et
Gemma `think: true` comme architecte sans outil. Elle n'a pas ete retenue :
l'interpreteur pouvait encore annoncer une verification ou une reparation sans
emettre l'appel `task` correspondant, et le plugin de continuation ajoute pour
compenser introduisait un risque de boucle/compaction. La source deployable reste
donc sur `v0.2 Prompt global` tant qu'une orchestration deterministe n'est pas
validee separement.

## Suite appliquee

La correction retenue ensuite est `OPALE v0.3 Machine d'etat globale` :

- un runner Python pilote les etats `INTAKE`, `DISCOVER`, `ARCHITECTURE`,
  `IMPLEMENT`, `BUILD`, `FUNCTIONAL_VERIFY`, `REPAIR`, `FINAL_REVIEW`, `DONE` et
  `FAILED` ;
- un custom tool global OpenCode `opale_run` permet a `local-team` de lancer le
  runner depuis l'interface ;
- le deploiement genere `opale-runner\opale.env.json` avec le chemin absolu du
  binaire OpenCode pour eviter les erreurs `FileNotFoundError` dues au `PATH`
  incomplet des applications GUI Windows ;
- l'orchestration principale ne repose plus sur un hook ou un plugin de
  continuation.

La source deployable n'est donc plus `v0.2 Prompt global`, mais `v0.3 Machine
d'etat globale`.
