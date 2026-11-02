# 0010. Make images in Studio, through a backend the operator brings

- Status: Accepted
- Date: 2026-11-01

## Context

Primer has been for one thing: answers about a library, citing the passages
they came from. Lab widened that to looking inside a model. Students and
researchers also make images (figures, diagrams, a poster, a slide) and
edit them, and they would like to do it where their libraries already are.

Image generation is widely served now. vLLM-Omni, LocalAI and hosted APIs
serve the OpenAI images API. ComfyUI runs workflows of nodes, and most local
image models reach people through it first. No single API covers both, and
what each server supports varies by version and by model: whether it edits,
whether it takes a mask, which sizes it allows.

A ComfyUI workflow is a graph of nodes, and its custom nodes are arbitrary
Python. Whoever can submit a workflow can run code on the machine that runs
ComfyUI.

## Decision

Primer gains a **Studio** section for generating images from a prompt and
editing them by instruction or by a painted mask. It is off by default
(`studio.enabled`).

- **Studio lives inside Primer.** It shares the proxy's identity, the
  per-user storage quota, the shell, and Control's file storage. A later
  change will let an image become a library document, and that is a link
  between two things Primer already owns.
- **The operator brings the backend**, as for chat models. Primer ships no
  diffusion model and adds no GPU service. Two adapters reach backends: the
  OpenAI images API, and ComfyUI.
- **Capabilities are declared, never inferred.** An operator says what each
  image provider does: generate, edit, inpaint, the sizes it allows, and how
  many images a request may ask for. Primer offers only what is declared and
  refuses anything else before it calls the backend.
- **Users fill templates; they never submit workflows.** An admin registers
  ComfyUI workflow templates, each with a table naming the node inputs that
  take the prompt, seed, size, input image and mask. Primer fills those
  inputs and nothing else.
- **Chat owns image providers; Control owns images.** Chat already holds the
  provider list, the encrypted keys and the page that edits them, so image
  providers join that list rather than starting a second one. Control
  already owns user files and quotas, so it stores the images, with each
  one's provenance: provider, model or template, prompt, seed, size, and the
  image it was edited from.
- **Content policy belongs to the operator.** Primer cannot judge an image,
  and a list of forbidden words is not a control. The operator chooses the
  models and the backend's safety settings. Primer provides the off switch,
  rate and size limits, and provenance for every image it stores.
- **Images are private to their owner,** decided by a predicate in the style
  of `LibraryAccess`, so sharing can be added later. Admins see counts and
  storage use, never other users' images or prompts.

## Alternatives considered

- **A sibling application.** Studio would need its own sign-in through the
  proxy, its own storage and quota, and its own shell, all to be kept in
  step with Primer's. The link from an image to a library would cross two
  applications.
- **A built-in diffusion service,** in the way Lab loads its own model. Lab
  does that because it must read a model's activations; Studio needs only
  outputs. A built-in service would be a second GPU workload for every
  deployment that turns Studio on, and would pin Primer to whichever models
  it shipped.
- **Inferring capabilities from the backend.** Neither API reports reliably
  which operations a model behind it supports. A guess that is wrong shows a
  user a control that fails.
- **Letting users submit ComfyUI workflows.** That is arbitrary code on the
  operator's GPU host, chosen by any user.
- **A prompt filter in Primer.** It would refuse ordinary prompts and pass
  the ones it was meant to stop, and it would let an operator believe a
  shared deployment had a policy when it did not.

## Consequences

- Primer is now for research and for making things. `AGENTS.md`'s product
  direction says so.
- An operator who turns Studio on for a shared deployment needs a content
  policy. The operations guide says this plainly.
- Prompts and images are user data. They are kept until their owner deletes
  them, or an operator-set retention period passes, and are not logged at
  info or above.
- Uploads and backend output are decoded and re-encoded before they are
  stored, which drops EXIF, including GPS location. A backend is the
  operator's choice, but it is still outside Primer.
- Video generation and images in libraries are later work, through the same
  two adapters and the same storage.
