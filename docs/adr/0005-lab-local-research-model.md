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
- **The operator chooses the model** from an allow-list in configuration:
  Qwen3-1.7B, which runs on CPU and has published SAEs, and Qwen3-4B, which
  wants a GPU. Users choose among what the operator allowed. They cannot
  name a Hugging Face repository.
- **Weights are fetched once, by a separate step.** In Compose that is a
  one-shot service; in the chart, a Job. The step writes into a volume that
  Lab mounts read-only. Lab itself runs with `HF_HUB_OFFLINE=1` and
  contains no code that downloads.
- **Weights load as safetensors only, with `trust_remote_code=False`.** A
  pickle checkpoint can run code when it is loaded, and remote code is code
  by definition. Both are refused, not warned about.
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
- **TransformerLens instead of `transformers` with nnsight.**
  TransformerLens re-implements each architecture, so each model needs a
  port and its output can drift from Hugging Face's. nnsight records and
  edits the real implementation, so any model `transformers` loads works.

## Consequences

- Primer now has one component that loads model weights for generation. A
  change to it is reviewed against the constraints above: the allow-list,
  safetensors only, no remote code, offline at runtime.
- The default install is unchanged. A deployment that never enables Lab
  never pulls the Lab image or the weights.
- Lab's hardware needs are its own. Qwen3-4B in bf16 is about 8 GB of
  weights, so the docs name Qwen3-1.7B for CPU-only machines.
- A third image to build, scan and publish, with two targets.
