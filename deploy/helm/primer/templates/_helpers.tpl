{{/*
Shared naming and configuration.

Environment is assembled here rather than repeated per deployment: the four
services read the same settings, and a value that drifted between two of
them would produce a cluster where half the pods talk to a different
database.
*/}}

{{- define "primer.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "primer.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "primer.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "primer.labels" -}}
app.kubernetes.io/name: {{ include "primer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "primer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "primer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "primer.image" -}}
{{- $image := index .root.Values.image .component -}}
{{- printf "%s:%s" $image.repository (default .root.Chart.AppVersion $image.tag) -}}
{{- end -}}

{{/*
Settings every service shares. Secrets are referenced, never inlined: a value
in the manifest is a value in `kubectl get deployment -o yaml`, and anyone
with read access to the namespace has it.

The database is not among them. Retrieval keeps nothing in PostgreSQL, and a
service that is handed credentials it never uses widens what a compromise of
it reaches; the ones that do use it include primer.databaseEnv themselves.
*/}}
{{- define "primer.commonEnv" -}}
- name: PRIMER_AUTH_MODE
  value: {{ .Values.auth.mode | quote }}
- name: PRIMER_SOURCE_STORE_URL
  value: {{ .Values.sourceStore.url | quote }}
- name: PRIMER_INTERNAL_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "primer.fullname" . }}-internal
      key: internal-token
- name: PRIMER_SERVICE_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "primer.fullname" . }}-internal
      key: internal-token
{{- /*
Encrypts the API keys of providers added through the settings page. Given to
every service rather than only the one that uses it, so that moving where
providers are held later does not mean a chart change and a restart.
*/}}
- name: PRIMER_SETTINGS_ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "primer.fullname" . }}-internal
      key: settings-key
{{- if .Values.auth.adminGroup }}
{{- /*
Who may see and change how this deployment is wired. Unset means nobody,
which is the safe reading of an operator who has not made the decision -
the alternative is that every user of a shared deployment can repoint it.
*/}}
- name: PRIMER_ADMIN_GROUP
  value: {{ .Values.auth.adminGroup | quote }}
{{- end }}
{{- include "primer.extraEnv" . }}
{{- end -}}

{{/*
How to reach PostgreSQL. An operator's database comes as one URI from their
Secret. A bundled one is addressed by the release, with the password passed
as PGPASSWORD rather than spliced into the URI: asyncpg and psycopg both
read it, and a password is then free to hold characters a URI would need
escaped.
*/}}
{{- define "primer.databaseEnv" -}}
{{- $secret := required "postgresql.existingSecret is required: name a Secret holding the database URI under postgresql.urlKey, or its password under postgresql.passwordKey with postgresql.bundled.enabled" .Values.postgresql.existingSecret -}}
{{- if .Values.postgresql.bundled.enabled }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.postgresql.passwordKey }}
- name: PRIMER_DATABASE_URL
  value: {{ printf "postgresql://primer@%s:5432/primer" (include "primer.postgresqlHost" .) | quote }}
{{- else }}
- name: PRIMER_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.postgresql.urlKey }}
{{- end }}
{{- end -}}

{{- define "primer.postgresqlHost" -}}
{{- printf "%s-postgresql" (include "primer.fullname" .) -}}
{{- end -}}

{{/*
Credentials for the source store, and anywhere else an operator needs to
reach past this chart.

Object storage is configured through the backend's own environment -
`AWS_ACCESS_KEY_ID`, `FSSPEC_S3_ENDPOINT_URL` and whatever else the provider
wants - and those names belong to the provider rather than to Primer. The
whole Secret is mounted rather than named key by key: enumerating them here
would mean a chart release every time a backend wanted a variable this chart
had not heard of.

Only the workloads that touch the store get it. Retrieval, Chat and the web
app never open a source object, and handing them object-storage credentials
would widen what a compromise of any of them reaches.
*/}}
{{- define "primer.sourceStoreEnvFrom" -}}
{{- if .Values.sourceStore.existingSecret }}
envFrom:
  - secretRef:
      name: {{ .Values.sourceStore.existingSecret }}
{{- end }}
{{- end -}}

{{/*
An escape hatch. Settings this chart does not model, without a fork or a
post-renderer.
*/}}
{{- define "primer.extraEnv" -}}
{{- with .Values.extraEnv }}
{{ toYaml . | trim }}
{{- end }}
{{- end -}}

