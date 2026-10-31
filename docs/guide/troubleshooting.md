# When something goes wrong

## A document is stuck in `queued`

Nothing is processing it. Either no ingestion worker is running, or the
message broker is not configured.

This is a deployment state, not a lost document: the job exists and will run
when a worker appears. See [Deploying](../operations/deploying.md).

## A document says `unsupported`

Primer could read the file but found nothing to index. The detail says
which:

- `ocr_required` — no text layer, and image reading is switched off.
- `no_text_found` — images were read as well, and there was still nothing.
- `unsupported_media_type` — the format is not one Primer parses.

## An upload was rejected

| Code | Meaning |
| --- | --- |
| `unsupported_content` | The extension and the contents disagree, or the format is not supported |
| `quota_exceeded` | Larger than the configured upload limit |
| `validation_failed` | Missing or unusable filename |
| `not_found` | The library does not exist, or is not yours |

## A question was not answered

| Code | Meaning |
| --- | --- |
| `search_unavailable` | Retrieval, or a store it reads, could not be reached. Asking again later may work |
| `search_refused` | The library is too large to ask across. Ask a focused question, or ask an operator to raise `PRIMER_GRAPH_COMMUNITY_MAX_MENTIONS` |

## A document says `failed`

A stage failed after exhausting its retries. The detail carries a short,
safe explanation; the full reason, including any stack trace, is in the
worker logs, correlated by job id.

Common causes are an embedding endpoint that is unreachable and a document
that takes longer to convert than the configured budget.

## Everything returns 404

If every library and document reports "not found", the request is arriving
as a different user than you expect. Primer derives your identity from
headers set by the authenticating proxy; if the proxy is misconfigured, or
the app is reached directly rather than through it, requests arrive as
somebody else — or as nobody.

!!! danger "Reaching Primer directly bypasses authentication"
    Primer trusts its identity headers. It must not be reachable except
    through the proxy, or anyone can set those headers themselves. See
    [Identity and access](../architecture/identity.md).
