# 0009. Build Lab's attribution graphs in an environment of their own

- Status: Accepted
- Date: 2026-11-01

## Context

An attribution graph traces which features carried a prompt to a predicted
token, and through which others. Building one means replacing each layer's
MLP with a transcoder, running the prompt, and solving for each feature's
direct effect on the rest. circuit-tracer does this, and reads the
transcoders published for Qwen3-4B.

circuit-tracer 0.5.0 is the release that reads those transcoders. It pins
transformer-lens 3.2, which needs transformers 4.x, and nnsight below 0.8.
Lab's lock has transformers 5 and nnsight 0.8, which nnterp and the rest of
Lab are built on. One lock cannot hold both.

A graph is also expensive in ways a forward pass is not. The transcoders
for Qwen3-4B are about 60GB. circuit-tracer loads its own copy of the
model. On a GPU a graph takes seconds; on a CPU it takes many minutes.

## Decision

The graph builder is a separate uv project, `services/lab-graphs`, with its
own lock, excluded from Primer's workspace. Lab runs it as a program,
`python -m primer_graphs`, once per graph.

- **Lab decides, the builder computes.** Lab tokenizes the prompt, checks
  every bound, fetches and checks the transcoders, and hands the builder a
  `GraphBuild` on stdin naming the files to read. The builder runs offline,
  and gets an environment with every `PRIMER_` variable removed, so it holds
  no database URL.
- **A build takes the run queue** like any other run, so it never shares the
  device with another user's forward pass. Its time limit,
  `PRIMER_LAB_GRAPH_TIMEOUT_SECONDS`, is at most 240 seconds. With the
  queue's 30 seconds of grace that stays under the five minutes the web
  server's fetch waits for Lab's response to begin. A longer limit would let
  Lab finish a graph nobody was still waiting for.
- **Graphs are for a GPU.** The builder ships only in the `-graphs` image,
  which is the CUDA image with the builder installed beside Lab at
  `/opt/graphs`. The image sets `PRIMER_LAB_GRAPH_PYTHON`, which is what
  turns graphs on, so the image tag is the switch. The chart refuses
  `lab.graphs.enabled` without `lab.gpu`.
- **Transcoders are fetched only where a graph can be built.** Without a
  builder, Lab fetches none, rather than spending 60GB on files nothing
  reads. They have their own size limit, because they are several times the
  size of the model they explain.
- **A prompt holding a special token is refused.** circuit-tracer zeroes
  what reaches the graph from the token it puts in front of the prompt. For
  Qwen3, that token is `<|endoftext|>`, the attention sink the model leans
  on. A second special token later in the prompt would be a sink nothing
  zeroes, and the graph would credit it with most of everything. The prompt
  is read as plain text, without the chat template, for the same reason.

## Alternatives considered

- **Pin Lab to transformers 4.x.** One environment, and graphs built in
  process. It would hold the rest of Lab, and nnterp's model support, to
  the version circuit-tracer needs, for a feature most deployments leave
  off.
- **A later circuit-tracer.** Later releases changed the transcoder loader's
  arguments and do not read the published Qwen3 sets as they are.
- **A separate service.** The builder would run in its own pod, with its own
  copy of the model loaded all the time. That is a second GPU allocation for
  a feature used now and then, and a second place that must agree with Lab
  about which model is resident. A subprocess loads the model only while it
  builds, and only ever the one Lab has.
- **Return the graph later, by polling.** That would lift the five-minute
  ceiling and let graphs run on a CPU. It needs a job store, cancellation,
  and a way to hand the device back mid-build, for a build that a GPU
  finishes in seconds anyway.

## Consequences

- A build holds two copies of the model in memory: Lab's, and the
  builder's while it runs. A GPU for graphs needs room for both, plus the
  transcoders it has open.
- The weights volume holds the transcoders beside the models. For Qwen3-4B
  that means at least 100Gi.
- The `-graphs` image is the largest Lab image, and CI builds a third Lab
  image on every run.
- The builder's dependencies are updated on their own schedule, through
  their own lock, and CI runs its tests in a job of their own.
