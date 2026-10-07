# Working on Primer

Primer is a self-hosted, multi-user RAG application. Not a "research
assistant" — it is Primer, and the name stands on its own.

This file is instructions for agents working in this repository. It is the
one documentation file that may be maintained as ordinary policy.

Harville Labs' org-wide standards
([Harville-Labs/agents](https://github.com/Harville-Labs/agents/blob/main/AGENTS.md))
apply too. This file adds to them and can tighten them; it departs from one
only where an ADR in `docs/adr/` says so.

## Repository policy

- **Never commit secrets.** Not in values files, not in tests, not in a
  comment explaining what a real one looks like. The chart references
  secrets and generates exactly one; nothing else is inlined.
- **`docs/superpowers/` stays untracked.** Those are agent-facing design
  notes and implementation plans. Everything else under `docs/` is the
  product's documentation and is tracked, reviewed, and expected to change
  alongside the code it describes.
- **Preserve unrelated changes.** The working tree may hold someone else's
  edit; commit what you were asked to change and leave the rest.
- **Do not rewrite shared history.** A branch you pushed and nobody has
  built on may be rebased with `--force-with-lease`. `main` never is.
- **Do not use `git reset --hard`.** To move work between branches, create
  a branch and cherry-pick, then `git branch -f` the one you are correcting.

## How a change gets made

Small, logical commits, each one a thing you could describe in a sentence.
Several stacked pull requests rather than one branch carrying a week.

**Commit messages say what changed and why it changed.** The why is the part
worth writing: a diff already shows the what. No co-author trailers, no
"Generated with" footers.

**No commit is finished until it is verified.** See the commands below. A
change to code with tests means running them; a change to a chart means
rendering it; a change to a generator means regenerating and diffing.

**When you fix a bug, prove the test catches it.** Put the bug back,
watch the test fail, restore it. A test written after the fix that has never
seen the failure is a test that might be asserting nothing. Say in the pull
request that you did this and what failed.

**Report what happened.** If tests fail, say so with the output. If you
skipped something, say which and why. Do not describe work as done that is
not.

## What a good change looks like

The standard is not "the symptom is gone". It is that someone reading the
result a year from now finds one obvious place where each rule lives.

- **Fix the cause.** If the symptom is a stale value, find why it is stale;
  do not add a refresh. If a message is unhelpful, find where the
  information was thrown away; do not append a hint to the string. A change
  that makes a bug invisible without making it impossible has moved it.
- **One home per rule.** If two call sites are about to do the same three
  things, give them a function and let both call it. This applies to fixture
  data and to prose, not only to logic: the same list of fields maintained
  in two functions will diverge, and the second one is the one nobody
  updates.
- **Extract on the second caller, not the first.** A helper written for one
  use is a guess about what the second will need, and it is usually wrong.
  Duplication is cheaper than the wrong abstraction right up until the
  moment the second caller exists - then it is not.
- **Do not work around a layer from outside it.** Reaching past a component
  to correct what it produced is a repair that has to be repeated every time
  that component changes. Fix it where it is produced, or work with what the
  layer already understands.
- **Leave nothing that asserts nothing.** A test with no assertions, a
  scaffold nobody calls, a parameter no caller passes, a branch that cannot
  be reached: delete it in the change that made it dead. It costs a reader
  the same attention as code that does something.
- **Say why the obvious alternative was rejected.** Where a change looks
  roundabout, the comment that earns its place is the one naming the
  straightforward thing that does not work, and why. Without it the next
  person simplifies it back into the bug.

## Working with `gh`

The GitHub CLI is the interface to issues, pull requests, and CI. Some
practical notes learned the hard way:

- **Write pull request bodies to a file and use `--body-file`.** A long
  `--body` through a shell heredoc is fragile. Put the file in the
  scratchpad directory, not the repository.
- **Pushes and `gh pr create` can take longer than a couple of minutes.**
  Run them in the background rather than watching a foreground command time
  out, then confirm with `git ls-remote` or `gh pr list`.
- **Check CI rather than assuming.** `gh pr checks <n>`, and
  `gh run list --branch main --limit 1` after a merge. When a job fails,
  `gh run view <id> --log-failed` is the fastest way to the actual error.
- **Pull requests are squash-merged.** A stacked branch therefore conflicts
  with `main` the moment its parent lands, because the parent's individual
  commits no longer exist. Replay only your own work:
  `git rebase --onto origin/main <last-commit-of-the-parent-branch>`, then
  push with `--force-with-lease`.
- **A stacked pull request targets its parent branch.** GitHub retargets it
  to `main` automatically when the parent merges.
- **Say what the pull request is for, and what you chose not to do.** The
  decisions worth reviewing are the ones with a defensible alternative:
  name them, give the reason, and make it easy to disagree.

### Issues

- Create implementation issues with `gh` once a plan is agreed.
- Priority labels are exactly: `priority:high`, `priority:medium`,
  `priority:low`, `priority:future`. Post-MVP roadmap work is
  `priority:future`.
- Every implementation issue carries scope, dependencies, acceptance
  criteria, and verification steps.
- Close issues from the pull request body (`Closes #12`) rather than by hand.

## Verifying

Run what your change touched. Before opening a pull request, run all of it.

```bash
# Python: lint, format, types
uv run ruff check .
uv run ruff format --check .
uv run ty check

# Python tests. Service suites take a PostgreSQL container (testcontainers,
# pgvector/pgvector:pg17) and start it themselves.
uv run pytest tests -q                      # repo, chart, compose, security policy
uv run pytest services/<name>/tests -q      # one service

# Web
cd apps/web && pnpm check && pnpm test      # svelte-check, vitest
pnpm -w run check                           # Biome, from the repository root
pnpm exec biome check --write apps/web/src  # fixes import order

# Generated artefacts. CI regenerates these and fails on any diff.
uv run python scripts/generate_diagrams.py  # docs/assets/diagrams
uv run python scripts/dump_openapi.py       # schemas/
pnpm run generate:api                       # apps/web/src/lib/api/generated
uv run mkdocs build --strict

# Chart
helm lint deploy/helm/primer
uv run pytest tests/helm -q
```

If you change a contract, a route, a diagram's source, or a chart template,
regenerate before committing. A red `main` is usually one of these.

The web commands run from `apps/web`, not the repository root: the root
workspace has Biome and nothing else, so `pnpm exec vitest` there fails to
find the binary rather than saying anything useful.

## How tests are written here

- **`tests/` holds policy tests**: things that must stay true about the
  repository, the chart, Compose, and security posture. They render and read
  rather than deploy. A rendered chart is read as YAML and asserted against.
- **Service tests live with their service** and use real dependencies where
  it matters — a real PostgreSQL, faked Control, Retrieval, and models. The
  fakes record what they were asked, so a test can prove something did *not*
  happen.
- **Name a test after the behaviour, not the function.** The docstring says
  why the behaviour matters, especially when the test guards against a
  plausible-looking mistake.
- **Guard against vacuous passes.** A loop over an empty list passes; assert
  that the list is not empty first.

## Working in the web app

- **Never mutate an object after it has gone into `$state`.** Svelte 5's
  proxy creates a signal per property the first time that property is read,
  and a later write to the *underlying* object does not go through the proxy
  - so the screen keeps rendering the value from first read while the object
  itself has moved on. Hold the plain value in a local and write it back
  through the container (`items[index] = {...}`), rather than keeping a
  reference to something you already handed to `$state`. This is worth
  knowing because it fails silently and looks like a stale cache: reloading
  the page shows the right thing, which sends you looking at the server.
- **Runes only work in `.svelte` and `.svelte.ts` files.** Vitest collects
  `src/**/*.test.ts`, and a `.test.ts` cannot contain `$state` - so reactive
  logic that needs a test goes in a `.svelte.ts` module the test imports.
  That constraint is a useful one: it pushes state out of the components and
  into somewhere it can be asserted against.
- **The browser never reaches Control, Chat, or Retrieval.** It calls
  SvelteKit, which calls them with the identity headers the proxy set. A new
  call to a service belongs in `src/lib/server/` or a `+server.ts`, never in
  a component - a browser that could call a service directly could set those
  headers itself and be anyone.
- **Types come from the schemas, not from hand.** `src/lib/api/types.ts`
  aliases the generated types so a field renamed in Python is a build
  failure here rather than `undefined` at runtime. Add an alias there rather
  than describing a shape a second time.

## Failures that cross a service boundary

A dependency failing is not the same thing as Primer being broken, and the
difference is the whole content of the message someone reads.

- **Translate a dependency's failure at the boundary that calls it**, into
  an error named for what was actually unreachable. A transport error or a
  raised HTTP status that reaches a catch-all arrives at a user as the
  generic "something went wrong" that every unrelated bug also produces, and
  sends whoever reads it to look at the wrong component.
- **Say whether it is worth trying again.** The caller is the only party
  that knows; a code alone does not carry it.
- **Guard both directions of a dependency.** When one route is taught to
  handle an unreachable dependency, the other routes that touch it usually
  still have to be - the read path and the write path fail the same way and
  are fixed in different files.

## Code and prose style

- **Comments explain why, not what.** The code says what it does. A comment
  earns its place by recording a decision, a hazard, or the reason an
  obvious alternative was rejected.
- Write for a reader who is capable but has not been in this file before.
- **No vendor-specific asides.** Do not name the tools that generated the
  code or the services around it unless the reference is genuinely relevant
  to the reader.
- Match the surrounding density and idiom. This codebase comments more than
  most, and the comments are prose.

## Product direction

- Self-hosted and multi-user, for students and researchers.
- Libraries are private. The authorization model must keep sharing possible
  later; `LibraryAccess` is the only thing that decides access, and routes
  pass a predicate rather than comparing owners.
- Answers cite the passages they came from, and a user can copy or export a
  response with its citations.
- **Primer never implements authentication flows.** Authentication is
  toggleable, and provided by generic OIDC through `oauth2-proxy` — Authentik
  is the first tested provider. Primer trusts the headers the proxy sets and
  the ingress strips inbound copies of them.
- Any OpenAI-compatible endpoint: vLLM, Ollama, llama.cpp, or a hosted API.
  Assume operators may also run a web search MCP server.
- Use Haystack, Docling, and other established libraries rather than
  hand-rolling parsing, chunking, embeddings, retrieval adapters, or tool
  protocols.
- **Tools are operator-controlled, user-approved, and isolated** through
  ephemeral MCP sandbox providers. Never expose the host shell.
- The vector store is chosen per deployment and stays behind Haystack's
  document-store integrations.

## Technology constraints

- Python 3.13, `uv`, Ruff, Astral `ty`, pytest. Type checking is
  `uv run ty check`; do not add mypy or Pyright.
- Dependencies go in `[dependency-groups]`, never
  `[project.optional-dependencies]`. The build backend is `uv_build`.
- SvelteKit 2 with Svelte 5 runes, TypeScript, pnpm, Biome, Node 24.
- UI components come from `@sivir-ui/svelte` (https://www.sivir.dev); icons
  from `@lucide/svelte`.
- Primer keeps light and dark reading themes rather than the HL dark-only
  visual palette. Theme Sivir through shared semantic tokens in `app.css`.
  See [ADR 0001](docs/adr/0001-reading-themes.md).
- The web app is a **desktop-dense application shell**, not a scaled-up
  mobile column.
- Alembic for migrations, per-service schemas. **Never run migrations during
  application startup** — the chart runs them as a pre-install hook.
- Kubernetes for shared deployments; Docker Compose for local single-user
  ones. Image files are `deploy/images/Dockerfile.python`,
  `Dockerfile.worker`, and `Dockerfile.gui`.
- **Compose provides no authentication.** Auth is reserved for Kubernetes
  deployments.
