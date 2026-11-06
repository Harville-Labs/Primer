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
Lab's image: the CPU build, the CUDA build with `-cuda`, or the CUDA build
with the graph builder installed beside Lab with `-graphs`. One place,
because Lab and its migrations must run the same image. Graphs are refused
without a GPU: a build is held under Lab's response time (ADR 0009), which
a CPU does not finish a graph inside.
*/}}
{{- define "primer.labImage" -}}
{{- $lab := .Values.lab -}}
{{- $suffix := "" -}}
{{- if $lab.graphs.enabled -}}
{{- if not (gt (int $lab.gpu) 0) -}}
{{- fail "lab.graphs.enabled needs lab.gpu above zero. A graph is built inside Lab's four-minute response limit, which a CPU cannot meet; give Lab a GPU or turn graphs off." -}}
{{- end -}}
{{- $suffix = "-graphs" -}}
{{- else if gt (int $lab.gpu) 0 -}}
{{- $suffix = "-cuda" -}}
{{- end -}}
{{- printf "%s%s" (include "primer.image" (dict "root" . "component" "lab")) $suffix -}}
{{- end -}}

{{/*
Settings every service shares. Credentials are not among them: each
workload is handed only the ones it uses (primer.credentialEnv), because a
credential in a pod is a credential anyone who compromises that pod holds.

The database is not among them either. Retrieval keeps nothing in
PostgreSQL; the services that do use it include primer.databaseEnv
themselves.
*/}}
{{- define "primer.commonEnv" -}}
- name: PRIMER_AUTH_MODE
  value: {{ .Values.auth.mode | quote }}
- name: PRIMER_SOURCE_STORE_URL
  value: {{ .Values.sourceStore.url | quote }}
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
Credentials from the release's internal Secret, as environment variables.
Called with the root context and a map from variable name to Secret key, so
each workload's template lists exactly what it holds:

  internal-token  Chat presents it to Control and Retrieval, which accept it
                  on their authorization and read routes.
  worker-token    The ingestion workers present it to Control's job API and
                  Retrieval's write routes, and nothing else accepts it. A
                  worker that is compromised through a hostile document can
                  then do no more than a worker does.
  web-token       The web server presents it with the identity it forwards,
                  so a service believes an identity header only from it.
  settings-key    Encrypts provider keys; only Chat stores them.
  db-<role>-password
                  A bundled PostgreSQL's login for Control, Chat or Lab;
                  see primer.databaseEnv.
*/}}
{{- define "primer.credentialEnv" -}}
{{- $root := index . 0 }}
{{- range $name, $key := index . 1 }}
- name: {{ $name }}
  valueFrom:
    secretKeyRef:
      name: {{ include "primer.fullname" $root }}-internal
      key: {{ $key }}
{{- end }}
{{- end -}}

{{/*
How to reach PostgreSQL, as one service's runtime role or as the owner.

Called with the root context and a role: "control", "chat" or "lab" for that
service, or "" for the owner the migrations run as. A bundled PostgreSQL
gives each service its own login, `primer_<role>`, which the migrations
create and confine to that service's schema - so a flaw in Chat that ran
arbitrary SQL could read conversations, but not libraries, and nothing at
runtime can alter a table. Its password is in the release's internal Secret.

An operator's database comes as one URI from their Secret, used by every
service: the chart cannot build a URI for a role on a server it does not
know the address of. See docs/operations/configuration.md.

The password is passed as PGPASSWORD rather than spliced into the URI:
asyncpg and psycopg both read it, and a password is then free to hold
characters a URI would need escaped.
*/}}
{{- define "primer.databaseEnv" -}}
{{- $root := index . 0 }}
{{- $role := index . 1 }}
{{- $secret := required "postgresql.existingSecret is required: name a Secret holding the database URI under postgresql.urlKey, or its password under postgresql.passwordKey with postgresql.bundled.enabled" $root.Values.postgresql.existingSecret -}}
{{- if $root.Values.postgresql.bundled.enabled }}
{{- $host := include "primer.postgresqlHost" $root }}
{{- if $role }}
{{- include "primer.credentialEnv" (list $root (dict "PGPASSWORD" (printf "db-%s-password" $role))) }}
- name: PRIMER_DATABASE_URL
  value: {{ printf "postgresql://primer_%s@%s:5432/primer" $role $host | quote }}
{{- else }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ $root.Values.postgresql.passwordKey }}
- name: PRIMER_DATABASE_URL
  value: {{ printf "postgresql://primer@%s:5432/primer" $host | quote }}
{{- end }}
{{- else }}
- name: PRIMER_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ $root.Values.postgresql.urlKey }}
{{- end }}
{{- end -}}

