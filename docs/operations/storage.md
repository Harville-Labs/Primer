# Storage

Primer keeps data in two kinds of place, and they want different storage.

| What | Kept by | Belongs on |
| --- | --- | --- |
| Uploaded documents and Studio's images | the source store, written by Control and read by the ingestion workers | a directory (a local disk, a NAS share, a cluster file system) or an S3-compatible object store |
| Accounts, libraries and jobs | PostgreSQL | a local or block disk |
| Passages and their vectors | Qdrant | a local or block disk |
| The knowledge graph | Neo4j | a local or block disk |
| Lab's model weights | Lab | any disk with room |

The source store can be rebuilt from nothing but your users' uploads, and
every database can be rebuilt from the source store by reindexing. Back up
the source store and PostgreSQL first.
[ADR 0016](../adr/0016-store-uploads-in-a-directory-or-s3.md) records why
the store takes only two kinds of URL.

## The source store

`PRIMER_SOURCE_STORE_URL` (`sourceStore.url` on the chart) takes one of two
schemes. Any other stops Control and the workers at startup, with a
message naming these two.

- **`file:///<absolute path>`** is a directory. To keep uploads on a NAS or
  a cluster file system, mount it at that path. Primer does not speak SMB,
  NFS or WebDAV itself, so the share's credentials stay with whoever mounts
  it.
- **`s3://<bucket>[/<prefix>]`** is any S3-compatible object store: AWS,
  MinIO, Garage, Ceph's RADOS Gateway. Credentials are the backend's own
  environment: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
  and `FSSPEC_S3_ENDPOINT_URL` for anything that is not AWS.

Every process that opens the store must see the same one. With `file://`
that means the same directory, mounted in Control, both ingestion workers
and, on the chart, Studio's retention job.

Control's readiness check writes a small object under `.primer-probe`, reads
it back and deletes it. A store that is missing, read-only or full shows as
not ready, rather than failing on someone's first upload.

## The databases

PostgreSQL, Qdrant and Neo4j each need a disk of their own. **Put them on
local or block storage, not on an NFS or SMB share.** Their locking and
`fsync` assume a local disk. A network file system that reorders writes or
acknowledges them early does not fail at the time; the database is
corrupted, and you find out later. A block device presented over the
network, such as iSCSI, a cloud disk, or Ceph RBD, is fine, because to the
database it is a local disk.

If you already run PostgreSQL, Qdrant or Neo4j, point Primer at them
instead and their storage is yours to choose. [Configuration](configuration.md)
lists the URLs.

## Docker Compose

Unset, everything lives in Docker named volumes on the host's Docker disk.
Each of these, in `.env`, binds a host directory instead:

| Variable | Holds |
| --- | --- |
| `PRIMER_SOURCE_DIR` | Uploads. May be a mounted NAS share |
| `PRIMER_POSTGRES_DIR` | PostgreSQL's data directory |
| `PRIMER_QDRANT_DIR` | Qdrant's storage |
| `PRIMER_GRAPH_DIR` | Neo4j's data |

```bash
PRIMER_SOURCE_DIR=/mnt/nas/primer/sources
PRIMER_POSTGRES_DIR=/srv/primer/postgres
PRIMER_QDRANT_DIR=/srv/primer/qdrant
PRIMER_GRAPH_DIR=/srv/primer/graph
```

Create each directory before the first start. A database's directory must
be empty the first time, or hold that same database's files from before.
The source directory is given to the services' user on every start, so a
NAS share mounted there needs to allow that.

To keep uploads in an object store instead, set the URL and the backend's
settings. Compose passes them to Control and the ingestion workers only,
and leaves any you do not set unset rather than empty:

```bash
PRIMER_SOURCE_STORE_URL=s3://primer-sources
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
FSSPEC_S3_ENDPOINT_URL=http://minio.example.internal:9000
```

## Kubernetes

### Uploads in an object store

This is the chart's default, and the simplest choice with more than one
node. Name the bucket, and a Secret holding the backend's settings:

```yaml
sourceStore:
  url: s3://primer-sources
  existingSecret: primer-s3   # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, ...
```

Every key in the Secret is passed through, to Control, the ingestion
workers and Studio's retention job, and to nothing else.

### Uploads in a directory

A `file://` store needs a volume behind it, and the chart refuses one
without:

```yaml
sourceStore:
  url: file:///var/lib/primer/sources
  volume:
    enabled: true
    size: 50Gi
    storageClass: ""          # must offer the access mode below
    accessMode: ReadWriteMany
```

The volume is mounted at the url's path in the same workloads that get the
credentials, and nowhere else. It is ReadWriteMany by default, because
those pods run on different nodes. ReadWriteOnce works only if every one of
them is scheduled onto the same node. The claim the chart makes is kept
when the release is uninstalled, so uninstalling does not delete everyone's
documents.

To use a claim you made, for example a NAS share through an NFS or SMB CSI
driver, or a CephFS volume, name it and the chart makes none:

```yaml
sourceStore:
  url: file:///var/lib/primer/sources
  volume:
    enabled: true
    existingClaim: nas-primer-sources
```

A cluster without a ReadWriteMany storage class needs one of these, or an
object store.

### The databases

Each bundled database takes a size, a storage class, an access mode, and
optionally a claim you made:

```yaml
postgresql:
  bundled:
    persistence:
      size: 20Gi
      storageClass: local-ssd
      accessMode: ReadWriteOnce   # or ReadWriteOncePod
      existingClaim: ""           # a claim you made, such as one restored from a backup

vectorStore:
  qdrant:
    persistence: { size: 20Gi, storageClass: local-ssd }

graph:
  neo4j:
    persistence: { size: 10Gi, storageClass: local-ssd }
```

Lab's model cache takes the same keys under `lab.persistence`.

With `existingClaim` set, the StatefulSet mounts that claim instead of
making one from its template. A StatefulSet cannot adopt an existing claim
through its template, which is why this is a separate setting rather than a
claim given the template's name.
