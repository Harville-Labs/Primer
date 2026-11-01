# 0008. Keep Lab's saved experiments in the shared PostgreSQL

- Status: Accepted
- Date: 2026-11-01

## Context

Lab kept nothing. Steering vectors and probes lived in memory for the person
who made them and went when the model changed. A run's prompt, its edits and
its result were on the screen and nowhere else. A researcher who closed the
tab lost the experiment, and had no way to show it to themselves a week
later.

Lab was also given none of Primer's shared secrets: no database URL, no
internal token, no settings key. The chart's Lab template said so, and a
policy test held it there. The reason was containment. Lab is the one process
that runs code shaped by a user's choice of model, so a compromise of it
should reach nothing beyond its own volume.

Saving an experiment means storing it somewhere that outlives the process,
for its owner and nobody else, and deleting it when an operator says it
should go.

## Decision

Lab stores saved experiments in the PostgreSQL the other services use, in a
schema of its own, through the same `PRIMER_DATABASE_URL`.

- **A `lab` schema with its own Alembic history.** The chart migrates it in
  a hook and Compose in a `migrate-lab` one-shot, both from Lab's image,
  which is the only one that holds its migrations. Lab never migrates at
  startup. No table refers outside the schema. An owner is an id, not a
  join.
- **Only what a user saves is kept.** Running, scoring, making a vector and
  training a probe write nothing. A save stores the request, the result,
  and the vectors and probes the run named, with their numbers, so the
  experiment can be restored after the model changes.
- **Every query is scoped to its owner.** Another user's experiment answers
  as one that does not exist, to every route.
- **Retention is the operator's.** `lab.experimentRetentionDays`, 90 by
  default. Lab deletes expired experiments within the hour, and every read
  leaves them out before then, so the period does not depend on when the
  cleanup last ran.
- **The database is optional to Lab.** Without a URL, Lab runs as before and
  says that saving is unavailable here.

This departs from "Lab is given none of the shared secrets" for the database
URL alone. Lab still has no internal token and no settings key.

## Alternatives considered

- **A database of Lab's own.** This keeps the shared database out of Lab's
  reach entirely. It is a second PostgreSQL for operators to run, back up and
  upgrade, for one table, in a component most deployments leave off.
- **A separate role, required.** Granting Lab a role limited to the `lab`
  schema would contain a compromise to saved experiments. The chart does not
  create roles. It is handed a URL, and a bundled PostgreSQL has one user.
  Requiring a second URL would make turning Lab on harder for the many
  deployments that would not use it. An operator who wants the containment
  can give Lab such a URL; the operations guide says how.
- **Save through Control.** Lab would keep no credentials, and Control would
  store experiments on its behalf. Lab would then need Control's internal
  token, which reaches more than the database does. Control would also hold
  a schema for a component it does not run.
- **Save in the browser.** Nothing would leave the user's machine, but an
  experiment would be lost with the browser's storage and could not follow
  its owner to another one. Retention would be out of the operator's hands.

## Consequences

- A compromise of Lab reaches whatever the database URL's role can. With the
  default URL, that is every service's schema. The operations guide
  describes the narrower role.
- Lab's readiness does not depend on the database. A deployment whose
  database is down still runs Lab, and saving fails with a message that
  names the database.
- Turning Lab on adds a migration hook and lets Lab through the bundled
  PostgreSQL's network policy.