{{/*
What a migration Job needs to make the runtime role its service connects as.
Empty for an operator's database, where every service uses their URI.
*/}}
{{- define "primer.runtimeRoleEnv" -}}
{{- $root := index . 0 }}
{{- $role := index . 1 }}
{{- if $root.Values.postgresql.bundled.enabled }}
- name: PRIMER_DATABASE_RUNTIME_ROLE
  value: {{ printf "primer_%s" $role | quote }}
{{- include "primer.credentialEnv" (list $root (dict "PRIMER_DATABASE_RUNTIME_PASSWORD" (printf "db-%s-password" $role))) }}
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
  value: {{ include "primer.embeddingUrl" . | quote }}
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
- name: PRIMER_EMBEDDING_BATCH_SIZE
  value: {{ .Values.inference.embeddings.capabilities.batchSize | quote }}
{{- if .Values.inference.embeddings.existingSecret }}
- name: PRIMER_EMBEDDING_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.inference.embeddings.existingSecret }}
      key: {{ .Values.inference.embeddings.apiKeyKey }}
{{- end }}
{{- /*
Reranking is optional. Emitting nothing when there is no reranker is what
makes Retrieval return the vector ordering unchanged.
*/}}
{{- with include "primer.rerankUrl" . }}
- name: PRIMER_RERANK_BASE_URL
  value: {{ . | quote }}
- name: PRIMER_RERANK_MODEL
  value: {{ required "inference.rerank.model is required when a reranker is set" $.Values.inference.rerank.model | quote }}
- name: PRIMER_RERANK_CANDIDATES
  value: {{ $.Values.inference.rerank.candidates | quote }}
{{- if $.Values.inference.rerank.existingSecret }}
- name: PRIMER_RERANK_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.inference.rerank.existingSecret }}
      key: {{ $.Values.inference.rerank.apiKeyKey }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Where the embedding and reranking servers are: the one place each is
decided, as primer.qdrantUrl is for the vector store. Both a bundled server
and a URL is refused, because either choice leaves the other running unused.

Embeddings have no "off" - nothing can be indexed or searched without them -
so neither is refused too. Reranking is optional, and neither means none.
*/}}
{{- define "primer.embeddingUrl" -}}
{{- $embeddings := .Values.inference.embeddings -}}
{{- if $embeddings.bundled.enabled -}}
{{- if $embeddings.baseUrl -}}
{{- fail "inference.embeddings.baseUrl and inference.embeddings.bundled.enabled are both set. Set baseUrl to use an embedding server you run and turn bundled.enabled off, or leave baseUrl empty to have this release run one." -}}
{{- end -}}
{{- printf "http://%s-embeddings:8080/v1" (include "primer.fullname" .) -}}
{{- else -}}
{{- required "inference.embeddings.baseUrl is required when inference.embeddings.bundled.enabled is off: give the /v1 address of an OpenAI-compatible embedding server, or turn bundled.enabled back on" $embeddings.baseUrl -}}
{{- end -}}
{{- end -}}

{{- define "primer.rerankUrl" -}}
{{- $rerank := .Values.inference.rerank -}}
{{- if $rerank.bundled.enabled -}}
{{- if $rerank.baseUrl -}}
{{- fail "inference.rerank.baseUrl and inference.rerank.bundled.enabled are both set. Set baseUrl to use a reranker you run and turn bundled.enabled off, or leave baseUrl empty to have this release run one." -}}
{{- end -}}
{{- printf "http://%s-reranker:8080" (include "primer.fullname" .) -}}
{{- else -}}
{{- $rerank.baseUrl -}}
{{- end -}}
{{- end -}}

