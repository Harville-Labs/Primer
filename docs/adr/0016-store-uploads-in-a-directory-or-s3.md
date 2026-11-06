# 0016. Store uploads in a directory or in S3, and nothing else

- Status: Accepted
- Date: 2026-11-05

## Context

Primer keeps two kinds of data on disk. Uploaded documents and Studio's
images go to the source store, which Control writes and the ingestion
workers read. PostgreSQL, Qdrant and Neo4j each keep their own files.

The source store is opened through fsspec, which accepts any scheme it has
a backend for: `smb://`, `sftp://`, `webdav://`, `gs://` and dozens more.
Primer passed the configured URL straight through. Most of those schemes
were never installed in the images, so they failed on the first upload with
an `ImportError`. The ones that were installed had never been run against
the way Primer writes: to a temporary name, then renamed into place.

Operators want their data on a NAS, a cluster's shared storage, or an object
store, and the databases on a disk with room. In Kubernetes the chart had a
`file://` store with no volume behind it, so each pod wrote to its own
disk. Control saved uploads the workers could not see, and both lost them
on restart. In Compose every location was a named volume, with no way to
choose the disk.

## Decision

**The source store speaks two schemes: `file://` and `s3://`.**

- `file://` is a directory. A NAS, a cluster file system or a cloud disk is
  mounted by the host or a CSI driver and reached as a directory. Primer
  does not speak SMB, NFS or WebDAV itself.
- `s3://` is any S3-compatible object store: AWS, MinIO, Garage, Ceph's
  RADOS Gateway. A non-AWS endpoint is `FSSPEC_S3_ENDPOINT_URL`.
- `primer_storage.backends` holds the allow-list, and every store opens
  its filesystem through it. Control and the ingestion workers check the URL
  when they read their settings, so any other scheme stops the process at
  startup with a message naming the two that work.
- Control's readiness writes, reads back and deletes a small object in the
  store. A listing passes on a read-only mount, and an upload does not.

**The chart refuses a directory with nowhere to put it.** A `file://` store
needs `sourceStore.volume`, a claim the chart makes or one the operator
names. It is mounted in exactly the workloads that get the store's
credentials: Control, the ingestion workers and Studio's retention job. It
defaults to ReadWriteMany, because those pods run on different nodes, and
the claim the chart makes is kept on uninstall.

**The databases go on block storage.** PostgreSQL, Qdrant, Neo4j and Lab
take an `existingClaim` and an `accessMode`. The docs say plainly that their
volumes belong on a local or block disk, not an NFS or SMB share: their
locking and fsync assume one, and a network file system that reorders or
acknowledges writes early corrupts them without an error at the time.

**Compose names a host directory for each.** `PRIMER_SOURCE_DIR`,
`PRIMER_POSTGRES_DIR`, `PRIMER_QDRANT_DIR` and `PRIMER_GRAPH_DIR` bind a
directory in place of the matching named volume when set. The S3 settings
reach Control and the workers only, and stay unset rather than empty when
not given.

## Consequences

- A typo in the store URL is a startup failure, not a lost upload.
- Supporting another protocol means a new backend in the allow-list,
  installed in the images and tested against the rename-into-place writes.
  It is a change to Primer, not a setting.
- A NAS is mounted by whoever runs the host or cluster, with that platform's
  tools. Primer holds no NAS credentials.
- The readiness probe writes to the store on every check. It writes one
  small object under `.primer-probe` and deletes it.
- A cluster without a ReadWriteMany storage class needs an object store or
  a claim the operator makes, for example from an NFS CSI driver.

## Alternatives considered

- **Pass any fsspec URL through.** That is what Primer did. It offered
  dozens of backends and supported two.
- **Speak SMB or NFS from Primer.** That would put share credentials in
  Primer's configuration and a protocol client in every image. Mounting the
  share is something every host and cluster already does well.
- **Let the databases use a shared volume too.** Some network file systems
  work for PostgreSQL in some configurations. Failures appear later, as
  corruption, and the docs cannot tell an operator in advance which
  configuration they have.
