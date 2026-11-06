# 0015. Bundle llama.cpp for embeddings and reranking, and refuse a changed embedder

- Status: Accepted
- Date: 2026-11-05

## Context

Primer runs its own embedding model and reranker so that an install can
index and search with nothing configured. Until now both ran on Text
Embeddings Inference (TEI), on CPU. That had three costs.

- **TEI's CPU image is published for amd64 only.** Primer's own images are
  built for arm64 too, so the one thing stopping a default install from
  running natively on a DGX Spark, another GB10 machine, or Apple Silicon
  was the bundled model server. On an arm64 Linux host it failed with
  `exec format error`.
- **TEI could not serve the reranker that matches the embedder.**
  `Qwen3-Reranker-0.6B` scores through its language-model head, and TEI's
  `/rerank` needs a sequence classifier. The bundled reranker was
  `BAAI/bge-reranker-base`, chosen for what TEI could run, not for how
  well it reranks.
- **Changing the embedder went unnoticed.** Qdrant checks a vector's width
  and nothing else. Two models of the same width produce vectors Qdrant
  accepts without complaint, and every search after the swap compares a
  question in one model's space with passages in another's. Answers still
  cite passages. They are worse, and nothing says why.

Many operators already run a model server: vLLM on a GPU node, Ollama on a
workstation. Whatever Primer bundles has to give way to that cleanly.

## Decision

**The bundled server for both roles is llama.cpp's `llama-server`**, from
`ghcr.io/ggml-org/llama.cpp`, pinned to one build.

- It is published for amd64 and arm64, with CPU, CUDA 12 and CUDA 13 tags,
  so the default install runs natively on every machine Primer's images
  support.
- It serves `/v1/embeddings` and `/rerank`, and runs `Qwen3-Reranker-0.6B`,
  so the reranker matches the embedder.
- It loads GGUF weights by Hugging Face repository (`-hf`), so changing the
  bundled model is a value, not an image.
- The build is pinned. A server that pools or tokenizes differently returns
  vectors that are plausible, fail nothing, and retrieve worse, so the
  build a model was checked against is part of that model.

**Your own server is the same interface.** Setting the embedding or rerank
base URL to any OpenAI-compatible server (vLLM, llama.cpp, Ollama, a hosted
API) replaces the bundled one. In the chart that is exclusive with
`inference.*.bundled.enabled`, and setting both is refused, so one of them
cannot be silently ignored. What the server accepts, a batch size and an
input limit, is declared in configuration rather than discovered, because
an OpenAI-compatible server says nothing about its limits until a request
breaks one.

**A collection records the embedder that built it.** Retrieval embeds a
fixed probe text and stores the vector in the Qdrant collection's metadata.
Each process checks its embedder against the record before the first
search or write, and refuses with a 409 if the cosine similarity is below
0.98. A collection with no record adopts the current embedder's.

## Consequences

- An arm64 host runs the default install with nothing replaced.
- The bundled embedder is Qwen's own Q8_0 GGUF of the same model TEI
  served. Its vectors are close enough to the old ones that an existing
  collection adopts the new fingerprint and keeps working.
- On CPU, reranking twenty passages with `Qwen3-Reranker-0.6B` takes about
  five seconds on fifteen cores. `bge-reranker-base` on TEI took well under
  a second. That is the price of a reranker that separates answers from
  passages that only share a topic. A node that cannot spare it can lower
  `candidates` or turn the bundled reranker off.
- Its scores are close to binary, so a relevance floor set against it
  belongs close to zero.
- A swapped embedder is caught when a process starts using the collection,
  not when the server behind the same URL changes. A model replaced under
  a running Retrieval is caught at its next restart.
- The check runs at first use, not at startup. A Retrieval whose embedder
  is down still starts and reports the embedder as the thing that is
  unreachable, rather than failing to start over something it could not
  check.

## Alternatives considered

- **Keep TEI and document a replacement for arm64.** That is what the docs
  said before this. It made the most capable small machine many people
  would run Primer on the one that needed extra setup.
- **Bundle vLLM.** It is the server most GPU operators already run, and it
  is the recommended thing to point Primer at. As a bundled default it needs
  a GPU, its images are large, and its CPU support is limited.
- **Ollama.** Its embedding API is OpenAI-compatible, but it does not serve
  `/rerank`, and it chooses pooling and context per model in ways a
  configuration cannot pin.
- **Record the model's name instead of a probe vector.** A name says what
  the operator typed, not what the server loaded. Two servers given the same
  name and different weights, or the same weights pooled differently, are
  exactly the case this has to catch.