{{/*
Where the knowledge graph is. The one place that decides it, so a bundled
Neo4j and an operator's one cannot be told apart downstream.

There is no "off": Retrieval does not start without a graph (ADR 0014), so
neither is refused here, where the message can name the setting, rather
than in a crash-looping pod. Both at once is refused rather than resolved.
Either choice would quietly leave the other unused, and the one ignored is
usually the one meant.
*/}}
{{- define "primer.graphUrl" -}}
{{- if .Values.graph.neo4j.enabled -}}
{{- if .Values.graph.url -}}
{{- fail "graph.url and graph.neo4j.enabled are both set. Set graph.url to use a Neo4j you run, or graph.neo4j.enabled to have this release run one - not both." -}}
{{- end -}}
{{- printf "bolt://%s-neo4j:7687" (include "primer.fullname" .) -}}
{{- else -}}
{{- required "graph.neo4j.enabled is off and graph.url is empty. Every release needs a knowledge graph: set graph.url to a Neo4j you run, or turn graph.neo4j.enabled back on." .Values.graph.url -}}
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
Where the graph's password is, as a secretKeyRef. Retrieval reads it, and a
bundled Neo4j takes its initial password from it, so the two cannot
disagree.

A bundled Neo4j with no Secret named uses one this release generated, in its
internal Secret, because a graph on by default must not make every install
write a password first. An operator's Neo4j has a password the chart cannot
know, so it still needs one named.
*/}}
{{- define "primer.graphPasswordRef" -}}
{{- if .Values.graph.existingSecret -}}
name: {{ .Values.graph.existingSecret }}
key: {{ .Values.graph.passwordKey }}
{{- else if .Values.graph.neo4j.enabled -}}
name: {{ include "primer.fullname" . }}-internal
key: graph-password
{{- else -}}
{{- fail "graph.existingSecret is required with graph.url: name a Secret holding the Neo4j password under graph.passwordKey" -}}
{{- end -}}
{{- end -}}

{{/*
How to reach the knowledge graph. Given to Retrieval, which owns it, and to
the step that prepares its schema - nothing else opens a connection to it.
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
      {{- include "primer.graphPasswordRef" . | nindent 6 }}
{{- end -}}

{{/*
The model that reads a graph out of each passage. Only the index worker gets
it: extraction runs in the embed stage, beside the call that embeds the
same passages.
*/}}
{{- define "primer.graphExtractionEnv" -}}
{{- with include "primer.graphExtraction" . | fromYaml }}
- name: PRIMER_GRAPH_EXTRACTION_BASE_URL
  value: {{ .baseUrl | quote }}
- name: PRIMER_GRAPH_EXTRACTION_MODEL
  value: {{ required "graph.extraction.model is required when graph.extraction.baseUrl is set" .model | quote }}
- name: PRIMER_GRAPH_EXTRACTION_CONCURRENCY
  value: {{ $.Values.graph.extraction.concurrency | quote }}
