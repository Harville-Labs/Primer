# Studio

Studio makes and edits images. Primer does not run an image model for it.
It calls one you already run, or a hosted API, the same way it calls a chat
model: a URL, perhaps a key, and a declaration of what the endpoint serves.
[ADR 0010](../adr/0010-studio-image-generation.md) records why.

It is off unless you turn it on.

## Before you turn it on

A shared deployment needs a policy for what people may make. Primer does not
filter prompts or images. Whatever your image backend refuses is refused;
everything else is made and kept in that person's gallery. Decide what is
acceptable, tell your users, and choose a backend whose own filtering fits.

Images are private to the person who made or uploaded them. Administrators
see how many images each person has and how much space they take, and
nothing else.

## Choosing a backend

Primer speaks two APIs to an image server.

| API | Servers | What a user picks |
| --- | --- | --- |
| `openai` | Hosted images APIs, LocalAI, vLLM-Omni, and anything else serving `/v1/images/generations` and `/v1/images/edits` | A model the server serves |
| `comfyui` | ComfyUI | A template: a workflow you exported, with the inputs a user may fill |

Run the server wherever suits your hardware: on the same machine, on a GPU
host, or not at all if you use a hosted API. Primer needs only to reach it.

## Declaring what it serves

Primer never asks a server what it can do. Neither API reports reliably
which operations a model supports or which sizes it accepts, so you declare
them, and Primer offers exactly that. A request for anything else is refused
before the server is called.

For an `openai` server the declaration names models, operations and sizes:

```json
{
  "models": ["gpt-image-1"],
  "operations": ["generate", "edit", "inpaint"],
  "sizes": ["1024x1024", "1536x1024", "1024x1536"],
  "max_images": 4
}
```

- `generate` makes an image from a prompt.
- `edit` changes a whole image by instruction.
- `inpaint` changes only the part a painted mask marks. Many servers that
  edit do not take a mask, so it is a separate operation.
- `max_images` is how many one request may ask for. It defaults to 1.
- Set `ask_for_base64` to `true` for DALL-E models and LocalAI, which return
  links unless asked for the image itself.

[`examples/studio/openai-capabilities.json`](https://github.com/Harville-Labs/Primer/blob/main/examples/studio/openai-capabilities.json)
is this example as a file.

## ComfyUI templates

Users never send ComfyUI a workflow. ComfyUI's custom nodes are arbitrary
Python, so a workflow from a user would be arbitrary code on your GPU host.
Instead you register templates. A user chooses one and fills the inputs you
bound, and nothing else in the workflow changes.

To make one:

1. Build the workflow in ComfyUI and check that it runs.
2. Export it with **Export (API)**. The file the editor saves is a different
   format, and Primer refuses it.
3. Name the node and input each value goes into.

| Binding | Fills | Needed for |
| --- | --- | --- |
| `prompt` | The prompt | Every template |
| `negative_prompt` | What to avoid | Optional |
| `seed` | The seed. Primer picks one when the user does not, and records it | Optional. Without it every run makes the same image |
| `steps` | Sampling steps | Optional |
| `width`, `height` | The size the user chose | Optional. Without them the template makes its own size, so declare only that size |
| `image` | The image to change, uploaded to ComfyUI first | `edit` and `inpaint` |
| `mask` | The painted mask | `inpaint` |
| `output` | The node whose images are the result, given as a node id | Every template |

A binding names a node and one of its inputs:

```json
"bindings": {
  "prompt": { "node": "6", "input": "text" },
  "seed": { "node": "3", "input": "seed" },
  "output": "9"
}
```

Primer checks every binding when the configuration is loaded, so a template
naming a node the workflow does not have stops Chat starting. That is better
than finding out when someone runs it.

[`examples/studio/comfyui-capabilities.json`](https://github.com/Harville-Labs/Primer/blob/main/examples/studio/comfyui-capabilities.json)
declares two templates for an SDXL checkpoint: one that generates and one
that restyles an uploaded image. Change `ckpt_name` to a checkpoint your
ComfyUI has. A ComfyUI provider makes one image per request.

## Configuring it

The declaration goes to Chat, which calls the image server. Studio itself
(limits, storage and retention) is configured in Control. Both are listed in
[Configuration](configuration.md).

An image endpoint can also be added by an administrator on the settings
page, alongside chat providers. One configured below is the deployment's own
and changes only by redeploying.

### Compose

In `deploy/compose/.env`:

```bash
PRIMER_STUDIO_ENABLED=true
PRIMER_IMAGES_BASE_URL=http://host.docker.internal:8188
PRIMER_IMAGES_CAPABILITIES='{"api": "comfyui", "sizes": ["1024x1024"], "templates": [...]}'
```

`host.docker.internal` is the machine Compose runs on. To use the example
file without pasting it, put it on one line:

```bash
echo "PRIMER_IMAGES_CAPABILITIES='$(jq -c . examples/studio/comfyui-capabilities.json)'" \
  >> deploy/compose/.env
```

For a hosted API, use its base URL and set `PRIMER_IMAGES_API_KEY`. If your
chat endpoint also makes images, leave `PRIMER_IMAGES_BASE_URL` unset and only
declare the capabilities; Chat then uses the chat endpoint and its key.

### Kubernetes

```yaml
studio:
  enabled: true
inference:
  images:
    baseUrl: http://comfyui.gpu.svc:8188
    capabilities:
      api: comfyui
      sizes: ["1024x1024"]
      templates: []   # yours, as in the example file
```

A key comes from a Secret you create, never from values:

```yaml
inference:
  images:
    baseUrl: https://api.openai.com/v1
    existingSecret: primer-images
    apiKeyKey: images-api-key
```

The chart refuses a `baseUrl` with no `capabilities`, which Chat would refuse
at startup anyway.

## Limits

Limits apply per person.

- **Generations per hour.** Failed ones count, because the server still did
  the work.
- **Storage.** Uploads and results together. Checked before the server is
  asked and again before results are stored.
- **Uploads.** Size and pixels on a side, checked before an image is decoded.
  Every upload is re-encoded as PNG, which drops its EXIF, location
  included.

## Retention

By default images are kept until their owner deletes them. Set a number of
days and anything older is deleted by a sweep you run on a schedule:

```bash
python -m primer_control.studio.retention
```

The chart runs it as a CronJob when `studio.retentionDays` is set, at
`studio.retentionSchedule`. Compose runs nothing on a schedule, so add it to
the host's cron:

```bash
docker compose -f deploy/compose/compose.yaml run --rm control \
  python -m primer_control.studio.retention
```