{{- define "primer.brokerEnv" -}}
- name: PRIMER_BROKER_URL
  valueFrom:
    secretKeyRef:
      name: {{ required "rabbitmq.existingSecret is required" .Values.rabbitmq.existingSecret }}
      key: {{ .Values.rabbitmq.urlKey }}
{{- end -}}

{{- define "primer.embeddingEnv" -}}
{{- /*
Passages scoring below this are not returned at all, even when that leaves
nothing. Emitted only when set, because the right value belongs to whatever
produced the score - cosine from one embedding model is not comparable to
cosine from another, nor to a reranker's output - so a default here would
drop good passages on one deployment and admit everything on the next.
*/}}
{{- if .Values.inference.minScore }}
- name: PRIMER_MIN_SCORE
  value: {{ .Values.inference.minScore | quote }}
{{- end }}
- name: PRIMER_EMBEDDING_BASE_URL
  value: {{ required "inference.embeddings.baseUrl is required" .Values.inference.embeddings.baseUrl | quote }}
- name: PRIMER_EMBEDDING_MODEL
  value: {{ required "inference.embeddings.model is required" .Values.inference.embeddings.model | quote }}
{{- /*
Width and prompts come from the model's row in Primer's table; these are
emitted only to override it, or to describe a model the table does not
know. Retrieval refuses to start on an unknown model with no width.
*/}}
{{- with .Values.inference.embeddings.dimensions }}
- name: PRIMER_EMBEDDING_DIMENSIONS
  value: {{ . | quote }}
{{- end }}
{{- with .Values.inference.embeddings.queryPrefix }}
- name: PRIMER_EMBEDDING_QUERY_PREFIX
  value: {{ . | quote }}
{{- end }}
{{- with .Values.inference.embeddings.documentPrefix }}
- name: PRIMER_EMBEDDING_DOCUMENT_PREFIX
  value: {{ . | quote }}
{{- end }}
{{- if .Values.inference.embeddings.existingSecret }}
- name: PRIMER_EMBEDDING_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.inference.embeddings.existingSecret }}
      key: {{ .Values.inference.embeddings.apiKeyKey }}
{{- end }}
{{- /*
Reranking is optional and off unless an endpoint is named. Emitting nothing
is what keeps a deployment without one behaving exactly as it did before.
*/}}
{{- if .Values.inference.rerank.baseUrl }}
- name: PRIMER_RERANK_BASE_URL
  value: {{ .Values.inference.rerank.baseUrl | quote }}
- name: PRIMER_RERANK_MODEL
  value: {{ required "inference.rerank.model is required when a rerank endpoint is set" .Values.inference.rerank.model | quote }}
- name: PRIMER_RERANK_CANDIDATES
  value: {{ .Values.inference.rerank.candidates | quote }}
{{- if .Values.inference.rerank.existingSecret }}
- name: PRIMER_RERANK_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.inference.rerank.existingSecret }}
      key: {{ .Values.inference.rerank.apiKeyKey }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Where the knowledge graph is, or nothing when it is off. The one place that
decides it: every template that asks whether the graph is on asks this, so
a bundled Neo4j and an operator's one cannot be told apart downstream.

Both at once is refused rather than resolved. Either choice would quietly
leave the other unused, and the one ignored is usually the one meant.
*/}}
{{- define "primer.graphUrl" -}}
{{- if .Values.graph.neo4j.enabled -}}
{{- if .Values.graph.url -}}
{{- fail "graph.url and graph.neo4j.enabled are both set. Set graph.url to use a Neo4j you run, or graph.neo4j.enabled to have this release run one - not both." -}}
{{- end -}}
{{- printf "bolt://%s-neo4j:7687" (include "primer.fullname" .) -}}
{{- else -}}
{{- .Values.graph.url -}}
{{- end -}}
{{- end -}}

