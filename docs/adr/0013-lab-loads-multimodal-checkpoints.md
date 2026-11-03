# 0013. Load a multimodal checkpoint as the model it is

- Status: Accepted
- Date: 2026-11-02

## Context

Lab loads a model with nnterp's `StandardizedTransformer`, which is nnsight's
`LanguageModel` with Lab's layer names mapped onto it. Qwen3.5 is published
only as an image-text model: every checkpoint's `config.json` names
`Qwen3_5ForConditionalGeneration`, a language model with a vision tower in
front of it. nnsight loads through `AutoModelForCausalLM` by default, and
there it refuses any `model_type` that `transformers` lists as
image-text-to-text.

Everything Lab reads, the residual stream, the MLPs, attention, the
logits, is in the language model. A text prompt never reaches the vision
tower.

Qwen3.5's language model is also a hybrid. Three in every four layers mix
tokens with a gated delta rule, a form of linear attention, at
`linear_attn`. Only the fourth has a softmax attention at `self_attn`.
nnterp checks for `self_attn` on layer 0 and refuses the model when it is
missing.

## Decision

Lab loads a multimodal checkpoint whole, as the class its config names.

- **An image-text `model_type` loads through
  `AutoModelForImageTextToText`.** nnsight takes the auto class as an
  argument and skips its refusal when it is not the default. Everything
  else about the load is nnsight's ordinary path: tokenizer, padding,
  generation. A text model loads as before.
- **The language model's sizes come from the text config.** A composite
  config has no top-level `hidden_size`, `intermediate_size` or
  `eos_token_id`. `LoadedModel.text_config` holds `get_text_config()`,
  which for a text model is its config unchanged, and every route that
  needs a width reads it there.
- **An architecture that generates no text is refused before download.**
  The config check already reads `config.json` first. It now also refuses
  a `model_type` that `transformers` cannot load as a causal or an
  image-text-to-text model, such as T5.
- **A hybrid skips nnterp's attention check.** When the text config lists
  `linear_attention` among its `layer_types`, Lab passes
  `RenameConfig(ignore_attn=True)`. The attention on the softmax layers is
  still mapped and readable.
- **Lab knows which layers have attention.** `LoadedModel.attention_layers`
  holds them, and a read or an edit of the attention output anywhere else
  is refused with the layers that do have one. Without that check the
  request fails inside the pass, as an nnsight exception, and reaches the
  person as a failed run rather than a wrong request.
- **Generation builds its cache from the config.** A linear-attention
  layer keeps a recurrent state between steps rather than keys and values,
  and `DynamicCache()` with no config has nowhere to put it.
- **nnterp's own refusal is translated.** A `RenamingError` names the
  rename argument a developer would pass. The person who chose the model
  can only choose another, so the status says Lab cannot inspect that
  architecture yet, and the log keeps nnterp's message.

## Consequences

- Qwen3.5 models load. Qwen3.5-2B-Base, which Qwen-Scope publishes SAEs
  for, was read, steered, probed and generated from with those SAEs, and
  the chart and Lab's own default now map them to Qwen3.5-2B and
  Qwen3.5-2B-Base. Qwen3.5-4B is the same architecture with 32 layers.
- The vision tower is loaded and sits unused in memory. For Qwen3.5-2B it
  is about 330M of 2.2B parameters, so the model needs about 15% more
  memory than its language model alone would.
- Other image-text architectures pass the config check. Lab has been
  tested only with Qwen3.5; one whose layers nnterp cannot find is refused
  by name when it loads.
- Lab still reads text prompts only. Accepting an image would be a change
  to the contract and the interface, not to loading.
- The attention output exists on fewer layers than the others. The status
  does not yet say which, so the interface offers it everywhere and Lab
  refuses it where it is missing.

## Alternatives considered

- **Load only the language model, by handing nnsight the text config.**
  `transformers` maps `qwen3_5` to a text-only `Qwen3_5ForCausalLM` too,
  and passing `config_model=config.get_text_config()` builds it from the
  same checkpoint and leaves the vision tower on disk. It works for
  Qwen3.5, but only for families that have such a class: Qwen2.5-VL and
  most others do not. It also means Lab reads a model other than the one
  published, and every multimodal family would rest on `transformers`
  continuing to provide that second class.
- **nnsight's `VisionLanguageModel`.** It adds image inputs Lab does not
  accept, and nnterp's standard names are defined for `LanguageModel`
  only.
- **Renaming `linear_attn` to `self_attn`.** That would make every layer
  look like it has attention. nnterp checks that every attention returns
  the same shape, and the two kinds do not. An edit to the attention output
  on a linear layer would also mean something different from the same edit
  on a softmax one, with nothing to tell the person that.
