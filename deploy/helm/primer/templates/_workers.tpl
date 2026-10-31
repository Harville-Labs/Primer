{{- /*
Helpers for the ingestion workers, kept apart from _helpers.tpl, which
holds the rest of the chart's.
*/ -}}

{{- /*
The most parse pods that can exist at once: what the model cache has to be
attachable by. Under queue scaling that is the ceiling, not the count, since
a cache only one pod can mount fails the second one KEDA starts.
*/ -}}
{{- define "primer.parseReplicasAtMost" -}}
{{- $parse := .Values.workers.parse -}}
{{- ternary $parse.queueScaling.maxReplicas $parse.replicas $parse.queueScaling.enabled | int -}}
{{- end -}}

{{- /*
The queues a worker consumes. Named once, because the worker's -Q and the
queues KEDA watches to scale it must be the same list.
*/ -}}
{{- define "primer.workerQueues" -}}
{{- if eq . "parse" -}}
ingestion.parse
{{- else -}}
ingestion.embed,ingestion.index,ingestion.delete
{{- end -}}
{{- end -}}

{{/*
How the parse worker's model cache may be attached.

Derived from the replica count rather than defaulted to a mode of its own,
because it is not a preference: every parse replica mounts this one claim,
so what the volume must support is decided entirely by how many replicas
there are. A standalone default would be a second thing to keep in step
with `workers.parse.replicas`, and the two disagreeing is precisely the
failure this derivation removes - a scaled deployment whose extra pods can
never attach anything and sit Pending with no explanation.

One replica gets ReadWriteOnce. Not merely because every storage class has
it, though that matters for a chart that has to install on EBS, GCE PD,
Azure Disk and the local-path provisioner that k3s and kind default to -
but because ReadWriteMany is worse at that count. Longhorn and its like
serve RWX through an NFS share-manager pod, which is a second workload and
a network hop for a volume exactly one pod ever opens.

More than one gets ReadWriteMany, which is the only mode that lets them
coexist. That needs a storage class providing it; a cluster without one
sees the claim stay Pending, which is a plain answer to having asked for a
topology the storage cannot serve. It is on the scaling path rather than
the install path, which is where a question like that belongs.

Set `modelCache.accessMode` explicitly to override either.
*/}}
{{- define "primer.modelCacheAccessMode" -}}
{{- if .Values.workers.parse.modelCache.accessMode -}}
{{- .Values.workers.parse.modelCache.accessMode -}}
{{- else if gt (include "primer.parseReplicasAtMost" . | int) 1 -}}
ReadWriteMany
{{- else -}}
ReadWriteOnce
{{- end -}}
{{- end -}}