{{/*
Where Qdrant is. The one place that decides it, as primer.graphUrl is for the
graph. Unlike the graph there is no "off": search has nothing to search
without a vector store, so having neither is refused, as having both is.
*/}}
{{- define "primer.qdrantUrl" -}}
{{- if .Values.vectorStore.qdrant.enabled -}}
{{- if .Values.vectorStore.url -}}
{{- fail "vectorStore.url and vectorStore.qdrant.enabled are both set. Set vectorStore.url to use a Qdrant you run and turn vectorStore.qdrant.enabled off, or leave vectorStore.url empty to have this release run one." -}}
{{- end -}}
{{- printf "http://%s-qdrant:6333" (include "primer.fullname" .) -}}
{{- else -}}
{{- required "vectorStore.url is required when vectorStore.qdrant.enabled is off: give the http(s):// address of a Qdrant you run, or turn vectorStore.qdrant.enabled back on" .Values.vectorStore.url -}}
{{- end -}}
{{- end -}}

{{/*
The Secret holding the graph's password. Retrieval reads it, and a bundled
Neo4j takes its initial password from it, so the two cannot disagree.
*/}}
{{- define "primer.graphSecret" -}}
{{- required "graph.existingSecret is required when the graph is on: name a Secret holding the Neo4j password under graph.passwordKey" .Values.graph.existingSecret -}}
{{- end -}}

{{/*
How to reach the knowledge graph. Given to Retrieval, which owns it, and to
the step that prepares its schema - nothing else opens a connection to it.

Included only where the graph is on; each caller guards it, so a deployment
without a graph renders exactly as it did before there was one.
*/}}
{{- define "primer.graphEnv" -}}
- name: PRIMER_GRAPH_URL
  value: {{ include "primer.graphUrl" . | quote }}
- name: PRIMER_GRAPH_USERNAME
  value: {{ .Values.graph.username | quote }}
- name: PRIMER_GRAPH_DATABASE
  value: {{ .Values.graph.database | quote }}
- name: PRIMER_GRAPH_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "primer.graphSecret" . }}
      key: {{ .Values.graph.passwordKey }}
{{- end -}}

{{/*
The model that reads a graph out of each passage. Only the index worker gets
it: extraction runs in the embed stage, beside the call that embeds the
same passages.

Refused without a graph rather than rendered anyway. Retrieval with nowhere
to put a graph refuses every one it is sent, so the deployment would install
cleanly and then fail every document it indexed.
*/}}
{{- define "primer.graphExtractionEnv" -}}
{{- with .Values.graph.extraction }}
{{- if .baseUrl }}
{{- if not (include "primer.graphUrl" $) }}
{{- fail "graph.extraction.baseUrl is set but the graph is off, so every extracted graph would be refused and every document would fail to index. Set graph.url or graph.neo4j.enabled, or unset graph.extraction.baseUrl." }}
{{- end }}
- name: PRIMER_GRAPH_EXTRACTION_BASE_URL
  value: {{ .baseUrl | quote }}
- name: PRIMER_GRAPH_EXTRACTION_MODEL
  value: {{ required "graph.extraction.model is required when graph.extraction.baseUrl is set" .model | quote }}
- name: PRIMER_GRAPH_EXTRACTION_CONCURRENCY
  value: {{ .concurrency | quote }}
- name: PRIMER_GRAPH_EXTRACTION_TIMEOUT_SECONDS
  value: {{ .timeoutSeconds | quote }}
{{- if .existingSecret }}
- name: PRIMER_GRAPH_EXTRACTION_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .existingSecret }}
      key: {{ .apiKeyKey }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
A writable home and temp directory. The root filesystem is read-only, and
several libraries create a cache or config directory under $HOME on import
and fail hard without one.
*/}}
{{- define "primer.scratchVolumes" -}}
- name: tmp
  emptyDir:
    sizeLimit: 512Mi
- name: home
  emptyDir:
    sizeLimit: 64Mi
{{- end -}}

{{- define "primer.scratchMounts" -}}
- name: tmp
  mountPath: /tmp
- name: home
  mountPath: /home/primer
{{- end -}}

