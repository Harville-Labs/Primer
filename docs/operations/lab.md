# Lab

Lab loads a small open model and lets a user look inside it while it runs:
which features a prompt lights up, and what the output does when one of them
is turned up or down. It is the one part of Primer that runs a generation
model itself rather than calling an endpoint, which is why it is optional and
why it needs a machine sized for a model. [ADR 0005](../adr/0005-lab-local-research-model.md)
records why it works this way.

It is off unless you turn it on.

## Hardware

Lab holds one model in memory at a time and runs one request at a time.
Everything else waits in a queue.

| Model | Where | Memory | Notes |
| --- | --- | --- | --- |
| `Qwen/Qwen3-1.7B` (the default) | CPU | 12 GB RAM | `float32` on CPU, so about 7 GB of weights. A run of the default length takes tens of seconds |
| `Qwen/Qwen3-4B` | NVIDIA GPU | 12 GB or more of GPU memory | `bfloat16`, about 8 GB of weights. A run takes a few seconds |

A model too large for the GPU fails to load, and Lab says so and puts the
previous model back. One too large for the container's
memory is a different matter: the kernel kills the process, and Lab restarts
on its startup model. Either way, the way to keep a model off hardware that
cannot hold it is the download limit, set below what fits.

The GPU image needs a driver that supports CUDA 13.2, and on Kubernetes the
NVIDIA device plugin, which is what makes `nvidia.com/gpu` a resource a pod
can request.

## The weights volume

Lab downloads a model the first time someone chooses it and keeps it in its
weights volume, mounted at `/models`. Nothing is fetched ahead of time, so the
first start downloads the default model, which takes as long as about 4 GB
takes on your connection.

**Nothing is evicted.** The volume grows by one model for every model anyone
chooses, and a full volume makes the next download fail. Size it for the
models you expect, narrow which repositories may be chosen, or leave the
choice to yourself:

- `lab.repositoryPatterns` (`PRIMER_LAB_REPOSITORY_PATTERNS`) limits which
  repositories may be named, for example `["Qwen/*"]`.
- `lab.modelChoice` (`PRIMER_LAB_MODEL_CHOICE`) says who may switch the
  model: `users`, `admins`, or `operator`, which leaves it to configuration.
- `lab.maxModelBytes` (`PRIMER_LAB_MAX_MODEL_BYTES`) refuses a repository
  whose weights are larger, before any of it downloads.

To free space, delete a model's directory under `hub/` in the volume. Lab
downloads it again if someone chooses it later.

### Your own weights

A directory you put in the volume can be loaded by its path relative to
`/models`, as `{"source": "local", "path": "my-finetune"}`. It needs the
files a Hugging Face model directory has: `config.json`, the tokenizer, and
the weights as `.safetensors`, for an architecture `transformers` implements
itself. Lab never runs code that ships with a model, and refuses a directory
that also holds pickled weights (`.bin`, `.pt`, `.ckpt`), because unpickling
can run code: remove them.

### SAE features