- name: PRIMER_GRAPH_EXTRACTION_TIMEOUT_SECONDS
  value: {{ $.Values.graph.extraction.timeoutSeconds | quote }}
{{- if .existingSecret }}
- name: PRIMER_GRAPH_EXTRACTION_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .existingSecret }}
      key: {{ .apiKeyKey }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Which model extracts the graph, as YAML, or nothing when none does: the one
named in graph.extraction, else the chat model when a chat endpoint and
model are both set. Decided here rather than left to the index worker,
which applies the same rule to Compose's variables, so the install notes
can name the model that writes the graph.

The chat model is the fallback because a graph written by nothing is a
Neo4j that costs memory and adds nothing to search.
It is not the fallback when chat has no model named, because chat then asks
the endpoint which it serves, and the worker cannot.
*/}}
{{- define "primer.graphExtraction" -}}
{{- $extraction := .Values.graph.extraction -}}
{{- $chat := .Values.inference.chat -}}
{{- if $extraction.baseUrl -}}
{{- dict "baseUrl" $extraction.baseUrl "model" $extraction.model "existingSecret" $extraction.existingSecret "apiKeyKey" $extraction.apiKeyKey | toYaml -}}
{{- else if and $chat.baseUrl $chat.model -}}
{{- dict "baseUrl" $chat.baseUrl "model" $chat.model "existingSecret" $chat.existingSecret "apiKeyKey" $chat.apiKeyKey | toYaml -}}
{{- end -}}
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
How many embedded images the parse worker will run OCR over, and describe
with the vision model when there is one.

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
The embedder's declared input limit, for both processes that have to stay
inside it: Retrieval, which embeds questions, and the parse worker, which
refuses to start when its chunks would not fit.
*/}}
{{- define "primer.embeddingInputLimit" -}}
- name: PRIMER_EMBEDDING_MAX_INPUT_TOKENS
  value: {{ .Values.inference.embeddings.capabilities.maxInputTokens | quote }}
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
{{ include "primer.embeddingInputLimit" . }}
{{- /*
Joining runs on whatever the chunker produced, and the structural fallback
- what a hosted model without a named tokenizer gets - is the one that
produces the most fragments, so this is never left out.
*/}}
- name: PRIMER_MIN_CHUNK_CHARS
  value: {{ .Values.ingestion.minChunkChars | quote }}
{{- end -}}

{{/*
The parse worker's vision model: it describes the figures in documents, and
reads Studio images added to a library.

Set whenever a model is, not only with Studio, because figures need no
Studio. Control offers "Add to library" from inside the `studio.enabled`
block of its own template, so it still cannot offer an image the worker has
no model to read.
*/}}
{{- define "primer.visionEnv" -}}
{{- if .Values.inference.vision.baseUrl }}
{{- with .Values.inference.vision }}
- name: PRIMER_VISION_BASE_URL
  value: {{ .baseUrl | quote }}
- name: PRIMER_VISION_MODEL
  value: {{ required "inference.vision.model is required when inference.vision.baseUrl is set" .model | quote }}
- name: PRIMER_VISION_TIMEOUT_SECONDS
  value: {{ .timeoutSeconds | quote }}
- name: PRIMER_VISION_CONCURRENCY
  value: {{ .concurrency | int | quote }}
{{- end }}
- name: PRIMER_DESCRIBE_FIGURES
  value: {{ .Values.ingestion.describeFigures | quote }}
{{- with .Values.inference.vision }}
{{- if .existingSecret }}
- name: PRIMER_VISION_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .existingSecret }}
      key: {{ .apiKeyKey }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chat's PRIMER_TOOLS, as JSON: the operator's servers as written, and the
bundled search server when this release runs one.

The operator's entries are passed through rather than re-described here in
camelCase. Chat validates them at startup against its own model, and a
second description of the same fields in the chart is the one that would
fall behind when Chat gains one.
*/}}
{{- define "primer.toolsConfig" -}}
{{- $tools := .Values.tools }}
{{- $servers := list }}
{{- range $tools.servers }}
{{- if and $tools.search.enabled (eq .name "search") }}
{{- fail "tools.servers names a server 'search', which is the name of the search server this release runs with tools.search.enabled. Rename yours, or turn the bundled one off." }}
{{- end }}
{{- $servers = append $servers . }}
{{- end }}
{{- if $tools.search.enabled }}
{{- $servers = append $servers (dict
  "name" "search"
  "url" (printf "http://%s-search:3000/mcp" (include "primer.fullname" .))
  "allowed_tools" $tools.search.allowedTools
  "required_groups" $tools.search.requiredGroups) }}
{{- end }}
{{- dict "enabled" true "approval_ttl_seconds" $tools.approvalTtlSeconds "servers" $servers | toJson }}
{{- end -}}
