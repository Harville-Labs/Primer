# 0005. Let Lab load a small open model of its own

- Status: Accepted
- Date: 2026-10-31

## Context

Primer runs no generation model. Chat sends every question to an
OpenAI-compatible endpoint the operator chooses, and that is deliberate:
the operator decides what hardware answers questions, and Primer does not
pick a model for them.

Lab is a research section with two tabs. **Neurons** shows what happens
inside a model as it reads a prompt, then lets a user edit it. **Anomalies**
flags unusual model behaviour. Most of that cannot be done through an
endpoint:

- **An endpoint returns at most top-k logprobs.** vLLM caps them at 20 by
  default. So entropy from an endpoint is a truncated estimate, and the
  prompt's own logprobs are an extension only some servers offer.
- **No endpoint exposes hidden states**, MLP activations or attention. The
  logit lens, neuron and SAE feature views, activation outliers and linear
  probes all read those.
- **No endpoint accepts an intervention.** Ablating a neuron, clamping a
  feature or adding a steering vector all change the forward pass, so
  Primer has to run that pass itself.

Surprisal, perplexity and truncated entropy can come from the operator's
chat endpoint, and Chat will score them there. Everything else needs model
weights loaded inside Primer.

## Decision

Lab is an **optional service**, off by default, that loads an open model
itself and keeps one resident at a time.

- **Chat stays bring-your-own.** Lab never answers chat questions, and
  turning it on changes nothing about how Chat chooses a provider. It is a
  research instrument, not a second chat backend.
- **Users choose the model; the operator bounds the choice.** A user names
  either a Hugging Face repository, with an optional revision, or a path
  inside the weights volume: a fine-tune, a checkpoint from someone's own
  training run, a model Primer has never heard of. The point of the
  section is to look inside, and edit, the model a researcher actually
  cares about, and the community work Lab is modelled on - steering a
  Qwen model's personality by editing a handful of directions - is done on
  whichever model the researcher picked. The operator sets the model
  loaded at startup (Qwen3-1.7B, which runs on CPU and has published SAEs,
  by default), which repositories may be named, the largest download
  allowed, and whether users may choose at all.
- **One model is resident, and switching it is shared.** A GPU holds one
  model well. A user who loads a different one replaces it for every other
  user, the switch waits for the run in progress like any other run, and
  the status every user sees names the model that is loaded. A per-user
  model would need a GPU per user, which no deployment Primer is built
  for has.
- **Any model is accepted only in a form that cannot run code.** Its
  `config.json` must name an architecture `transformers` implements itself,
  because the alternative is the checkpoint's own Python, and its weights
  must be safetensors. A model that fails either check is refused with the
  reason, not loaded with a warning.
- **Lab downloads only files that are data.** Fetching a named repository
  asks the hub for its file list first and refuses before downloading
  anything if it has no safetensors weights or is larger than the
  operator's limit. Then only safetensors, JSON, and tokenizer
  vocabularies are downloaded - a pickle or a Python file in the repository
  is never written to disk. Downloads are cached in the weights volume by
  the commit they resolved to, so a repository is fetched once and a
  moving branch cannot change what a cached name means.
- **Weights load as safetensors only, with `trust_remote_code=False`.** A
  pickle checkpoint can run code when it is loaded, and remote code is code
  by definition. Both are refused, not warned about. This matters more,
  not less, once users name their own models: a checkpoint from anywhere
  is exactly where a pickle payload would come from. Loading always passes
  `local_files_only`, so the only code that reaches the network is the
  fetch described above.
- **Egress to the model hub, and nothing else.** Only Web may reach Lab.
  Lab reaches the Hugging Face hub, or the mirror the operator configures
  instead, to fetch what a user named; an operator who turns user choice
  off can deny egress entirely. Lab calls no other hosted service,
  Neuronpedia included; feature labels, when used, are a file the operator
  supplies.
- **Lab gets its own image, `deploy/images/Dockerfile.lab`**, with CPU and
  CUDA build targets. torch and CUDA are several gigabytes, and no other
  service needs them. This departs from the list of image files in
  `AGENTS.md`, which this ADR amends.

## Alternatives considered

- **Use the chat endpoint for everything.** It cannot provide activations
  or interventions, so Lab would be reduced to the surprisal view. Chat
  will still offer that view where the endpoint declares logprob support.
- **Point Lab at a second, operator-run server that exposes internals.**
  No common server protocol carries hidden states or edits. Primer would be
  defining one, and someone would have to run a server that speaks it,
  which is the same model loaded in the same way with a network hop added.
- **Put torch in the worker image, which already has it for Docling.**
  That image is built for CPU document parsing. A CUDA build would make
  every ingestion worker gigabytes larger for a service most deployments
  never turn on, and it would tie Lab's torch version to Docling's.
- **Only models the operator listed.** Simpler to test, and no egress, but
  it makes every model a researcher wants to look at a request to the
  operator and a restart. The safety of loading comes from the format
  checks, not from who chose the name, so the list protects less than it
  costs. An operator who wants it anyway can restrict the repositories
  users may name, or turn choice off.
- **A separate fetch step, with Lab offline.** That keeps the process that
  holds the model off the network, but a step run by the operator cannot
  answer a user's choice made in the browser.
- **Let users upload weights through the browser.** Gigabytes per upload,
  per user, on a shared volume, and a stranger's file loaded into a process
  every other user shares. Not ruled out, but it is a separate decision
  with its own storage and isolation design, and this one does not make it.
- **TransformerLens instead of `transformers` with nnsight.**
  TransformerLens re-implements each architecture, so each model needs a
  port and its output can drift from Hugging Face's. nnsight records and
  edits the real implementation, so any model `transformers` loads works.

## Consequences

- Primer now has one component that loads model weights for generation. A
  change to it is reviewed against the constraints above: safetensors
  only, no remote code, data files only on download, and network access
  only in the fetch.
- Lab cannot promise every view for every model. Logit lens and neurons
  work wherever nnterp can name the layers; SAE features exist only where
  someone has trained SAEs for that model. The UI says which views a model
  supports rather than failing inside one.
- The default install is unchanged. A deployment that never enables Lab
  never pulls the Lab image or the weights.
- Lab's hardware needs are its own, and now depend on what users pick.
  Qwen3-4B in bf16 is about 8 GB of weights, so the docs name Qwen3-1.7B
  for CPU-only machines, and the download limit is also the operator's way
  of keeping a model from being chosen that the hardware cannot hold.
- Users who share a Lab share its model. Two people alternating between
  models make each other wait for loads; the status names the loaded model
  and any switch in progress, and a deployment where that matters runs more
  than one Lab.
- The weights volume grows with every model anyone names. Cached models
  are not yet evicted, so the operator sizes the volume or clears it.
- A third image to build, scan and publish, with two targets.