{{/*
The account one workload runs as.

One per component rather than one for the release, so that what a pod may do
is stated per pod and an audit log can tell them apart. Falls back to the
namespace default when the chart is not creating them, which is what a
cluster that manages its own accounts wants.
*/}}
{{- define "primer.serviceAccountName" -}}
{{- if .root.Values.serviceAccounts.create -}}
{{- printf "%s-%s" (include "primer.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- else -}}
default
{{- end -}}
{{- end -}}

{{/*
What every Primer pod says about its own identity.

`automountServiceAccountToken: false` everywhere, because nothing here calls
the Kubernetes API. A token mounted into a pod that never uses it is a
credential sitting in a filesystem for whoever gets into the container next.
*/}}
{{- define "primer.podIdentity" -}}
serviceAccountName: {{ include "primer.serviceAccountName" . }}
automountServiceAccountToken: false
{{- end -}}

{{/*
Spread one component against its own pods. Keeping the selector component-
scoped means a busy worker does not affect where the web replicas land.
*/}}
{{- define "primer.topologySpread" -}}
{{- if .root.Values.podTopologySpread.enabled }}
topologySpreadConstraints:
  - maxSkew: {{ .root.Values.podTopologySpread.maxSkew }}
    topologyKey: {{ .root.Values.podTopologySpread.topologyKey | quote }}
    whenUnsatisfiable: {{ .root.Values.podTopologySpread.whenUnsatisfiable }}
    labelSelector:
      matchLabels:
        {{- include "primer.selectorLabels" .root | nindent 8 }}
        app.kubernetes.io/component: {{ .component }}
    {{- with .root.Values.podTopologySpread.matchLabelKeys }}
    matchLabelKeys:
      {{- toYaml . | nindent 6 }}
    {{- end }}
{{- end }}
{{- end -}}

{{/*
Autoscaling settings for one component, with the shared defaults underneath.

Per component so that one service can be autoscaled without turning it on
for the rest, and merged rather than replaced so that turning one on does
not mean restating every threshold.
*/}}
{{- define "primer.autoscaling" -}}
{{- $shared := omit .root.Values.autoscaling "control" "chat" "retrieval" "web" -}}
{{- $own := get .root.Values.autoscaling .component | default dict -}}
{{- merge (deepCopy $own) $shared | toYaml -}}
{{- end -}}

{{/*
Whether a component's replica count is the autoscaler's to decide.

Where it is, the Deployment leaves `replicas` unset. Setting both means
every `helm upgrade` writes the configured number back and the autoscaler
undoes it, which reads as a service that scales down for no reason a few
seconds after every release.
*/}}
{{- define "primer.autoscaled" -}}
{{- $scaling := include "primer.autoscaling" . | fromYaml -}}
{{- if $scaling.enabled }}true{{ end -}}
{{- end -}}

{{/*
How many embedded images the parse worker will run OCR over.

Only the PDF pipeline does OCR of its own; every other format Primer
accepts is converted by a pipeline that has none, so a slide deck of pasted
charts is read by this or not at all. Each picture costs a full pass, which
is why there is a ceiling rather than no number.
*/}}
{{- define "primer.pictureEnv" -}}
- name: PRIMER_MAX_PICTURES_PER_DOCUMENT
  value: {{ .Values.ingestion.maxPicturesPerDocument | quote }}
{{- end -}}

{{/*
What the parse worker needs to size chunks for the embedding model.

The worker is told the model, not a tokenizer, and finds the tokenizer
itself from the table in `primer_contracts.embedding_models`. Deriving it
here as well would be a second copy of that table, and the copy in a
template is the one nobody updates: Ollama's `nomic-embed-text` names no
repository, so a template can only guess, while the table knows.

`ingestion.chunkTokenizer` is passed only when it is set, for a hosted
model that publishes no tokenizer of its own. Only the parse worker gets
any of this. It is the only process that chunks, and a tokenizer is a
download.
*/}}
{{- define "primer.chunkingEnv" -}}
- name: PRIMER_EMBEDDING_MODEL
  value: {{ required "inference.embeddings.model is required" .Values.inference.embeddings.model | quote }}
{{- with .Values.ingestion.chunkTokenizer }}
- name: PRIMER_CHUNK_TOKENIZER
  value: {{ . | quote }}
{{- end }}
- name: PRIMER_MAX_CHUNK_TOKENS
  value: {{ .Values.ingestion.maxChunkTokens | quote }}
{{- /*
Joining runs on whatever the chunker produced, and the structural fallback
- what a hosted model without a named tokenizer gets - is the one that
produces the most fragments, so this is never left out.
*/}}
- name: PRIMER_MIN_CHUNK_CHARS
  value: {{ .Values.ingestion.minChunkChars | quote }}
{{- end -}}
