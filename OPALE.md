# OPALE global

**OPALE** signifie **Orchestration Pilotee d'Agents Locaux Encadres**.

La version active est `OPALE v0.2 Prompt global`, un workflow multi-role local
route par prompt. Ce n'est ni une machine a etats, ni une sandbox systeme.

## Contrat operationnel

- les lectures triviales restent directes ;
- toute modification passe par le worker puis le verificateur ;
- worker et verificateur utilisent une copie temporaire ;
- l'application d'un changement demande une approbation humaine ;
- le diff exact est conserve dans `.opale/last-change.patch` ;
- une seule correction est autorisee apres un premier `FAIL` ;
- le dernier verdict `PASS`, `FAIL` ou `BLOCKED` lie la conclusion.

Les instructions et plugins propres a un projet sont prioritaires. Aucun skill
n'est installe ou active globalement par OPALE.
