# Tools

A model answering in Chat can call tools - a web search, for instance -
through MCP servers you run. Primer runs no tool itself. It connects to the
servers you name, offers the model the tools you allow, and runs nothing
until the person who asked the question approves the call.

Tools are off until you configure them.

## How a call goes

1. The model asks for a tool while it answers. The answer pauses.
2. The person sees the tool's name and the exact arguments, and approves or
   denies it. A request nobody answers expires after
   `approval_ttl_seconds` (five minutes by default) and does not run.
3. An approved call runs on its MCP server. Its output, cut to 8,000
   characters, is shown to the person and given to the model.
4. The model goes on with its answer. A denied, expired or failed call is
   reported to the model too, so the answer can say it could not look
   something up rather than write as if it had.

One answer may make up to four rounds of calls. After that the model is
offered no tools and has to answer with what it has.

Every request is recorded, including the ones that were denied or expired,
with who decided and when.

## Configuring

`PRIMER_TOOLS` is JSON:

```json
{
  "enabled": true,
  "approval_ttl_seconds": 300,
  "servers": [
    {
      "name": "search",
      "url": "http://search-mcp:8080/mcp",
      "transport": "streamable-http",
      "allowed_tools": ["searxng_web_search", "web_url_read"],
      "required_groups": []
    }
  ]
}
```

- `allowed_tools` is an allowlist. A tool the server gains in a later
  release is not offered until you add it here.
- `required_groups` restricts a server to people in one of those groups.
  Empty means everyone who can sign in.
- `transport` is `streamable-http` (the default) or `sse`, for servers that
  still speak the older transport.
- A server whose tools run code - a shell, a Python interpreter - must also
  declare a complete `sandbox`, or Chat refuses to start. See the
  `SandboxDeclaration` fields in `primer_chat.tool_config`.

A server that cannot be reached when a question is asked is skipped for
that question: its tools are not offered, and the model is not told it can
use them. Chat tries it again on the next question.

## The model has to support it

The model's server must accept tool definitions and return tool calls.
For vLLM that means `--enable-auto-tool-choice` and a `--tool-call-parser`;
for llama.cpp, `--jinja`. See [Connecting model providers](providers.md).
A model that cannot call tools never asks, and its answers are unaffected.
