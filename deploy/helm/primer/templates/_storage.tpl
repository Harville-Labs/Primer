{{/*
Volumes: what every claim this chart makes looks like, and the one shared
volume, the source store's.

docs/operations/storage.md is the operator's side of this, and
docs/adr/0016 says why Primer speaks only `file://` and `s3://`.
*/}}

{{/*
The spec of a claim, from a `persistence` block: its access mode, class and
size. One definition for the databases, Lab and the source store, so a
field added to one is added to all.
*/}}
{{- define "primer.claimSpec" -}}
accessModes: [{{ .accessMode | default "ReadWriteOnce" | quote }}]
{{- with .storageClass }}
storageClassName: {{ . | quote }}
{{- end }}
resources:
  requests:
    storage: {{ .size }}
{{- end -}}

{{/*
A StatefulSet's claim template, or nothing when the operator brought a claim.

A StatefulSet cannot be told to use an existing claim through its template:
the template names claims it creates, `<template>-<pod>`. So an existing one
replaces the template with an ordinary pod volume of the same name - see
`primer.existingClaimVolume` - and the container's mount does not change.
That also means one replica, which every StatefulSet here already is.

Takes (dict "root" $ "component" "postgresql" "persistence" $p).
*/}}
{{- define "primer.claimTemplate" -}}
{{- if not .persistence.existingClaim }}
volumeClaimTemplates:
  - metadata:
      name: data
      labels:
        {{- include "primer.selectorLabels" .root | nindent 8 }}
        app.kubernetes.io/component: {{ .component }}
    spec:
      {{- include "primer.claimSpec" .persistence | nindent 6 }}
{{- end }}
{{- end -}}

{{- define "primer.existingClaimVolume" -}}
{{- with .existingClaim }}
- name: data
  persistentVolumeClaim:
    claimName: {{ . | quote }}
{{- end }}
{{- end -}}

{{/*
Whether uploads live on a volume rather than in an object store.

A `file://` store with no volume is refused rather than rendered. Each pod
would write to its own temporary disk: Control saves an upload the parse
worker cannot see, and both lose it when they restart. That fails nothing
at install time and loses documents afterwards.
*/}}
{{- define "primer.sourceStoreOnVolume" -}}
{{- $store := .Values.sourceStore }}
{{- if hasPrefix "file://" $store.url }}
{{- if not $store.volume.enabled }}
{{- fail (printf "sourceStore.url is %s, a directory, so sourceStore.volume must be enabled: each pod would otherwise write to its own disk, and uploads would be lost between Control and the workers. Set sourceStore.volume.enabled (with existingClaim for a claim you made, such as a NAS share), or use an s3:// url." $store.url) }}
{{- end }}
{{- if not (hasPrefix "/" (trimPrefix "file://" $store.url)) }}
{{- fail (printf "sourceStore.url must be an absolute path, file:///like/this, not %s." $store.url) }}
{{- end }}
true
{{- else if $store.volume.enabled }}
{{- fail (printf "sourceStore.volume is enabled, but sourceStore.url is %s, which does not use it. Set a file:// url for the volume, or turn the volume off." $store.url) }}
{{- end }}
{{- end -}}

{{- define "primer.sourceStoreClaim" -}}
{{- .Values.sourceStore.volume.existingClaim | default (printf "%s-sources" (include "primer.fullname" .)) }}
{{- end -}}

{{/*
The volume and its mount, for exactly the workloads that open the store:
Control, the ingestion workers, and Studio's retention job. The same set
that gets `primer.sourceStoreEnvFrom`, for the same reason.
*/}}
{{- define "primer.sourceStoreVolumes" -}}
{{- if include "primer.sourceStoreOnVolume" . }}
- name: sources
  persistentVolumeClaim:
    claimName: {{ include "primer.sourceStoreClaim" . | quote }}
{{- end }}
{{- end -}}

{{- define "primer.sourceStoreMounts" -}}
{{- if include "primer.sourceStoreOnVolume" . }}
- name: sources
  mountPath: {{ trimPrefix "file://" .Values.sourceStore.url | quote }}
{{- end }}
{{- end -}}
