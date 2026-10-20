# Primer

Primer answers questions about your own documents. You upload sources into
private libraries, and it answers from them alone, with citations back to the
passages it used.

It is built for people who cannot or will not send their documents to a
hosted service: everything runs on your infrastructure, against your own
model endpoints.

## What it does

- **Private libraries.** A library belongs to one person. Nobody else can
  read it, list it, or discover that it exists.
- **Real documents.** PDF, Word, PowerPoint, Markdown, and plain text,
  including text that exists only inside images.
- **Answers with citations.** Every claim points at a passage in a specific
  version of a specific document, so you can check it.
- **Your models.** Any OpenAI-compatible endpoint, hosted or local.
- **Connections across documents.** Optionally, a knowledge graph of what
  your sources name, so a question also finds passages linked to what it
  mentions rather than only ones that resemble it.

## How it fits together

<div class="primer-diagram" markdown>
![Primer system architecture](assets/diagrams/architecture.svg#only-light)
![Primer system architecture](assets/diagrams/architecture-dark.svg#only-dark)
</div>

Four processes, each owning one thing. The **Control API** owns what exists
and who may see it. **Ingestion workers** turn uploaded files into passages, and optionally into
a knowledge graph. **Retrieval** is the only process that touches the vector
store or the graph. The **web app** is what you use.

## Where to start

- New to Primer? [Getting started](guide/getting-started.md).
- Running it for a team? [Configuration](operations/configuration.md) and
  [Deploying](operations/deploying.md).
- Want to know how it works? [Architecture](architecture/index.md).

!!! note "This documentation describes what is built"
    Primer is under active development. Pages document behaviour that exists
    and is tested, and say so plainly where something is not built yet.
