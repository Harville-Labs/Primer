# Studio backends

Declarations for an image server Primer calls. Primer runs no image model
itself: start the server you want, then point Chat at it.

| File | For |
| --- | --- |
| `openai-capabilities.json` | A hosted images API, or a local server speaking the same API (LocalAI, vLLM-Omni) |
| `comfyui-capabilities.json` | ComfyUI, with two SDXL templates: one generates, one restyles an upload |

Change the model names, sizes and checkpoint to what your server has. A
declaration that names something the server lacks fails when someone uses
it, not before.

[docs/operations/studio.md](../../docs/operations/studio.md) covers the
fields, the ComfyUI bindings, and how to set these in Compose or the chart.