The Neurons tab can show which sparse-autoencoder features fire at each
token, for a model someone has trained SAEs for. You choose the SAEs, not
your users. `lab.saes` (`PRIMER_LAB_SAES`) maps a model repository to the
SAE repository Lab reads with it. By default it maps Qwen3-1.7B and
Qwen3-1.7B-Base to Qwen's
[Qwen-Scope](https://huggingface.co/Qwen/SAE-Res-Qwen3-1.7B-Base-W32K-L0_50)
set: 32,768 features on the residual stream after each of the 28 layers.

Before you keep that default, know three things about it:

- **It is about 15GB**, on top of the model, in the same volume. It
  downloads after the model has loaded, so the model is usable in the
  meantime, and `lab.maxModelBytes` applies to it too.
- **Its licence permits scientific research only.** Read the repository's
  terms before you offer it to anyone.
- **Its files are pickles.** Lab opens them with torch's weights-only
  loader, which rebuilds tensors and refuses anything else, and it
  downloads only `config.json` and the layer files the config names.
  [ADR 0007](../adr/0007-lab-saes-as-weights-only-pickles.md) records why.

Lab reads TopK SAEs on the residual stream laid out as Qwen-Scope's are,
and refuses any other kind with the reason, rather than show features it
has computed wrongly. The status in the Neurons tab says whether the SAEs
are downloading, ready, or refused.

To turn the feature view off, set `lab.saes: null` in the chart or
`PRIMER_LAB_SAES={}` in Compose.

## Editing a run

A generation runs the prompt twice, greedily: once as the model is, and
once with the edits applied at every step. Greedy, so the edits are the
only thing that can make the two answers differ, and the stream says where
they first do. An edit names a site, a layer, and an operation:

- **Sites**: the attention output, the MLP's neurons, the MLP output, the
  residual stream after the layer, or an SAE feature at a layer that has
  an SAE.
- **Operations**: `zero`, `mean` (the site's average over the prompt),
  `scale`, `clamp` to a value, or `add` a value or a steering vector.
- **Positions**: every text token, the prompt, the generated tokens, or a
  span. Only a span can edit the chat template's own tokens.

A feature is edited through its SAE's decoder, adding only the change in
that feature: what the SAE does not reconstruct is left alone.

A steering vector is the difference between the mean residual stream of two
sets of prompts, read at each one's last token. Lab keeps it, in memory and
for the person who made it, until the model changes.

How long a run may be is the operator's choice: the run limits under
[Configuration](configuration.md#lab) bound the prompt, the answer, and how
many edits one run applies. The GPU is shared, and a run holds it from its
first step to its last.

## Saved experiments

Nothing Lab does is kept unless someone saves it. A saved experiment holds
the prompt, its edits or detector settings, the result, and the steering
vectors and probes the run used, so it can be opened again after the model
has changed. It is private to the person who saved it: to anyone else, it
does not exist.

Lab keeps them in the shared PostgreSQL, in a `lab` schema of its own
([ADR 0008](../adr/0008-lab-saves-experiments-in-postgresql.md)). Without a
database URL, Lab runs as before and says that saving is unavailable.

An experiment is kept for `PRIMER_LAB_EXPERIMENT_RETENTION_DAYS` days from
when it was saved, 90 by default, and then deleted. Lab checks every hour,
and an expired experiment is left out of every list and read before then,
so nobody sees one past its date. The web app shows each experiment's
expiry date. Shortening the period deletes the newly expired ones within the
hour; lengthening it keeps the rest longer, but brings back none already
deleted.

Opening an experiment puts its vectors and probes back, under the ids the
saved request names, so the run can be repeated as it was. It does that
only on the model it was saved from: a vector is a direction in one model's
residual stream and means nothing in another's.

Lab's database URL can reach every service's schema. To limit what a
compromise of Lab could read, give it a URL for a role granted only the
`lab` schema:

```sql
CREATE ROLE primer_lab LOGIN PASSWORD '...';
GRANT USAGE ON SCHEMA lab TO primer_lab;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lab TO primer_lab;
```

Run this after the first migration has created the schema. The migration
itself still needs the role that owns it.

## Docker Compose

Turn on the `lab` profile, and tell Control where Lab is so the web app
shows its section. Compose can't tell Control which profiles are on, so this
is a second variable:

```bash
COMPOSE_PROFILES=lab PRIMER_LAB_SERVICE_URL=http://lab:8000 docker compose up --wait
```

On a machine with an NVIDIA GPU and the NVIDIA Container Toolkit, add the GPU
override, which builds the CUDA image and gives Lab the GPU:

```bash
COMPOSE_PROFILES=lab PRIMER_LAB_SERVICE_URL=http://lab:8000 docker compose -f compose.yaml -f deploy/compose/compose.gpu.yaml up --wait
```

The weights live in the `lab-models` volume. `env.example` lists the
variables, and [Configuration](configuration.md#lab) what each one does.

## Kubernetes

```yaml
lab:
  enabled: true
```

That runs one Lab pod on CPU with a 50Gi volume. For a GPU node:

```yaml
lab:
  enabled: true
  model:
    source: hub
    repository: Qwen/Qwen3-4B
  gpu: 1
  nodeSelector:
    nvidia.com/gpu.present: "true"
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
```

The pod is replaced rather than rolled on an upgrade, so Lab is unavailable
while the new one loads its model. Two pods would each need the volume and a
copy of the model in memory, and on a GPU node there is rarely room for the
second.

Lab accepts connections from the web app and nothing else. Of Primer's
shared secrets it is given the database URL alone, for saved experiments: no
internal token, no settings key. It needs egress to the Hugging Face hub, or
to your mirror, to download a model.

Experiments are kept for 90 days unless you say otherwise:

```yaml
lab:
  enabled: true
  experimentRetentionDays: 30
```
