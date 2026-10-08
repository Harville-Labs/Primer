# Images

Three images, published to GitHub Container Registry:

| Image | What it runs |
| --- | --- |
| `ghcr.io/isaiah-harville/primer-api` | Control, Chat, and Retrieval — the service is chosen by the command |
| `ghcr.io/isaiah-harville/primer-worker` | The ingestion workers |
| `ghcr.io/isaiah-harville/primer-web` | The web app |

## Architectures

Every tag serves `linux/amd64` and `linux/arm64`. Apple Silicon and Graviton
run Primer natively; nothing is emulated at runtime.

They are not cross-compiled either. Each architecture is built on a runner
of that architecture, because the worker image carries torch and Docling and
a cross-build that quietly falls back to compiling from source is worse than
not offering the platform at all.

```bash
docker buildx imagetools inspect ghcr.io/isaiah-harville/primer-api:latest
```

Both must be there before a tag exists: the two builds push by digest and a
separate job stitches them into one manifest, so a tag never appears serving
one architecture while the other is still building.

## What is in an image

Every published image carries an SBOM and build provenance, attached to the
image rather than published beside it — so what is inside one is a question
you can answer from the registry without pulling it:

```bash
# What is installed
docker buildx imagetools inspect ghcr.io/isaiah-harville/primer-api:latest \
  --format '{{ json .SBOM }}'

# What built it, from what source, with which workflow
docker buildx imagetools inspect ghcr.io/isaiah-harville/primer-api:latest \
  --format '{{ json .Provenance }}'
```

For one architecture specifically, name it:

```bash
docker buildx imagetools inspect ghcr.io/isaiah-harville/primer-api:latest \
  --format '{{ json (index .SBOM "linux/arm64") }}'
```

## The vulnerability policy

Every push and every pull request builds the three images and scans them
with Trivy. The threshold is written here because a policy nobody agreed to
is a policy that gets waived the first time it fires.

**A build fails on a `CRITICAL` or `HIGH` finding that has a fix
available.** Anything else — `MEDIUM`, `LOW`, and findings with no fix
published yet — is reported in the job's output and does not block.

That line is where it is for one reason: an unfixed CVE in a base image is a
thing to know about, not a reason to block a change that has nothing to do
with it. A scan that fails on findings nobody can act on gets ignored, and
an ignored scan is worse than none, because it looks like a control.

Failures are fixed by updating the dependency or the base image. The scan
runs on a pull request, before the image is published, so this is a thing to
deal with while making the change rather than after it has shipped.

### Waivers

A finding that cannot be fixed and does not apply can be waived in
`.trivyignore`, at the repository root. Every entry needs the identifier,
why it does not apply or cannot be fixed today, and an expiry date after
which it comes back.

A waiver is a change to the repository: it goes through a pull request and
is reviewed by someone with write access, like any other change. It is never
a thing done to make a red build green — if that is the reason, the answer
is to fix the finding or to say plainly, in the pull request, why the
release is going out with it.
