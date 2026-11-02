# 0012. Offer a bundled web search MCP server, off by default

- Status: Accepted
- Date: 2026-11-02

## Context

Chat can call tools through MCP servers the operator runs, with each call
approved by the person who asked. Web search is the tool most people want
first, and every operator who wants it has to find a server, an engine
for it to query, and a way to run both. The chart already runs Qdrant and
Neo4j for this reason: a feature that needs a part nobody provides is a
feature nobody uses.

Search is different from those stores in one way. Turning it on sends the
model's queries, written from people's questions, to public search engines.
That is a privacy decision. Primer is private by default, so the chart
can't make that decision for the operator.

## Decision

The chart can run a search server, `tools.search.enabled`, off by default.

- **mcp-searxng in front of SearXNG**, in one pod. SearXNG is a
  metasearch engine that keeps no profile of who searched, and it needs no
  API key. mcp-searxng is a small MCP server over it with streamable HTTP
  transport. Both are pinned by tag.
- **SearXNG listens on localhost.** Only the MCP server beside it talks
  to it, so it gets no Service and no network policy rule.
- **Only Chat can reach the MCP port.** The server has no authentication
  we can use. Chat's tool configuration has no field for a bearer token,
  and a NetworkPolicy does the same job inside the cluster without one.
- **`web_url_read` refuses private addresses.** It fetches the URL the
  model names. `MCP_HTTP_ALLOW_PRIVATE_URLS` is set to `false` in the
  manifest, not left to the default, so the model can't make a pod read a
  cluster address.
- **It still goes through approval.** The bundled server is just another
  entry in `PRIMER_TOOLS`. Each call is shown to the person who asked and
  runs only if they approve it.

## Consequences

- An operator turns on search with two values and no other configuration.
- The chart now pins two more images to track and upgrade.
- The policy only works if the cluster's CNI enforces NetworkPolicy. That
  was already true of every policy in the chart.
- Search reaches the internet through the cluster's egress. The chart's
  policies are ingress-only, as `networkpolicy.yaml` explains, so egress
  stays the operator's to restrict.

## Alternatives considered

- **On by default, like Qdrant and Neo4j.** Rejected, for the reason
  in the context above: it sends questions outside the deployment.
- **A hosted search API** (Brave, Tavily). These need an account and a
  key, and they send queries to one company that keeps them. SearXNG
  spreads queries across engines and keeps none.
- **SearXNG in its own pod.** That needs a Service, a policy, and an
  address other pods can find, all for a component with one client.
