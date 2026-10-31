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

## Docker Compose

Turn on the `lab` profile:

```bash
COMPOSE_PROFILES=lab docker compose -f deploy/compose/compose.yaml up --wait
```

On a machine with an NVIDIA GPU and the NVIDIA Container Toolkit, add the GPU
override, which builds the CUDA image and gives Lab the GPU:

```bash
COMPOSE_PROFILES=lab docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.gpu.yaml up --wait
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

Lab accepts connections from the web app and nothing else, and it is given
none of Primer's shared secrets: no database, no internal token. It needs
egress to the Hugging Face hub, or to your mirror, to download a model.
