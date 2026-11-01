# 0007. Open Qwen-Scope's SAEs as weights-only pickles

- Status: Accepted
- Date: 2026-10-31
- Supersedes in part: [0005](0005-lab-local-research-model.md), on loading
  only safetensors

## Context

[ADR 0005](0005-lab-local-research-model.md) lets Lab load a model of its
own and holds it to a rule: weights load as safetensors only, and a pickle
is never written to disk, because unpickling a file can run code.

Lab's feature view reads sparse autoencoders. The SAEs published for the
model Lab loads by default, Qwen's own Qwen-Scope set for Qwen3-1.7B, are
`torch.save`d files: one `layerN.sae.pt` per layer, each a dict of four
tensors. No safetensors copy is published. The rule in ADR 0005 would mean
the default model ships with no feature view at all.

## Decision

Lab opens SAE layer files with `torch.load(weights_only=True)`, and with
nothing else.

- **The weights-only loader is the boundary.** It rebuilds tensors and
  plain containers, and refuses any other global in the pickle instead of
  importing it. A file that needs anything more is refused with its name,
  and the global it asked for goes to the log, not to the user. A test
  plants a payload that would create a file when unpickled and asserts the
  file never appears.
- **Only SAE layer files are opened this way.** Model weights stay
  safetensors only, exactly as ADR 0005 says, and a local model directory
  that holds a `.pt` file is still refused.
- **Only the files the SAE config names are downloaded.** Lab fetches
  `config.json`, validates it, then fetches exactly the `layerN.sae.pt`
  files it lists. Nothing else in the repository reaches the disk.
  Qwen-Scope's repository also holds a demo `app.py`.
- **The shapes are checked before anything is encoded.** The config must
  describe a TopK SAE on the residual stream, as wide as the loaded model,
  for layers the model has. Each file must hold exactly the four tensors
  the config implies.
- **The operator decides which SAEs go with which model**, through
  `PRIMER_LAB_SAES`. Users cannot name an SAE repository. The default maps
  Qwen3-1.7B and Qwen3-1.7B-Base to Qwen-Scope.

## Alternatives considered

- **Convert to safetensors ahead of time.** This keeps pickles out of the
  process entirely, and an operator who wants that can host a converted
  copy and map it instead. Lab still accepts only the `.sae.pt` layout, so
  a converted copy needs a format change first. As the default, conversion
  is a step every operator has to run before Lab can use a set it can
  already name.
- **Convert on download, in a separate process.** The pickle is still
  opened, just elsewhere. This adds a process without removing the risk it
  is meant to contain.
- **Use sae-lens to load and encode.** It would bring about thirty
  packages into the Lab image, including TransformerLens, datasets, wandb
  and plotly. The encode itself is two lines.
- **Ship no feature view until safetensors SAEs exist for a small model.**
  None exist for Qwen3, so this means no feature view.

## Consequences

- Lab now opens one kind of pickle. Its safety rests on torch's
  weights-only loader. A change to `saes.py` is reviewed against that, and
  a torch upgrade that changes the loader's defaults is a change to it.
- The default SAEs are about 15 GB for 28 layers. They download after the
  model has loaded, so the model is usable in the meantime, and they count
  towards the weights volume.
- The Qwen-Scope licence restricts use to scientific research. The
  operator turns this on, so the operations guide says so where it
  describes the default.
