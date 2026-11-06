# Images

Primer publishes its images to GitHub Container Registry from `main`:

| Image | Tags | What it runs | Architectures |
| --- | --- | --- | --- |
| `ghcr.io/harville-labs/primer-api` | `latest` | Control, Chat, and Retrieval, chosen by the command | amd64, arm64 |
| `ghcr.io/harville-labs/primer-worker` | `latest` | The ingestion workers | amd64, arm64 |
| `ghcr.io/harville-labs/primer-web` | `latest` | The web app | amd64, arm64 |
| `ghcr.io/harville-labs/primer-lab` | `latest` | Lab on a CPU | amd64, arm64 |
| `ghcr.io/harville-labs/primer-lab` | `latest-cuda` | Lab on an NVIDIA GPU | amd64, arm64 |
| `ghcr.io/harville-labs/primer-lab` | `latest-graphs` | Lab on a GPU, with the graph builder | amd64, arm64 |

Each push to `main` publishes `latest` and `sha-<commit>`, with Lab's
`-cuda` and `-graphs` suffixes where they apply. A release does not build
anything. It gives the `sha-<commit>` images of the commit it tags the
release's version, for example `1.2.0` and `1.2`, so a release ships exactly
the images CI built and scanned from that commit.

Pull requests build no images. The arm64 builds of the worker and Lab take
more runner time than every other check combined, and a pull request could
not publish what it built anyway. The cost is that a change that breaks a
Dockerfile fails on `main` after it merges, rather than in review.

## Architectures

Each architecture is built on a runner of that architecture rather than
emulated. The worker image carries torch and Docling, and building those
under QEMU is slow enough that nobody would keep it.

```bash
docker buildx imagetools inspect ghcr.io/harville-labs/primer-api:latest
```

Lab's CUDA builds are published for arm64 too, for NVIDIA's arm64 machines
such as the DGX Spark and Jetson-class boards running a server distribution.
They use PyTorch's CUDA 13.2 build, which needs a driver that supports CUDA
13. Its kernels are built for sm_120, which the Spark's GB10 (compute
capability 12.1) runs as it is. On a DGX Spark, the arm64 image is pulled
without naming a platform:

```bash
docker run --gpus all -p 8000:8000 -v primer-models:/models \
  ghcr.io/harville-labs/primer-lab:latest-cuda
```

The bundled embedder and reranker run on llama.cpp's `llama-server`, from
`ghcr.io/ggml-org/llama.cpp`. It is third-party, pinned to one build, and
published for amd64 and arm64, so a default deployment has nothing that
needs emulation. Its tag picks the hardware: `server-<build>` for CPU, which
Compose and the chart use, `server-cuda-<build>` for CUDA 12, and
`server-cuda13-<build>` for CUDA 13, which the GB10 needs. On the chart that
is `inference.modelServer.image.tag`;
[Choosing models](models.md#sizing) says what else changes for a GPU.

Each build pushes by digest, and one later job gives all of an image's
architectures their tags together. A tag applied by each build would belong
to whichever architecture finished last, and would serve only that one. The
later job runs only when every build has succeeded, so no tag moves onto an
image that is missing an architecture, or onto the API while the worker is
still a commit behind.

## What is in an image

Every published image carries an SBOM and build provenance. They are
attached to the image rather than published beside it, so you can read them
from the registry without pulling anything:

```bash
# What is installed
docker buildx imagetools inspect ghcr.io/harville-labs/primer-api:latest \
  --format '{{ json .SBOM }}'

# What built it, from what source, with which workflow
docker buildx imagetools inspect ghcr.io/harville-labs/primer-api:latest \
  --format '{{ json .Provenance }}'
```

For one architecture, name it:

```bash
docker buildx imagetools inspect ghcr.io/harville-labs/primer-api:latest \
  --format '{{ json (index .SBOM "linux/arm64") }}'
```

## The vulnerability policy

Trivy scans every image built on `main` before it is tagged. A scan that
fails leaves `latest` where it was. A release can't name that commit either,
because its `sha-<commit>` tag never exists.

**A scan fails on a `CRITICAL` or `HIGH` finding that has a fix available.**
Anything else, meaning `MEDIUM`, `LOW`, and findings with no fix published
yet, shows up in the job's output and does not block.

The threshold is set there because an unfixed CVE in a base image is worth
knowing about, but it is no reason to block a change that has nothing to do
with it. A scan that fails on findings nobody can act on gets ignored. An
ignored scan is worse than none, because it looks like a control.

Fix a failure by updating the dependency or the base image.

### Waivers

A finding that can't be fixed and doesn't apply can be waived in
`.trivyignore`, at the repository root. Each entry needs a comment saying
why it doesn't apply or can't be fixed today, and an `exp:` date after which
Trivy stops honouring the waiver.

A waiver is a change to the repository. It goes through a pull request and
is reviewed like any other change. Never add one just to turn a red build
green. If that is the only reason, fix the finding, or say plainly in the
pull request why the release is going out with it.
