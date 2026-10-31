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

Lab is an **optional service**, off by default, that loads one small open
model itself and keeps it resident.

- **Chat stays bring-your-own.** Lab never answers chat questions, and
  turning it on changes nothing about how Chat chooses a provider. It is a
  research instrument, not a second chat backend.
- **The operator chooses the models**, as an allow-list in configuration.
  Each entry is either a Hugging Face repository, which the fetch step
  downloads, or a directory of the operator's own weights placed in the
  weights volume: a fine-tune, a checkpoint from their own training run, a
  model Primer has never heard of. Qwen3-1.7B, which runs on CPU and has
  published SAEs, and Qwen3-4B, which wants a GPU, are the documented
  defaults, not the limit. Users choose among what the operator listed.
  They cannot name a repository or a path.
- **Any model is accepted only in a form that cannot run code.** Its
  `config.json` must name an architecture `transformers` implements itself,
  because the alternative is the checkpoint's own Python, and its weights
  must be safetensors. A directory that fails either check is refused at
  startup with the reason, not loaded with a warning.
- **Weights are fetched once, by a separate step.** In Compose that is a
  one-shot service; in the chart, a Job. The step writes into a volume that
  Lab mounts read-only. Lab itself runs with `HF_HUB_OFFLINE=1` and
  contains no code that downloads.
- **Weights load as safetensors only, with `trust_remote_code=False`.** A
  pickle checkpoint can run code when it is loaded, and remote code is code
  by definition. Both are refused, not warned about. This matters more,
  not less, once operators bring their own weights: a checkpoint from
  anywhere is exactly where a pickle payload would come from.
- **No egress.** Only Web may reach Lab. Lab calls no hosted service,
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
- **Only the allow-listed Qwen models.** Simpler to test, but researchers
  most want to look inside the model they trained or fine-tuned, and the
  safety of loading comes from the format checks, not from the name.
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
  change to it is reviewed against the constraints above: the allow-list,
  safetensors only, no remote code, offline at runtime.
- Lab cannot promise every view for every model. Logit lens and neurons
  work wherever nnterp can name the layers; SAE features exist only where
  someone has trained SAEs for that model. The UI says which views a model
  supports rather than failing inside one.
- The default install is unchanged. A deployment that never enables Lab
  never pulls the Lab image or the weights.
- Lab's hardware needs are its own. Qwen3-4B in bf16 is about 8 GB of
  weights, so the docs name Qwen3-1.7B for CPU-only machines.
- A third image to build, scan and publish, with two targets.
